"""Rule-hygiene and safety warnings: AS030 whys, AS031 constraint counts,
AS033 unused tasks, AS034 undo on mutating tasks, AS035 fan-out failure
modes, AS036 duplicate rule ids, AS037 rule id format, AS045 undeclared
failure behavior, AS046 weakened inherited musts."""

import re
from collections.abc import Iterator

from agentspec.diagnostics import Diagnostic
from agentspec.lint.catalog import mk
from agentspec.parser import Rule, SpecModule

CONSTRAINT_CEILING = 15  # spec §5: instruction adherence drops with count

KEBAB_ID = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


def check_hygiene(module: SpecModule) -> Iterator[Diagnostic]:
    for const in module.constants.values():
        for rule in const.rules or []:
            yield from _rule_hygiene(rule)
    for task in module.tasks.values():
        seen_ids: dict[str, Rule] = {}
        for rule in task.constraints:
            yield from _rule_hygiene(rule)
            prior = seen_ids.get(rule.id)
            if prior is not None and prior.text != rule.text:
                yield mk(
                    "AS036",
                    f"task '{task.name}': rule id '{rule.id}' is declared "
                    "twice with different text — ids must be unique within "
                    "a task's composed constraints",
                    rule.loc,
                )
            seen_ids.setdefault(rule.id, rule)
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
        irreversible = sorted(
            f"{tool.name}:{op}"
            for tool in task.tools
            for op in tool.ops
            if tool.risk_of(op) == "irreversible"
        )
        if irreversible and not task.undo:
            yield mk(
                "AS047",
                f"task '{task.name}' declares irreversible op(s) "
                f"{', '.join(irreversible)} but no undo — an abort cannot "
                "unwind what cannot be reversed; declare the undo (or the "
                "closest compensating action) explicitly",
                task.loc,
            )
        if task.on_failure is None:
            yield mk(
                "AS045",
                f"task '{task.name}' declares no on_failure — a runtime's "
                "only conservative reading is abort; declare the behavior "
                "instead of implying it",
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
    yield from _check_weakened_musts(module)


def _rule_hygiene(rule: Rule) -> Iterator[Diagnostic]:
    if rule.severity == "must" and not rule.why:
        yield mk(
            "AS030",
            f"must-rule '{rule.id}' has no why",
            rule.loc,
        )
    if not KEBAB_ID.match(rule.id):
        yield mk(
            "AS037",
            f"rule id '{rule.id}' is not kebab-case (lowercase words joined by hyphens)",
            rule.loc,
        )


def _clip(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _check_weakened_musts(module: SpecModule) -> Iterator[Diagnostic]:
    """AS046 (spec §5): a parent task's constraints bind every step inside
    it — a step may add stricter rules; it may never weaken a parent must.
    Walked transitively, since a step may itself be an orchestrator."""
    reported: set[tuple[str, str]] = set()
    for root in module.tasks.values():
        if root.is_orchestrator:
            yield from _weakened_below(module, root, {}, {root.name}, reported)


def _weakened_below(
    module: SpecModule,
    parent,
    inherited: dict[str, str],
    visited: set[str],
    reported: set[tuple[str, str]],
) -> Iterator[Diagnostic]:
    musts = dict(inherited)
    for rule in parent.constraints:
        if rule.severity == "must":
            musts.setdefault(rule.id, parent.name)
    for bind in parent.pipeline:
        child = module.tasks.get(bind.task)
        if child is None or child.name in visited:
            continue
        for rule in child.constraints:
            owner = musts.get(rule.id)
            if owner is None or rule.severity == "must":
                continue
            if (child.name, rule.id) not in reported:
                reported.add((child.name, rule.id))
                yield mk(
                    "AS046",
                    f"task '{child.name}' redeclares rule '{rule.id}' as "
                    f"{rule.severity}, weakening a must inherited from "
                    f"'{owner}' — a step may never weaken a parent must",
                    rule.loc,
                )
        yield from _weakened_below(module, child, musts, visited | {child.name}, reported)


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
