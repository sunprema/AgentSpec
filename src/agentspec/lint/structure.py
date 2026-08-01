"""Structural task checks: AS003 unknown attrs, AS004 docstrings,
AS005 named-schema outputs, AS010 terminating failure chains."""

from collections.abc import Iterator

from agentspec.diagnostics import Diagnostic
from agentspec.lint.catalog import mk
from agentspec.parser import FailurePolicy, SpecModule, TaskDef


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
