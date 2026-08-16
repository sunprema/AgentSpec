"""Compare two SpecModules and classify every semantic difference.

The comparison is structural, never textual: rules match by their id,
tools by their name, binds by their variable, fields by their name. A
reworded rule therefore reports as `rule-text-changed` on its stable id —
the requirement changed, but its identity survived the edit.
"""

from typing import Any

from agentspec.diff.model import Change
from agentspec.parser.model import (
    RISK_RANK,
    FailurePolicy,
    PipelineBind,
    SchemaDef,
    SchemaField,
    SpecModule,
    TaskDef,
    ToolDecl,
    TypeExpr,
    ValueRef,
)

_SEVERITY_RANK = {"may": 0, "should": 1, "must": 2}


def diff_modules(old: SpecModule, new: SpecModule) -> list[Change]:
    changes: list[Change] = []
    _diff_toplevel(old, new, changes)
    for name in sorted(old.schemas.keys() & new.schemas.keys()):
        _diff_schema(old.schemas[name], new.schemas[name], changes)
    for name in sorted(old.tasks.keys() & new.tasks.keys()):
        _diff_task(old.tasks[name], new.tasks[name], changes)
    return changes


def _add(changes: list[Change], **kw: Any) -> None:
    changes.append(Change(**kw))


# ---------------- module level ----------------


def _diff_toplevel(old: SpecModule, new: SpecModule, changes: list[Change]) -> None:
    if (old.docstring or "") != (new.docstring or ""):
        _add(
            changes,
            code="module-docstring-changed",
            category="module",
            kind="changed",
            direction="neutral",
            owner="<module>",
            detail="module docstring changed (revision history / postmortems)",
        )

    for name in sorted(new.schemas.keys() - old.schemas.keys()):
        _add(
            changes,
            code="schema-added",
            category="schema",
            kind="added",
            direction="neutral",
            owner=name,
            detail=f"schema '{name}' added",
        )
    for name in sorted(old.schemas.keys() - new.schemas.keys()):
        _add(
            changes,
            code="schema-removed",
            category="schema",
            kind="removed",
            direction="reshapes",
            owner=name,
            detail=f"schema '{name}' removed — its consumers lose the contract",
        )

    for name in sorted(new.tasks.keys() - old.tasks.keys()):
        task = new.tasks[name]
        kind = "orchestrator" if task.is_orchestrator else "task"
        _add(
            changes,
            code="task-added",
            category="task",
            kind="added",
            direction="reshapes",
            owner=name,
            detail=f"{kind} '{name}' added",
        )
        # a new task's capabilities are new capabilities — flag them loudly
        for tool in task.tools:
            _add(
                changes,
                code="tool-added",
                category="tool",
                kind="added",
                direction="widens",
                owner=name,
                detail=f"tool '{tool.name}' added ({_surface(tool)}) with new task '{name}'",
                new=_surface(tool),
            )
    for name in sorted(old.tasks.keys() - new.tasks.keys()):
        _add(
            changes,
            code="task-removed",
            category="task",
            kind="removed",
            direction="reshapes",
            owner=name,
            detail=f"task '{name}' removed",
        )

    if old.root_task != new.root_task:
        _add(
            changes,
            code="root-task-changed",
            category="module",
            kind="changed",
            direction="reshapes",
            owner="<module>",
            detail=f"root task changed: {old.root_task} -> {new.root_task}",
            old=old.root_task,
            new=new.root_task,
        )


# ---------------- schemas ----------------


def _type_str(t: TypeExpr | None) -> str:
    return "?" if t is None else t.render()


def _type_core(t: TypeExpr | None) -> Any:
    """Type identity ignoring optionality (diffed separately)."""
    if t is None:
        return None
    return (t.kind, t.name, tuple(repr(v) for v in t.values), _type_core(t.item), t.raw)


def _diff_schema(old: SchemaDef, new: SchemaDef, changes: list[Change]) -> None:
    old_fields = {f.name: f for f in old.fields}
    new_fields = {f.name: f for f in new.fields}

    for name in sorted(new_fields.keys() - old_fields.keys()):
        field = new_fields[name]
        required = not (field.type.optional or field.has_default)
        _add(
            changes,
            code="field-added",
            category="schema",
            kind="added",
            direction="reshapes" if required else "neutral",
            owner=old.name,
            detail=f"field '{name}: {_type_str(field.type)}' added"
            + (" (required — producers must now emit it)" if required else " (optional)"),
        )
    for name in sorted(old_fields.keys() - new_fields.keys()):
        _add(
            changes,
            code="field-removed",
            category="schema",
            kind="removed",
            direction="reshapes",
            owner=old.name,
            detail=f"field '{name}' removed — consumers lose it",
        )
    for name in sorted(old_fields.keys() & new_fields.keys()):
        _diff_field(old.name, old_fields[name], new_fields[name], changes)


def _diff_field(owner: str, old: SchemaField, new: SchemaField, changes: list[Change]) -> None:
    label = f"{owner}.{old.name}"

    if old.type.kind == "enum" and new.type.kind == "enum":
        added = [v for v in new.type.values if v not in old.type.values]
        removed = [v for v in old.type.values if v not in new.type.values]
        if added and removed:
            _add(
                changes,
                code="enum-changed",
                category="schema",
                kind="changed",
                direction="reshapes",
                owner=owner,
                detail=f"{label}: enum values changed: +{added!r} -{removed!r}",
                old=old.type.values,
                new=new.type.values,
            )
        elif added:
            _add(
                changes,
                code="enum-widened",
                category="schema",
                kind="changed",
                direction="widens",
                owner=owner,
                detail=f"{label}: enum widened: +{added!r}",
                old=old.type.values,
                new=new.type.values,
            )
        elif removed:
            _add(
                changes,
                code="enum-narrowed",
                category="schema",
                kind="changed",
                direction="narrows",
                owner=owner,
                detail=f"{label}: enum narrowed: -{removed!r}",
                old=old.type.values,
                new=new.type.values,
            )
    elif _type_core(old.type) != _type_core(new.type):
        _add(
            changes,
            code="field-type-changed",
            category="schema",
            kind="changed",
            direction="reshapes",
            owner=owner,
            detail=f"{label}: type changed: {_type_str(old.type)} -> {_type_str(new.type)}",
            old=_type_str(old.type),
            new=_type_str(new.type),
        )

    if old.type.optional != new.type.optional:
        if new.type.optional:
            _add(
                changes,
                code="field-now-optional",
                category="schema",
                kind="changed",
                direction="widens",
                owner=owner,
                detail=f"{label}: became optional — the value may now be absent",
            )
        else:
            _add(
                changes,
                code="field-now-required",
                category="schema",
                kind="changed",
                direction="narrows",
                owner=owner,
                detail=f"{label}: became required",
            )

    _diff_bound(owner, label, "ge", old.ge, new.ge, loosens_upward=False, changes=changes)
    _diff_bound(owner, label, "le", old.le, new.le, loosens_upward=True, changes=changes)
    _diff_bound(
        owner,
        label,
        "max_length",
        old.max_length,
        new.max_length,
        loosens_upward=True,
        changes=changes,
    )


def _diff_bound(
    owner: str,
    label: str,
    bound: str,
    old: float | None,
    new: float | None,
    *,
    loosens_upward: bool,
    changes: list[Change],
) -> None:
    if old == new:
        return
    if old is None:
        direction, verb = "narrows", "added"
    elif new is None:
        direction, verb = "widens", "removed"
    else:
        loosened = new > old if loosens_upward else new < old
        direction, verb = ("widens", "loosened") if loosened else ("narrows", "tightened")
    _add(
        changes,
        code=f"bound-{verb}",
        category="schema",
        kind="changed",
        direction=direction,
        owner=owner,
        detail=f"{label}: bound {bound} {verb}: {old} -> {new}",
        old=old,
        new=new,
    )


# ---------------- tasks ----------------


def _diff_task(old: TaskDef, new: TaskDef, changes: list[Change]) -> None:
    name = old.name

    if (old.docstring or "") != (new.docstring or ""):
        _add(
            changes,
            code="task-docstring-changed",
            category="task",
            kind="changed",
            direction="reshapes",
            owner=name,
            detail="docstring changed — this is the task's core instruction",
        )

    old_inputs = {i.name: i for i in old.inputs}
    new_inputs = {i.name: i for i in new.inputs}
    for iname in sorted(new_inputs.keys() - old_inputs.keys()):
        _add(
            changes,
            code="input-added",
            category="task",
            kind="added",
            direction="reshapes",
            owner=name,
            detail=f"input '{iname}: {_type_str(new_inputs[iname].type)}' added",
        )
    for iname in sorted(old_inputs.keys() - new_inputs.keys()):
        _add(
            changes,
            code="input-removed",
            category="task",
            kind="removed",
            direction="reshapes",
            owner=name,
            detail=f"input '{iname}' removed",
        )
    for iname in sorted(old_inputs.keys() & new_inputs.keys()):
        if _type_core(old_inputs[iname].type) != _type_core(new_inputs[iname].type) or (
            old_inputs[iname].type.optional != new_inputs[iname].type.optional
        ):
            _add(
                changes,
                code="input-type-changed",
                category="task",
                kind="changed",
                direction="reshapes",
                owner=name,
                detail=f"input '{iname}': type changed: "
                f"{_type_str(old_inputs[iname].type)} -> {_type_str(new_inputs[iname].type)}",
            )

    if _type_str(old.returns) != _type_str(new.returns):
        _add(
            changes,
            code="returns-changed",
            category="task",
            kind="changed",
            direction="reshapes",
            owner=name,
            detail=f"returns changed: {_type_str(old.returns)} -> {_type_str(new.returns)}",
            old=_type_str(old.returns),
            new=_type_str(new.returns),
        )

    _diff_tools(old, new, changes)
    _diff_rules(old, new, changes)
    _diff_failure_decls(old, new, changes)
    _diff_pipeline(old, new, changes)

    if (old.meta or {}) != (new.meta or {}):
        _add(
            changes,
            code="meta-changed",
            category="task",
            kind="changed",
            direction="neutral",
            owner=name,
            detail=f"meta changed: {old.meta!r} -> {new.meta!r}",
            old=old.meta,
            new=new.meta,
        )


# ---------------- tools ----------------


def _surface(tool: ToolDecl) -> str:
    parts = []
    if tool.ops:
        parts.append(f"ops={tool.op_display()!r}")
    for attr in ("scripts", "paths"):
        values = getattr(tool, attr)
        if values:
            parts.append(f"{attr}={values!r}")
    if tool.strict:
        parts.append("strict")
    if tool.exclusive:
        parts.append("exclusive")
    return ", ".join(parts) if parts else "unrestricted"


def _tools_by_name(tools: list[ToolDecl]) -> dict[str, dict[str, Any]]:
    """Merge same-named declarations: one capability, a combined surface."""
    merged: dict[str, dict[str, Any]] = {}
    for tool in tools:
        entry = merged.setdefault(
            tool.name,
            {
                "ops": [],
                "risks": {},
                "scripts": [],
                "paths": [],
                "strict": False,
                "exclusive": False,
            },
        )
        entry["ops"] += tool.ops
        for op in tool.ops:
            # merged risk is the widest any declaration grants the op
            risk = tool.risk_of(op)
            prior = entry["risks"].get(op)
            if prior is None or RISK_RANK[risk] > RISK_RANK[prior]:
                entry["risks"][op] = risk
        entry["scripts"] += tool.scripts
        entry["paths"] += tool.paths
        entry["strict"] = entry["strict"] or tool.strict
        entry["exclusive"] = entry["exclusive"] or tool.exclusive
    return merged


def _diff_tools(old: TaskDef, new: TaskDef, changes: list[Change]) -> None:
    name = old.name
    old_tools = _tools_by_name(old.tools)
    new_tools = _tools_by_name(new.tools)

    for tname in sorted(new_tools.keys() - old_tools.keys()):
        decl = next(t for t in new.tools if t.name == tname)
        _add(
            changes,
            code="tool-added",
            category="tool",
            kind="added",
            direction="widens",
            owner=name,
            detail=f"tool '{tname}' added ({_surface(decl)})",
            new=_surface(decl),
        )
    for tname in sorted(old_tools.keys() - new_tools.keys()):
        _add(
            changes,
            code="tool-removed",
            category="tool",
            kind="removed",
            direction="narrows",
            owner=name,
            detail=f"tool '{tname}' removed",
        )

    for tname in sorted(old_tools.keys() & new_tools.keys()):
        before, after = old_tools[tname], new_tools[tname]
        for attr in ("ops", "scripts", "paths"):
            added = [v for v in after[attr] if v not in before[attr]]
            removed = [v for v in before[attr] if v not in after[attr]]
            if added:
                _add(
                    changes,
                    code="tool-surface-widened",
                    category="tool",
                    kind="changed",
                    direction="widens",
                    owner=name,
                    detail=f"tool '{tname}' {attr} widened: +{added!r}",
                    old=before[attr],
                    new=after[attr],
                )
            if removed:
                _add(
                    changes,
                    code="tool-surface-narrowed",
                    category="tool",
                    kind="changed",
                    direction="narrows",
                    owner=name,
                    detail=f"tool '{tname}' {attr} narrowed: -{removed!r}",
                    old=before[attr],
                    new=after[attr],
                )
        # spec 2.4: an op moving up the read → mutate → irreversible lattice
        # is a capability escalation even when the op list itself is unchanged
        for op in sorted(set(before["risks"]) & set(after["risks"])):
            old_risk, new_risk = before["risks"][op], after["risks"][op]
            if RISK_RANK[new_risk] > RISK_RANK[old_risk]:
                _add(
                    changes,
                    code="op-risk-raised",
                    category="tool",
                    kind="changed",
                    direction="widens",
                    owner=name,
                    detail=f"tool '{tname}' op '{op}' risk raised: {old_risk} -> {new_risk}",
                    old=old_risk,
                    new=new_risk,
                )
            elif RISK_RANK[new_risk] < RISK_RANK[old_risk]:
                _add(
                    changes,
                    code="op-risk-lowered",
                    category="tool",
                    kind="changed",
                    direction="narrows",
                    owner=name,
                    detail=f"tool '{tname}' op '{op}' risk lowered: {old_risk} -> {new_risk}",
                    old=old_risk,
                    new=new_risk,
                )
        if before["strict"] != after["strict"]:
            if before["strict"]:
                _add(
                    changes,
                    code="strict-dropped",
                    category="tool",
                    kind="changed",
                    direction="widens",
                    owner=name,
                    detail=f"tool '{tname}': strict dropped — substitution now allowed",
                )
            else:
                _add(
                    changes,
                    code="strict-added",
                    category="tool",
                    kind="changed",
                    direction="narrows",
                    owner=name,
                    detail=f"tool '{tname}': strict added — that exact mechanism or nothing",
                )
        if before["exclusive"] != after["exclusive"]:
            if before["exclusive"]:
                _add(
                    changes,
                    code="exclusive-dropped",
                    category="tool",
                    kind="changed",
                    direction="widens",
                    owner=name,
                    detail=f"tool '{tname}': exclusive dropped — concurrent holders now allowed",
                )
            else:
                _add(
                    changes,
                    code="exclusive-added",
                    category="tool",
                    kind="changed",
                    direction="narrows",
                    owner=name,
                    detail=f"tool '{tname}': exclusive added — fan-out serializes per item",
                )


# ---------------- rules ----------------


def _trunc(text: str, limit: int = 70) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _diff_rules(old: TaskDef, new: TaskDef, changes: list[Change]) -> None:
    name = old.name
    old_rules = {r.id: r for r in old.constraints}
    new_rules = {r.id: r for r in new.constraints}

    for rule_id in sorted(old_rules.keys() - new_rules.keys()):
        rule = old_rules[rule_id]
        _add(
            changes,
            code="rule-removed",
            category="rule",
            kind="removed",
            direction="widens",
            owner=name,
            detail=f"{rule.severity}-rule '{rule_id}' removed: \"{_trunc(rule.text)}\"",
            old=rule.text,
        )
    for rule_id in sorted(new_rules.keys() - old_rules.keys()):
        rule = new_rules[rule_id]
        _add(
            changes,
            code="rule-added",
            category="rule",
            kind="added",
            direction="narrows",
            owner=name,
            detail=f"{rule.severity}-rule '{rule_id}' added: \"{_trunc(rule.text)}\"",
            new=rule.text,
        )

    for rule_id in sorted(old_rules.keys() & new_rules.keys()):
        before, after = old_rules[rule_id], new_rules[rule_id]
        if before.text != after.text:
            _add(
                changes,
                code="rule-text-changed",
                category="rule",
                kind="changed",
                direction="reshapes",
                owner=name,
                detail=f"rule '{rule_id}' text changed — the binding requirement is different",
                old=before.text,
                new=after.text,
            )
        if before.severity != after.severity:
            weakened = _SEVERITY_RANK[after.severity] < _SEVERITY_RANK[before.severity]
            _add(
                changes,
                code="rule-weakened" if weakened else "rule-strengthened",
                category="rule",
                kind="changed",
                direction="widens" if weakened else "narrows",
                owner=name,
                detail=f"rule '{rule_id}' {'weakened' if weakened else 'strengthened'} "
                f"{before.severity} -> {after.severity}",
                old=before.severity,
                new=after.severity,
            )
        if (before.why or "") != (after.why or ""):
            _add(
                changes,
                code="rule-why-changed",
                category="rule",
                kind="changed",
                direction="neutral",
                owner=name,
                detail=f"rule '{rule_id}' rationale changed",
            )
        if (before.since or "") != (after.since or ""):
            _add(
                changes,
                code="rule-since-changed",
                category="rule",
                kind="changed",
                direction="neutral",
                owner=name,
                detail=f"rule '{rule_id}' provenance changed: {before.since} -> {after.since}",
                old=before.since,
                new=after.since,
            )


# ---------------- failure declarations ----------------


def _policy_str(policy: FailurePolicy | None) -> str:
    if policy is None:
        return "undeclared"
    if policy.kind == "abort":
        return "abort"
    if policy.kind == "literal":
        return "declared default"
    if policy.kind == "retry":
        return (
            f"Retry(max={policy.max}, backoff_s={policy.backoff_s}) then {_policy_str(policy.then)}"
        )
    return (
        f"Escalate(channel={policy.channel!r}, timeout_s={policy.timeout_s}) "
        f"then {_policy_str(policy.then)}"
    )


def _policy_key(policy: FailurePolicy | None) -> Any:
    if policy is None:
        return None
    return (
        policy.kind,
        repr(policy.literal),
        policy.max,
        policy.backoff_s,
        policy.channel,
        policy.timeout_s,
        _policy_key(policy.then),
    )


def _diff_failure_decls(old: TaskDef, new: TaskDef, changes: list[Change]) -> None:
    name = old.name

    if (old.undo is None) != (new.undo is None):
        if new.undo is not None:
            _add(
                changes,
                code="undo-added",
                category="failure",
                kind="added",
                direction="narrows",
                owner=name,
                detail="undo declared — the task's reversal is now specified",
            )
        else:
            _add(
                changes,
                code="undo-removed",
                category="failure",
                kind="removed",
                direction="widens",
                owner=name,
                detail="undo removed — the saga unwind can no longer reverse this task",
            )
    elif old.undo != new.undo:
        _add(
            changes,
            code="undo-changed",
            category="failure",
            kind="changed",
            direction="reshapes",
            owner=name,
            detail="undo procedure changed",
        )

    if (old.on_uncertain is None) != (new.on_uncertain is None):
        if new.on_uncertain is not None:
            _add(
                changes,
                code="on-uncertain-added",
                category="failure",
                kind="added",
                direction="narrows",
                owner=name,
                detail="on_uncertain declared — doubt now has a defined output",
            )
        else:
            _add(
                changes,
                code="on-uncertain-removed",
                category="failure",
                kind="removed",
                direction="widens",
                owner=name,
                detail="on_uncertain removed — doubt is no longer declared",
            )
    elif old.on_uncertain != new.on_uncertain:
        _add(
            changes,
            code="on-uncertain-changed",
            category="failure",
            kind="changed",
            direction="reshapes",
            owner=name,
            detail="on_uncertain output changed",
            old=old.on_uncertain,
            new=new.on_uncertain,
        )

    _diff_policy(name, old.on_failure, new.on_failure, changes)

    if (old.on_item_failure or "") != (new.on_item_failure or ""):
        before, after = old.on_item_failure, new.on_item_failure
        if before == "abort" and after == "skip_and_report":
            direction = "widens"
        elif before == "skip_and_report" and after == "abort":
            direction = "narrows"
        else:
            direction = "reshapes"
        _add(
            changes,
            code="on-item-failure-changed",
            category="failure",
            kind="changed",
            direction=direction,
            owner=name,
            detail=f"on_item_failure changed: {before} -> {after}",
            old=before,
            new=after,
        )


def _diff_policy(
    name: str,
    old: FailurePolicy | None,
    new: FailurePolicy | None,
    changes: list[Change],
) -> None:
    if _policy_key(old) == _policy_key(new):
        return
    old_str, new_str = _policy_str(old), _policy_str(new)
    if old is None or new is None:
        _add(
            changes,
            code="on-failure-declared" if old is None else "on-failure-undeclared",
            category="failure",
            kind="added" if old is None else "removed",
            direction="reshapes",
            owner=name,
            detail=f"on_failure changed: {old_str} -> {new_str}",
            old=old_str,
            new=new_str,
        )
        return
    if old.kind != new.kind:
        # abort (stop + unwind) is the most conservative response; leaving it
        # means the run now continues past this task's failure.
        if old.kind == "abort":
            direction = "widens"
        elif new.kind == "abort":
            direction = "narrows"
        else:
            direction = "reshapes"
        _add(
            changes,
            code="on-failure-kind-changed",
            category="failure",
            kind="changed",
            direction=direction,
            owner=name,
            detail=f"failure handling changed: {old_str} -> {new_str}",
            old=old_str,
            new=new_str,
        )
    else:
        _add(
            changes,
            code="on-failure-params-changed",
            category="failure",
            kind="changed",
            direction="reshapes",
            owner=name,
            detail=f"failure policy parameters changed: {old_str} -> {new_str}",
            old=old_str,
            new=new_str,
        )


# ---------------- pipeline ----------------


def _ref_str(ref: ValueRef | None) -> str:
    if ref is None:
        return ""
    if ref.path is not None:
        return ref.path.dotted
    return ref.raw


def _diff_pipeline(old: TaskDef, new: TaskDef, changes: list[Change]) -> None:
    name = old.name
    old_binds = {b.var: b for b in old.pipeline}
    new_binds = {b.var: b for b in new.pipeline}

    for var in sorted(new_binds.keys() - old_binds.keys()):
        bind = new_binds[var]
        gated = f" gated on {bind.gate_condition()}" if bind.gate is not None else ""
        _add(
            changes,
            code="step-added",
            category="pipeline",
            kind="added",
            direction="reshapes",
            owner=name,
            detail=f"step '{var} = {bind.task}' added{gated}",
        )
    for var in sorted(old_binds.keys() - new_binds.keys()):
        _add(
            changes,
            code="step-removed",
            category="pipeline",
            kind="removed",
            direction="reshapes",
            owner=name,
            detail=f"step '{var} = {old_binds[var].task}' removed",
        )

    for var in sorted(old_binds.keys() & new_binds.keys()):
        _diff_bind(name, old_binds[var], new_binds[var], changes)

    old_derivations = {d.var: d for d in old.derivations}
    new_derivations = {d.var: d for d in new.derivations}
    for var in sorted(new_derivations.keys() - old_derivations.keys()):
        _add(
            changes,
            code="derivation-added",
            category="pipeline",
            kind="added",
            direction="reshapes",
            owner=name,
            detail=f"derivation '{var}' added",
        )
    for var in sorted(old_derivations.keys() - new_derivations.keys()):
        _add(
            changes,
            code="derivation-removed",
            category="pipeline",
            kind="removed",
            direction="reshapes",
            owner=name,
            detail=f"derivation '{var}' removed",
        )
    for var in sorted(old_derivations.keys() & new_derivations.keys()):
        _diff_derivation(name, old_derivations[var], new_derivations[var], changes)


def _diff_bind(name: str, old: PipelineBind, new: PipelineBind, changes: list[Change]) -> None:
    label = f"step '{old.var}'"

    if old.task != new.task:
        _add(
            changes,
            code="step-task-changed",
            category="pipeline",
            kind="changed",
            direction="reshapes",
            owner=name,
            detail=f"{label}: task changed: {old.task} -> {new.task}",
            old=old.task,
            new=new.task,
        )

    if (old.gate is None) != (new.gate is None):
        if new.gate is not None:
            _add(
                changes,
                code="gate-added",
                category="pipeline",
                kind="added",
                direction="narrows",
                owner=name,
                detail=f"{label}: gate added — runs only if {new.gate_condition()}",
            )
        else:
            _add(
                changes,
                code="gate-removed",
                category="pipeline",
                kind="removed",
                direction="widens",
                owner=name,
                detail=f"{label}: gate removed — was gated on {old.gate_condition()}, "
                "now always runs",
            )
    elif old.gate is not None and old.gate_condition() != new.gate_condition():
        _add(
            changes,
            code="gate-condition-changed",
            category="pipeline",
            kind="changed",
            direction="reshapes",
            owner=name,
            detail=f"{label}: gate condition changed: "
            f"{old.gate_condition()} -> {new.gate_condition()}",
            old=old.gate_condition(),
            new=new.gate_condition(),
        )

    rewired = []
    for key in sorted(new.kwargs.keys() - old.kwargs.keys()):
        rewired.append(f"+{key}={_ref_str(new.kwargs[key])}")
    for key in sorted(old.kwargs.keys() - new.kwargs.keys()):
        rewired.append(f"-{key}")
    for key in sorted(old.kwargs.keys() & new.kwargs.keys()):
        if _ref_str(old.kwargs[key]) != _ref_str(new.kwargs[key]):
            rewired.append(f"{key}: {_ref_str(old.kwargs[key])} -> {_ref_str(new.kwargs[key])}")
    if rewired:
        _add(
            changes,
            code="step-rewired",
            category="pipeline",
            kind="changed",
            direction="reshapes",
            owner=name,
            detail=f"{label}: inputs rewired: {'; '.join(rewired)}",
        )

    if (old.fanout is None) != (new.fanout is None):
        _add(
            changes,
            code="fanout-added" if new.fanout is not None else "fanout-removed",
            category="pipeline",
            kind="added" if new.fanout is not None else "removed",
            direction="reshapes",
            owner=name,
            detail=f"{label}: fan-out {'added' if new.fanout is not None else 'removed'}",
        )
    elif old.fanout is not None and new.fanout is not None:
        if _ref_str(old.fanout.source) != _ref_str(new.fanout.source):
            _add(
                changes,
                code="fanout-source-changed",
                category="pipeline",
                kind="changed",
                direction="reshapes",
                owner=name,
                detail=f"{label}: fan-out source changed: "
                f"{_ref_str(old.fanout.source)} -> {_ref_str(new.fanout.source)}",
            )
        old_filter = old.fanout.filter.raw if old.fanout.filter is not None else ""
        new_filter = new.fanout.filter.raw if new.fanout.filter is not None else ""
        if old_filter != new_filter:
            if not new_filter:
                _add(
                    changes,
                    code="filter-removed",
                    category="pipeline",
                    kind="removed",
                    direction="widens",
                    owner=name,
                    detail=f"{label}: fan-out filter removed — every item now runs "
                    f"(was: {old_filter})",
                )
            elif not old_filter:
                _add(
                    changes,
                    code="filter-added",
                    category="pipeline",
                    kind="added",
                    direction="narrows",
                    owner=name,
                    detail=f"{label}: fan-out filter added: {new_filter}",
                )
            else:
                _add(
                    changes,
                    code="filter-changed",
                    category="pipeline",
                    kind="changed",
                    direction="reshapes",
                    owner=name,
                    detail=f"{label}: fan-out filter changed: {old_filter} -> {new_filter}",
                )


def _derivation_row_key(row: Any) -> dict[str, str]:
    return {
        field: (ref.path.dotted if ref.path is not None else repr(ref.value))
        for field, ref in row.output.items()
    }


def _diff_derivation(name: str, old: Any, new: Any, changes: list[Change]) -> None:
    """Rows match by condition text — routing changes report per row."""
    old_rows = {r.raw: r for r in old.rows}
    new_rows = {r.raw: r for r in new.rows}
    for raw in sorted(new_rows.keys() - old_rows.keys()):
        _add(
            changes,
            code="derivation-row-added",
            category="pipeline",
            kind="added",
            direction="reshapes",
            owner=name,
            detail=f"derivation '{old.var}': row added for `{raw}`",
        )
    for raw in sorted(old_rows.keys() - new_rows.keys()):
        _add(
            changes,
            code="derivation-row-removed",
            category="pipeline",
            kind="removed",
            direction="reshapes",
            owner=name,
            detail=f"derivation '{old.var}': row removed for `{raw}`",
        )
    for raw in sorted(old_rows.keys() & new_rows.keys()):
        if _derivation_row_key(old_rows[raw]) != _derivation_row_key(new_rows[raw]):
            _add(
                changes,
                code="derivation-row-changed",
                category="pipeline",
                kind="changed",
                direction="reshapes",
                owner=name,
                detail=f"derivation '{old.var}': output changed for `{raw}`",
            )
