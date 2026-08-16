"""Orchestrated execution: the harness derives the schedule from the binds
(the same derivation as `aspec plan`), resolves the data flow mechanically,
and runs one guarded subagent per step — each receiving only its own pruned
contract plus the orchestrator's inherited constraints. The orchestrator is
the reducer: a final guarded call synthesizes the root `returns` from the
collected results. On abort, completed steps unwind via their declared
`undo` in reverse order."""

import getpass
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentspec.eval import Adapter, build_output_model, render_prompt
from agentspec.parser import PipelineBind, TaskDef
from agentspec.run.guard import Ask, RunError, guarded_call
from agentspec.run.model import RuleConflict, RunResult, StepRecord, Substitution
from agentspec.run.policy import resolve_with_policy
from agentspec.run.single import has_declared_doubt, load_routine, place_freeform

_SKIP = object()


def orchestrate(
    spec_path: str | Path,
    adapter: Adapter,
    *,
    context: str = "",
    inputs: dict[str, Any] | None = None,
    task_name: str | None = None,
    max_repairs: int = 2,
    ask: Ask | None = None,
) -> RunResult:
    module, task = load_routine(spec_path, task_name)
    if not task.is_orchestrator:
        raise RunError(f"task '{task.name}' has no pipeline; use `aspec run` without --orchestrate")
    root_inputs = place_freeform(task, dict(inputs or {}), context)
    env = _env()
    results: dict[str, Any] = {}
    records: list[StepRecord] = []
    notes: list[str] = []
    completed: list[tuple[str, TaskDef, Any]] = []  # for reverse-order unwind
    counter = {"calls": 0}
    clarifications: list = []
    substitutions: list[Substitution] = []
    rule_conflicts: list[RuleConflict] = []

    def collect_envelope(outcome, task_name: str) -> None:
        for record in outcome.substitutions + outcome.rule_conflicts:
            record.task = record.task or task_name
        substitutions.extend(outcome.substitutions)
        rule_conflicts.extend(outcome.rule_conflicts)
        if outcome.envelope_warning:
            notes.append(f"{task_name}: {outcome.envelope_warning}")

    def resolve(parts: list[str], loop_var: str | None = None, loop_item: Any = None) -> Any:
        root = parts[0]
        if loop_var is not None and root == loop_var:
            current: Any = loop_item
        elif root in results:
            current = results[root]
        elif root in root_inputs:
            current = root_inputs[root]
        elif root == "env":
            current = env
        else:
            raise RunError(f"unresolvable reference '{'.'.join(parts)}'")
        for part in parts[1:]:
            if current is _SKIP:
                return _SKIP
            if isinstance(current, list):  # a fan-out's collected results
                current = [item[part] for item in current]
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise RunError(f"cannot resolve '.{part}' in '{'.'.join(parts)}'")
        return current

    def guarded_step(target: TaskDef, kwargs: dict[str, Any]):
        output_model = build_output_model(module, target.returns.name)
        allow_question = ask is not None and has_declared_doubt(target)
        prompt = render_prompt(
            module,
            target,
            kwargs,
            output_model,
            extra_rules=task.constraints,
            allow_question=allow_question,
        )
        outcome = guarded_call(
            adapter,
            prompt,
            output_model,
            max_repairs=max_repairs,
            ask=ask if allow_question else None,
        )
        counter["calls"] += outcome.attempts
        for clarification in outcome.clarifications:
            clarification.task = target.name
            clarifications.append(clarification)
        collect_envelope(outcome, target.name)
        return outcome

    def abort_run(failed_record: StepRecord) -> RunResult:
        records.append(failed_record)
        _unwind(adapter, completed, records, notes, counter)
        return RunResult(
            task=task.name,
            mode="orchestrate",
            status="aborted",
            steps=records,
            notes=notes,
            clarifications=clarifications,
            substitutions=substitutions,
            rule_conflicts=rule_conflicts,
            adapter_calls=counter["calls"],
        )

    def evaluate_derivation(derivation) -> dict[str, Any]:
        """Mechanical first-match evaluation (spec 2.2). Atoms over skipped
        steps are false; copied values from skipped steps are null — a
        derivation always yields a value."""

        def atom_holds(atom) -> bool:
            value = resolve(atom.path.parts)
            if value is _SKIP:
                return False
            if atom.op is None:
                return (not value) if atom.negated else bool(value)
            return {
                "==": lambda: value == atom.value,
                "!=": lambda: value != atom.value,
                ">": lambda: value > atom.value,
                ">=": lambda: value >= atom.value,
                "<": lambda: value < atom.value,
                "<=": lambda: value <= atom.value,
                "in": lambda: value in atom.value,
            }[atom.op]()

        for row in derivation.rows:
            if all(atom_holds(atom) for atom in row.atoms):
                output = {}
                for field, ref in row.output.items():
                    if ref.path is not None:
                        resolved = resolve(ref.path.parts)
                        output[field] = None if resolved is _SKIP else resolved
                    else:
                        output[field] = ref.value
                return output
        return {}  # no catch-all matched — lint AS043 flags this shape

    items = sorted(
        [("bind", b) for b in task.pipeline] + [("derivation", d) for d in task.derivations],
        key=lambda item: item[1].loc.line,
    )
    for kind, bind in items:
        if kind == "derivation":
            results[bind.var] = evaluate_derivation(bind)
            records.append(StepRecord(var=bind.var, task="<derived>", status="ok"))
            continue
        target = module.tasks.get(bind.task)
        if target is None:
            raise RunError(f"'{bind.var}' references undefined task '{bind.task}'")

        if bind.gate is not None:
            gate_value = resolve(bind.gate.path.parts) if bind.gate.path else None
            if gate_value is _SKIP:
                condition = False  # a skipped producer skips the gated step too
            elif bind.gate_negated:
                condition = not gate_value
            else:
                condition = bool(gate_value)
            if not condition:
                results[bind.var] = _SKIP
                records.append(StepRecord(var=bind.var, task=bind.task, status="skipped"))
                continue

        def attempt(bind=bind, target=target) -> Any:
            if bind.fanout is not None:
                return _run_fanout(bind, target, resolve, guarded_step, notes)
            kwargs = _resolve_kwargs(bind, target, resolve)
            if kwargs is _SKIP:
                return _SKIP
            outcome = guarded_step(target, kwargs)
            attempt.last_failures = outcome.failures  # type: ignore[attr-defined]
            return outcome.output

        attempt.last_failures = []  # type: ignore[attr-defined]
        output = attempt()
        if output is _SKIP:
            results[bind.var] = _SKIP
            records.append(StepRecord(var=bind.var, task=bind.task, status="skipped"))
            continue
        failures = list(attempt.last_failures)  # type: ignore[attr-defined]
        if output is None:
            status, output = resolve_with_policy(
                target.on_failure, lambda: _not_skip(attempt()), notes
            )
            if status == "aborted":
                return abort_run(
                    StepRecord(var=bind.var, task=bind.task, status="failed", failures=failures)
                )
            record_status = "declared_fallback" if status == "declared_fallback" else "ok"
        else:
            record_status = "ok"
        results[bind.var] = output
        records.append(
            StepRecord(
                var=bind.var,
                task=bind.task,
                status=record_status,
                failures=failures,
                output=output,
            )
        )
        if record_status == "ok":
            completed.append((bind.var, target, output))

    # The orchestrator is the reducer (spec §7).
    output_model = build_output_model(module, task.returns.name)
    collected = {var: (None if value is _SKIP else value) for var, value in results.items()}
    reducer_inputs = {**root_inputs, "collected_step_results": collected, "env": env}
    reducer_asks = ask is not None and has_declared_doubt(task)
    prompt = render_prompt(module, task, reducer_inputs, output_model, allow_question=reducer_asks)

    def reduce_attempt() -> dict | None:
        outcome = guarded_call(
            adapter,
            prompt,
            output_model,
            max_repairs=max_repairs,
            ask=ask if reducer_asks else None,
        )
        counter["calls"] += outcome.attempts
        for clarification in outcome.clarifications:
            clarification.task = task.name
            clarifications.append(clarification)
        collect_envelope(outcome, task.name)
        if outcome.output is None:
            notes.extend(f"reduction violation: {f}" for f in outcome.failures)
        return outcome.output

    output = reduce_attempt()
    status = "conforming"
    if output is None:
        status, output = resolve_with_policy(task.on_failure, reduce_attempt, notes)
        if status == "aborted":
            _unwind(adapter, completed, records, notes, counter)
    return RunResult(
        task=task.name,
        mode="orchestrate",
        status=status,
        output=output,
        steps=records,
        notes=notes,
        clarifications=clarifications,
        substitutions=substitutions,
        rule_conflicts=rule_conflicts,
        adapter_calls=counter["calls"],
    )


def _not_skip(value: Any) -> Any:
    return None if value is _SKIP else value


def _resolve_kwargs(bind: PipelineBind, target: TaskDef, resolve, loop_var=None, loop_item=None):
    """Resolve a bind's kwargs; returns _SKIP when a required producer was
    skipped (spec §7: a false gate skips everything depending only on it —
    inputs declared `X | None` tolerate the skip and receive None)."""
    optional = {i.name for i in target.inputs if i.type.optional}
    kwargs: dict[str, Any] = {}
    for name, ref in bind.kwargs.items():
        if ref.kind == "literal":
            kwargs[name] = ref.value
            continue
        if ref.path is None:
            raise RunError(f"unresolvable value for '{name}': {ref.raw}")
        value = resolve(ref.path.parts, loop_var, loop_item)
        if value is _SKIP:
            if name in optional:
                kwargs[name] = None
            else:
                return _SKIP
        else:
            kwargs[name] = value
    return kwargs


def _run_fanout(bind: PipelineBind, target: TaskDef, resolve, guarded_step, notes):
    fanout = bind.fanout
    assert fanout is not None
    if fanout.source.path is None:
        raise RunError(f"fan-out '{bind.var}' has no resolvable source")
    source = resolve(fanout.source.path.parts)
    if source is _SKIP:
        return _SKIP
    if not isinstance(source, list):
        raise RunError(f"fan-out '{bind.var}' source is not a list")
    items = source
    if fanout.filter is not None:
        if not fanout.filter.valid:
            raise RunError(f"fan-out '{bind.var}' has a filter outside the cage")
        items = [
            item for item in items if _filter_ok(fanout.filter, item, fanout.loop_var, resolve)
        ]
    outputs = []
    for index, item in enumerate(items):
        kwargs = _resolve_kwargs(bind, target, resolve, fanout.loop_var, item)
        if kwargs is _SKIP:
            return _SKIP
        outcome = guarded_step(target, kwargs)
        if outcome.output is None:
            if target.on_item_failure == "skip_and_report":
                notes.append(
                    f"{bind.var}[{index}] failed and was skipped "
                    f"(on_item_failure): {'; '.join(outcome.failures[:2])}"
                )
                continue
            return None  # "abort": the whole step fails; on_failure applies
        outputs.append(outcome.output)
    return outputs


def _filter_ok(filt, item, loop_var, resolve) -> bool:
    assert filt.path is not None
    value = resolve(filt.path.parts, loop_var, item)
    return value in filt.values if filt.op == "in" else value == filt.values[0]


def _unwind(adapter, completed, records, notes, counter) -> None:
    """Saga unwind: perform each completed step's declared undo, reverse order."""
    by_var = {record.var: record for record in records}
    for var, target, output in reversed(completed):
        if not target.undo:
            continue
        prompt = "\n".join(
            [
                "The routine aborted; the saga unwind is running in reverse completion order.",
                f"Perform the declared undo for task {target.name}:",
                target.undo,
                "",
                "The task's recorded output was:",
                json.dumps(output, indent=2, default=str),
                "",
                "Reply with a short report of what you reversed.",
            ]
        )
        counter["calls"] += 1
        try:
            reply = adapter(prompt)
            notes.append(f"unwound {var} ({target.name}): {reply.strip()[:200]}")
            if var in by_var:
                by_var[var].undo_report = reply.strip()
        except Exception as exc:
            notes.append(f"undo of {var} ({target.name}) FAILED: {exc}")
        if var in by_var:
            by_var[var].status = "unwound"


def _env() -> dict[str, str]:
    """The closed env namespace (spec §10), with real values at run time."""
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    return {
        "now": datetime.now(UTC).isoformat(),
        "cwd": os.getcwd(),
        "user": user,
        "platform": sys.platform,
        "run_id": uuid4().hex,
    }
