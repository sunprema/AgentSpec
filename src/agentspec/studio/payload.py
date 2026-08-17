"""Build the studio's one JSON payload from a SpecModel.

Pure derivation — everything the UI shows comes from the parser, the plan,
and the linter. No heuristics, no model in the loop: this is the studio's
whole advantage over a prompt-file atlas.
"""

from typing import Any

from agentspec.lint import lint_module
from agentspec.parser import PipelineBind, SpecModule, TaskDef, ToolDecl, TypeExpr
from agentspec.plan import build_plan
from agentspec.reduction import extract_tables


def build_payload(module: SpecModule) -> dict[str, Any]:
    root_name = module.root_task
    root = module.tasks.get(root_name) if root_name else None
    payload: dict[str, Any] = {
        "file": module.path,
        "module_docstring": module.docstring,
        "root": root_name,
        "schemas": {name: _schema(module, name) for name in module.schemas},
        "tasks": {name: _task(module, task) for name, task in module.tasks.items()},
        "pipeline": None,
        "edges": [],
        "lint": _lint(module),
    }
    if root is not None and root.is_orchestrator:
        payload["pipeline"] = _pipeline(module, root)
        payload["edges"] = _edges(module, root)
    return payload


def _render(t: TypeExpr | None) -> str:
    return "?" if t is None else t.render()


# ---------------- schemas ----------------


def _schema(module: SpecModule, name: str) -> dict[str, Any]:
    schema = module.schemas[name]
    fields = []
    for field in schema.fields:
        bounds = []
        if field.ge is not None:
            bounds.append(f"ge={field.ge:g}")
        if field.le is not None:
            bounds.append(f"le={field.le:g}")
        if field.max_length is not None:
            bounds.append(f"max_length={field.max_length}")
        fields.append(
            {
                "name": field.name,
                "type": _render(field.type),
                "optional": field.type.optional,
                "bounds": ", ".join(bounds) or None,
            }
        )
    return {"docstring": schema.docstring, "fields": fields}


# ---------------- tasks ----------------


def _surface(tool: ToolDecl) -> str:
    parts = []
    if tool.ops:
        parts.append("ops: " + ", ".join(tool.op_display()))
    for attr in ("scripts", "paths"):
        values = getattr(tool, attr)
        if values:
            parts.append(f"{attr}: " + ", ".join(values))
    return " · ".join(parts) if parts else "unrestricted"


def _failure_str(task: TaskDef) -> str | None:
    policy = task.on_failure
    if policy is None:
        return None
    if policy.kind == "abort":
        return "abort"
    if policy.kind == "literal":
        return "declared default"
    inner = None
    if policy.kind == "retry":
        label = f"retry ×{policy.max}"
        if policy.backoff_s is not None:
            label += f" ({policy.backoff_s:g}s backoff)"
        inner = label
    else:
        inner = f"escalate to {policy.channel or '?'}"
        if policy.timeout_s is not None:
            inner += f" (wait {policy.timeout_s:g}s)"
    then = task.on_failure.then
    then_str = "?" if then is None else _failure_str_policy(then)
    return f"{inner}, then {then_str}"


def _failure_str_policy(policy: Any) -> str:
    if policy.kind == "abort":
        return "abort"
    if policy.kind == "literal":
        return "declared default"
    return policy.kind


def _task(module: SpecModule, task: TaskDef) -> dict[str, Any]:
    returns = task.returns
    returns_schema = None
    if returns is not None and returns.kind == "name" and returns.name in module.schemas:
        returns_schema = returns.name
    return {
        "docstring": task.docstring,
        "inputs": [{"name": i.name, "type": _render(i.type)} for i in task.inputs],
        "returns": _render(task.returns),
        "returns_schema": returns_schema,
        "tools": [
            {
                "name": t.name,
                "surface": _surface(t),
                "strict": t.strict,
                "exclusive": t.exclusive,
            }
            for t in task.tools
        ],
        "rules": [
            {
                "id": r.id,
                "text": r.text,
                "why": r.why,
                "severity": r.severity,
                "since": r.since,
                "source": r.source,
            }
            for r in task.constraints
        ],
        "undo": task.undo,
        "has_on_uncertain": task.on_uncertain is not None,
        "on_failure": _failure_str(task),
        "on_item_failure": task.on_item_failure,
        "meta": task.meta,
        "is_orchestrator": task.is_orchestrator,
        "line": task.loc.line,
    }


# ---------------- pipeline + simulator needs ----------------


def _needs(module: SpecModule, task: TaskDef, bind: PipelineBind) -> list[dict[str, Any]]:
    """What this step consumes from prior steps, and whether each consumed
    input tolerates absence (`X | None`) — the simulator's propagation data."""
    step_vars = {b.var for b in task.pipeline}
    child = module.tasks.get(bind.task)
    needs = []
    for input_name, ref in bind.kwargs.items():
        if ref.path is None or ref.path.root not in step_vars:
            continue
        declared = next((i for i in child.inputs if i.name == input_name), None) if child else None
        needs.append(
            {
                "root": ref.path.root,
                "input": input_name,
                "optional": bool(declared and declared.type.optional),
            }
        )
    if bind.gate is not None and bind.gate.path is not None and bind.gate.path.root in step_vars:
        needs.append({"root": bind.gate.path.root, "input": "<gate>", "optional": False})
    fanout = bind.fanout
    if fanout is not None and fanout.source.path is not None:
        source_root = fanout.source.path.root
        if source_root in step_vars:
            needs.append({"root": source_root, "input": "<fan-out>", "optional": False})
    return needs


def _pipeline(module: SpecModule, root: TaskDef) -> dict[str, Any]:
    plan = build_plan(module, root)
    binds = {b.var: b for b in root.pipeline}
    derivations = {d.var: d for d in root.derivations}
    steps = []
    for step in plan.steps:
        if step.var in derivations:
            steps.append(
                {
                    "var": step.var,
                    "task": "<derived>",
                    "wave": step.wave,
                    "depends_on": step.depends_on,
                    "gate": None,
                    "gate_root": None,
                    "fanout_over": None,
                    "filter": None,
                    "serialized": False,
                    "needs": [],  # a derivation always yields; it never skips
                    "line": derivations[step.var].loc.line,
                }
            )
            continue
        bind = binds[step.var]
        gate_root = None
        if bind.gate is not None and bind.gate.path is not None:
            gate_root = bind.gate.path.root
        steps.append(
            {
                "var": step.var,
                "task": step.task,
                "wave": step.wave,
                "depends_on": step.depends_on,
                "gate": step.gate,
                "gate_root": gate_root,
                "fanout_over": step.fanout_over,
                "filter": step.filter,
                "serialized": step.serialized,
                "needs": _needs(module, root, bind),
                "line": bind.loc.line,
            }
        )
    return {
        "orchestrator": plan.orchestrator,
        "inputs": [i.name for i in root.inputs],
        "steps": steps,
        "waves": plan.waves,
        "gate_skips": plan.gate_skips,
        "warnings": plan.warnings,
        "reduction": _reduction_table(module, root),
    }


def _reduction_table(module: SpecModule, root: TaskDef) -> dict[str, Any] | None:
    """The routing decision as a generic grid — from a derivation bind
    (spec 2.2) when one exists, else from the prose lift."""
    for derivation in root.derivations:
        fields = sorted({f for row in derivation.rows for f in row.output})
        return {
            "source": "derivation",
            "name": derivation.var,
            "fields": fields,
            "rows": [
                {
                    "condition": "otherwise" if row.is_catch_all else row.raw,
                    "values": {
                        f: (ref.path.dotted if ref.path is not None else ref.value)
                        for f, ref in row.output.items()
                    },
                }
                for row in derivation.rows
            ],
        }
    for table in extract_tables(module):
        if table.task != root.name:
            continue
        outcome_field = table.outcome_field or "outcome"
        stage_field = table.stage_field or "stage"
        return {
            "source": "prose",
            "name": table.rule_id,
            "fields": [outcome_field, stage_field],
            "rows": [
                {
                    "condition": row.condition,
                    "values": {outcome_field: row.outcome, stage_field: row.stage},
                }
                for row in table.rows
            ],
        }
    return None


def _edges(module: SpecModule, root: TaskDef) -> list[dict[str, Any]]:
    """Data-flow edges labeled with the fields they carry. `_inputs` is the
    dispatch-inputs pseudo-node."""
    step_vars = {b.var for b in root.pipeline} | {d.var for d in root.derivations}
    input_names = {i.name for i in root.inputs}
    grouped: dict[tuple[str, str, str], list[str]] = {}

    def add(src: str, dst: str, kind: str, label: str) -> None:
        grouped.setdefault((src, dst, kind), []).append(label)

    for bind in root.pipeline:
        for input_name, ref in sorted(bind.kwargs.items()):
            if ref.path is None:
                continue
            root_name = ref.path.root
            if root_name in step_vars:
                add(root_name, bind.var, "data", f"{ref.path.dotted} → {input_name}")
            elif root_name in input_names:
                add("_inputs", bind.var, "data", f"{ref.path.dotted} → {input_name}")
        if bind.gate is not None and bind.gate.path is not None:
            gate_root = bind.gate.path.root
            if gate_root in step_vars:
                add(gate_root, bind.var, "gate", bind.gate_condition())
        fanout = bind.fanout
        if fanout is not None and fanout.source.path is not None:
            src_root = fanout.source.path.root
            if src_root in step_vars:
                label = f"each of {fanout.source.path.dotted}"
                if fanout.filter is not None:
                    label += f" where {fanout.filter.raw}"
                add(src_root, bind.var, "fanout", label)
    for derivation in root.derivations:
        for root_name in sorted(derivation.referenced_roots() & step_vars):
            add(root_name, derivation.var, "data", f"→ {derivation.var}")
    return [
        {"src": src, "dst": dst, "kind": kind, "labels": labels}
        for (src, dst, kind), labels in grouped.items()
    ]


# ---------------- lint ----------------


def _lint(module: SpecModule) -> list[dict[str, Any]]:
    """Diagnostics with a best-effort owner: the last task or schema that
    starts at or before the diagnostic's line (same file only)."""
    starts: list[tuple[int, str]] = sorted(
        [(t.loc.line, name) for name, t in module.tasks.items()]
        + [(s.loc.line, name) for name, s in module.schemas.items()]
    )
    out = []
    for diag in lint_module(module):
        owner = None
        if diag.file == module.path:
            for line, name in starts:
                if line <= diag.line:
                    owner = name
                else:
                    break
        out.append(
            {
                "code": diag.code,
                "message": diag.message,
                "severity": diag.severity,
                "line": diag.line,
                "owner": owner,
            }
        )
    return out
