"""Structural task checks: AS003 unknown attrs, AS004 docstrings,
AS005 named-schema outputs, AS010 terminating failure chains, AS038
fallback-literal conformance, AS039 ambiguous roots, AS040 ineffective
on_item_failure."""

from collections.abc import Iterator
from typing import Any

from pydantic import ValidationError

from agentspec.diagnostics import Diagnostic
from agentspec.eval.schema import build_output_model
from agentspec.lint.catalog import mk
from agentspec.parser import FailurePolicy, SourceLoc, SpecModule, TaskDef


def check_structure(module: SpecModule) -> Iterator[Diagnostic]:
    for task in module.tasks.values():
        for attr in task.unknown_attrs:
            yield mk(
                "AS003",
                f"unknown attribute '{attr.name}' on task '{task.name}' — not a "
                "reserved attribute and not a pipeline bind",
                attr.loc,
            )
        if not (task.docstring and task.docstring.strip()):
            yield mk(
                "AS004",
                f"task '{task.name}' has no docstring — the docstring is the "
                "task's core instruction",
                task.loc,
            )
        yield from _check_returns(module, task)
        if task.on_failure is not None:
            yield from _check_policy(task, task.on_failure)
        yield from _check_fallback_literals(module, task)
    yield from _check_roots(module)
    yield from _check_item_failure_placement(module)


def _check_returns(module: SpecModule, task: TaskDef) -> Iterator[Diagnostic]:
    returns = task.returns
    if returns is None:
        yield mk("AS005", f"task '{task.name}' declares no returns schema", task.loc)
        return
    if returns.kind == "name" and returns.name in module.schemas:
        return
    got = returns.name if returns.kind == "name" else returns.kind
    yield mk(
        "AS005",
        f"returns of '{task.name}' must be a named schema, got '{got}' — "
        "anonymous returns erase the downstream contract",
        task.loc,
    )


def _check_policy(task: TaskDef, policy: FailurePolicy) -> Iterator[Diagnostic]:
    if policy.kind in ("retry", "escalate"):
        if policy.then is None:
            yield mk(
                "AS010",
                f"{policy.kind.capitalize()} in task '{task.name}' has no "
                "terminating 'then' behavior",
                policy.loc,
            )
        else:
            yield from _check_policy(task, policy.then)


def _fallback_literals(task: TaskDef) -> Iterator[tuple[str, Any, SourceLoc]]:
    if task.on_uncertain is not None:
        yield "on_uncertain", task.on_uncertain, task.loc
    policy, label = task.on_failure, "on_failure"
    while policy is not None:
        if policy.kind == "literal":
            yield label, policy.literal, policy.loc
        policy, label = policy.then, "on_failure 'then'"


def _check_fallback_literals(module: SpecModule, task: TaskDef) -> Iterator[Diagnostic]:
    """AS038: a declared fallback IS the routine's behavior at its worst
    moment — a fallback that violates the output contract is an error."""
    returns = task.returns
    if returns is None or returns.kind != "name" or returns.name not in module.schemas:
        return
    entries = list(_fallback_literals(task))
    if not entries:
        return
    try:
        model = build_output_model(module, returns.name)
    except Exception:  # noqa: BLE001 — an unbuildable schema is AS005 territory
        return
    for label, literal, loc in entries:
        if not isinstance(literal, dict):
            yield mk(
                "AS038",
                f"{label} of '{task.name}' must be a literal object matching "
                f"{returns.name}, got {type(literal).__name__}",
                loc,
            )
            continue
        try:
            # strict: these literals are author-written declarations, not
            # model output — "yes" is not a bool in a declared fallback
            model.model_validate(literal, strict=True)
        except ValidationError as exc:
            issues = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or '<output>'}: {err['msg']}"
                for err in exc.errors()[:3]
            )
            yield mk(
                "AS038",
                f"{label} of '{task.name}' does not conform to {returns.name}: {issues}",
                loc,
            )


def _check_roots(module: SpecModule) -> Iterator[Diagnostic]:
    """AS039: two orchestrator roots lint clean individually, but
    `aspec run` cannot pick one — the ambiguity should surface in CI,
    not at dispatch."""
    orchestrator_roots = sorted(
        name for name in module.root_tasks() if module.tasks[name].is_orchestrator
    )
    if len(orchestrator_roots) > 1:
        yield mk(
            "AS039",
            f"{len(orchestrator_roots)} orchestrator roots "
            f"({', '.join(orchestrator_roots)}) — there is no unique root task; "
            "the dispatch context must name one",
            module.tasks[orchestrator_roots[0]].loc,
        )


def _check_item_failure_placement(module: SpecModule) -> Iterator[Diagnostic]:
    """AS040: on_item_failure only means something for a task that is
    fanned out over. A file of only worker tasks is a library and is not
    checked (same exemption as AS033)."""
    if not any(task.is_orchestrator for task in module.tasks.values()):
        return
    fanned_over = {
        bind.task
        for task in module.tasks.values()
        for bind in task.pipeline
        if bind.fanout is not None
    }
    for task in module.tasks.values():
        if (
            task.on_item_failure is not None
            and task.name not in fanned_over
            and not task.is_orchestrator
        ):
            yield mk(
                "AS040",
                f"task '{task.name}' declares on_item_failure but nothing fans "
                "out over it — the declaration has no effect",
                task.loc,
            )
