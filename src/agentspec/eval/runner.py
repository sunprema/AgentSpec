"""Run an eval file's cases against a model adapter and check the results.

The adapter is the ONLY model-facing boundary: any callable taking the
rendered prompt and returning the model's reply. Everything else — schema
validation, expectation checking, reporting — is deterministic."""

import json
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agentspec.eval.model import (
    EXPECT_OPERATORS,
    CaseResult,
    EvalCase,
    EvalFile,
    EvalReport,
)
from agentspec.eval.schema import build_output_model
from agentspec.parser import SpecModule, TaskDef, parse_file

Adapter = Callable[[str], str]


class EvalError(Exception):
    """The eval file, spec, or adapter is unusable (not a failing case)."""


def run_eval(eval_path: str | Path, adapter: Adapter) -> EvalReport:
    path = Path(eval_path)
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise EvalError(f"{path}: invalid TOML: {exc}") from exc
    try:
        eval_file = EvalFile.model_validate(data)
    except ValidationError as exc:
        raise EvalError(f"{path}: invalid eval file: {exc}") from exc

    module = parse_file((path.parent / eval_file.spec).resolve())
    if module.errors:
        raise EvalError(f"{eval_file.spec} has parse errors; run `aspec lint` and fix them first")
    task_name = eval_file.task or module.root_task
    task = module.tasks.get(task_name or "")
    if task is None:
        raise EvalError(f"{path}: task '{eval_file.task}' not found in {eval_file.spec}")
    returns = task.returns
    if returns is None or returns.kind != "name" or returns.name not in module.schemas:
        raise EvalError(f"task '{task.name}' has no named returns schema to validate against")
    _check_case_inputs(path, task, eval_file.case)

    output_model = build_output_model(module, task.returns.name)
    results = [_run_case(module, task, output_model, case, adapter) for case in eval_file.case]
    return EvalReport(file=str(path), task=task.name, results=results)


def _check_case_inputs(path: Path, task: TaskDef, cases: list[EvalCase]) -> None:
    input_names = {i.name for i in task.inputs}
    required = {i.name for i in task.inputs if not i.type.optional}
    for case in cases:
        unknown = set(case.inputs) - input_names
        if unknown:
            raise EvalError(
                f"{path}: case '{case.name}' sets unknown input(s) "
                f"{', '.join(sorted(unknown))} of task '{task.name}'"
            )
        missing = required - set(case.inputs)
        if missing:
            raise EvalError(
                f"{path}: case '{case.name}' does not set required input(s) "
                f"{', '.join(sorted(missing))} of task '{task.name}'"
            )


def _run_case(module, task, output_model, case: EvalCase, adapter: Adapter) -> CaseResult:
    prompt = render_prompt(module, task, case.inputs, output_model)
    try:
        raw = adapter(prompt)
    except Exception as exc:  # adapter failures are case failures, not crashes
        return CaseResult(name=case.name, passed=False, failures=[f"adapter: {exc}"])
    try:
        payload = extract_json(raw)
    except EvalError as exc:
        return CaseResult(name=case.name, passed=False, failures=[str(exc)])
    try:
        validated = output_model.model_validate(payload)
    except ValidationError as exc:
        failures = [
            f"schema: {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        ]
        return CaseResult(name=case.name, passed=False, failures=failures)
    output = validated.model_dump()
    failures = check_expectations(case.expect, output)
    return CaseResult(name=case.name, passed=not failures, failures=failures, output=output)


def render_prompt(
    module: SpecModule,
    task: TaskDef,
    inputs: dict[str, Any],
    output_model,
    *,
    extra_rules: list | None = None,
) -> str:
    lines = [
        "You are the runtime for one task of an AgentSpec routine "
        "(see AgentSpec: the spec is data, the model is the runtime).",
        "Execute the task exactly as specified. Never ask questions.",
        "",
        f"# Task: {task.name}",
        task.docstring or "",
    ]
    # A parent task's constraints bind every step inside it (spec §5); shared
    # rule lists appear in both, so dedupe by rule id.
    rules, seen = [], set()
    for rule in list(extra_rules or []) + list(task.constraints):
        if rule.id not in seen:
            seen.add(rule.id)
            rules.append(rule)
    if rules:
        lines += ["", "# Rules"]
        for rule in rules:
            why = f" — why: {rule.why}" if rule.why else ""
            lines.append(f"- ({rule.severity}) [{rule.id}] {rule.text}{why}")
    if task.on_uncertain is not None:
        lines += [
            "",
            "# If you genuinely cannot decide, return exactly:",
            json.dumps(task.on_uncertain, indent=2),
        ]
    lines += [
        "",
        "# Inputs",
        json.dumps(inputs, indent=2),
        "",
        "# Output contract (JSON Schema)",
        json.dumps(output_model.model_json_schema(), indent=2),
        "",
        "Reply with a single JSON object matching the contract. "
        "Real values only — never invent, coerce, or pad values to satisfy the shape.",
    ]
    return "\n".join(lines)


def extract_json(text: str) -> Any:
    """The final JSON object in the reply is the result (agent contract §9)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise EvalError("no JSON object found in the model reply")


def check_expectations(expect: dict[str, Any], output: dict[str, Any]) -> list[str]:
    failures = []
    for key, expectation in expect.items():
        actual, present = _lookup(output, key)
        if not present:
            failures.append(f"{key}: not present in output")
            continue
        if isinstance(expectation, dict) and expectation and set(expectation) <= EXPECT_OPERATORS:
            for op, arg in expectation.items():
                message = _check_op(key, actual, op, arg)
                if message is not None:
                    failures.append(message)
        elif actual != expectation:
            failures.append(f"{key}: expected {expectation!r}, got {actual!r}")
    return failures


def _lookup(output: dict[str, Any], dotted: str) -> tuple[Any, bool]:
    current: Any = output
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


def _check_op(key: str, actual: Any, op: str, arg: Any) -> str | None:
    try:
        if op == "eq":
            ok = actual == arg
        elif op == "in":
            ok = actual in arg
        elif op == "not_in":
            ok = actual not in arg
        elif op == "gte":
            ok = actual >= arg
        elif op == "lte":
            ok = actual <= arg
        else:  # contains
            ok = arg in actual
    except TypeError as exc:
        return f"{key}: cannot apply {op} {arg!r} to {actual!r} ({exc})"
    if ok:
        return None
    return f"{key}: expected {op} {arg!r}, got {actual!r}"


def render_report(report: EvalReport) -> str:
    lines = [f"{report.task} — {report.file}"]
    for result in report.results:
        lines.append(f"  {'PASS' if result.passed else 'FAIL'} {result.name}")
        lines.extend(f"       {failure}" for failure in result.failures)
    passed = sum(1 for r in report.results if r.passed)
    lines.append(
        f"{len(report.results)} case(s): {passed} passed, {len(report.results) - passed} failed"
    )
    return "\n".join(lines)
