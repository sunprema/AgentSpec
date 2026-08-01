"""Rule-hygiene and safety warnings: AS030 whys, AS031 constraint counts,
AS033 unused tasks, AS034 undo on mutating tasks, AS035 fan-out failure modes."""

from collections.abc import Iterator

from agentspec.diagnostics import Diagnostic
from agentspec.lint.catalog import mk
from agentspec.parser import Rule, SpecModule

CONSTRAINT_CEILING = 15  # spec §5: instruction adherence drops with count


def check_hygiene(module: SpecModule) -> Iterator[Diagnostic]:
    for const in module.constants.values():
        for rule in const.rules or []:
            yield from _rule_hygiene(rule)
    for task in module.tasks.values():
        for rule in task.constraints:
            yield from _rule_hygiene(rule)
        if len(task.constraints) > CONSTRAINT_CEILING:
            yield mk(
                "AS031",
                f"task '{task.name}' has {len(task.constraints)} constraints "
                f"(> ~{CONSTRAINT_CEILING}); instruction adherence drops with "
                "count — decompose instead",
                task.loc,
            )
        if any(tool.exclusive for tool in task.tools) and not task.undo:
            yield mk(
                "AS034",
                f"task '{task.name}' holds an exclusive tool but declares no "
                "undo — an abort cannot unwind its effects",
                task.loc,
            )
        for bind in task.pipeline:
            if bind.fanout is None:
                continue
            target = module.tasks.get(bind.task)
            if target is not None and target.on_item_failure is None:
                yield mk(
                    "AS035",
                    f"'{bind.var}' fans out over task '{target.name}', which "
                    "declares no on_item_failure",
                    bind.loc,
                )
    yield from _check_unused(module)


def _rule_hygiene(rule: Rule) -> Iterator[Diagnostic]:
    if rule.severity == "must" and not rule.why:
        yield mk(
            "AS030",
            f'must-rule has no why: "{_clip(rule.text)}"',
            rule.loc,
        )


def _clip(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _check_unused(module: SpecModule) -> Iterator[Diagnostic]:
    """AS033: with an orchestrator present, every task should be reachable
    from an orchestrator root. A file of only worker tasks is a library and
    is not checked."""
    orchestrator_roots = [
        name for name in module.root_tasks() if module.tasks[name].is_orchestrator
    ]
    if not any(task.is_orchestrator for task in module.tasks.values()):
        return
    reachable: set[str] = set()
    stack = list(orchestrator_roots)
    while stack:
        name = stack.pop()
        if name in reachable:
            continue
        reachable.add(name)
        task = module.tasks.get(name)
        if task is not None:
            stack.extend(bind.task for bind in task.pipeline if bind.task in module.tasks)
    for name, task in module.tasks.items():
        if name not in reachable:
            yield mk("AS033", f"task '{name}' is never referenced", task.loc)
