"""Render a SpecModel's pipeline as a Mermaid flowchart.

Visual grammar:
- rectangle           a step (`var = Task`)
- double rectangle    a fan-out (`var = Task ×N`), source edge labeled
                      "each of <path> [where <filter>]"
- dashed labeled edge a gate (the condition text is the label)
- solid edge          a data dependency (a join is just several of them)
- `--failures` layer  abort steps get a dashed edge into a shared saga-unwind
                      node; retry/escalate/default become node label lines,
                      plus an "undo declared" marker
"""

from pathlib import Path

from agentspec.parser import FailurePolicy, SpecModule, TaskDef

MERMAID_RESERVED = {
    "class",
    "classDef",
    "click",
    "direction",
    "end",
    "graph",
    "linkStyle",
    "style",
    "subgraph",
}


def _node(var: str) -> str:
    return var + "_" if var in MERMAID_RESERVED else var


def _esc(label: str) -> str:
    return label.replace('"', "#quot;")


def render_mermaid(module: SpecModule, task: TaskDef, *, failures: bool = False) -> str:
    binds = task.pipeline
    var_set = {b.var for b in binds}
    input_names = {i.name for i in task.inputs}
    lines = ["flowchart TD"]

    consumed_inputs = sorted(
        {
            ref.path.root
            for bind in binds
            for ref in bind.kwargs.values()
            if ref.path is not None and ref.path.root in input_names
        }
    )
    if consumed_inputs:
        lines.append(f'    _inputs(["inputs: {", ".join(consumed_inputs)}"])')

    for bind in binds:
        target = module.tasks.get(bind.task)
        label = f"{bind.var} = {bind.task}"
        if bind.fanout is not None:
            label += " ×N"
            if target is not None and any(t.exclusive for t in target.tools):
                label += "<br/>serialized per item"
        if failures and target is not None:
            label += "".join(f"<br/>{note}" for note in _failure_notes(target))
        open_, close = ('[["', '"]]') if bind.fanout is not None else ('["', '"]')
        lines.append(f"    {_node(bind.var)}{open_}{_esc(label)}{close}")

    uses_unwind = False
    for bind in binds:
        gate_root = bind.gate.path.root if bind.gate is not None and bind.gate.path else None
        fanout = bind.fanout
        fan_root = fanout.source.path.root if fanout is not None and fanout.source.path else None
        if any(
            ref.path is not None and ref.path.root in input_names for ref in bind.kwargs.values()
        ):
            lines.append(f"    _inputs --> {_node(bind.var)}")
        for dep in sorted(bind.referenced_roots() & var_set):
            if dep == gate_root:
                assert bind.gate is not None and bind.gate.path is not None
                lines.append(
                    f'    {_node(dep)} -. "{_esc(bind.gate.path.dotted)}" .-> {_node(bind.var)}'
                )
            elif dep == fan_root:
                assert fanout is not None and fanout.source.path is not None
                edge = f"each of {fanout.source.path.dotted}"
                if fanout.filter is not None:
                    edge += f" where {fanout.filter.raw}"
                lines.append(f'    {_node(dep)} -- "{_esc(edge)}" --> {_node(bind.var)}')
            else:
                lines.append(f"    {_node(dep)} --> {_node(bind.var)}")
        target = module.tasks.get(bind.task)
        if (
            failures
            and target is not None
            and target.on_failure is not None
            and target.on_failure.kind == "abort"
        ):
            uses_unwind = True
            lines.append(f'    {_node(bind.var)} -. "on failure: abort" .-> _unwind')
    if uses_unwind:
        lines.append('    _unwind[/"saga unwind: undo in reverse order"/]')
    return "\n".join(lines)


def _failure_notes(task: TaskDef) -> list[str]:
    notes = []
    policy = task.on_failure
    if policy is not None and policy.kind != "abort":
        notes.append(f"on failure: {_policy_desc(policy)}")
    if task.undo:
        notes.append("undo declared")
    return notes


def _policy_desc(policy: FailurePolicy) -> str:
    if policy.kind == "abort":
        return "abort"
    if policy.kind == "literal":
        return "declared default"
    then = f" then {_policy_desc(policy.then)}" if policy.then is not None else ""
    if policy.kind == "retry":
        return f"retry ×{policy.max}{then}"
    return f"escalate '{policy.channel}'{then}"


def render_markdown(module: SpecModule, *, failures: bool = False) -> str:
    """A review-ready markdown document: one Mermaid block per orchestrator."""
    name = Path(module.path).name
    parts = [f"# Execution graph — {name}"]
    orchestrators = [t for t in module.tasks.values() if t.is_orchestrator]
    if not orchestrators:
        parts.append("_No orchestrator — nothing to graph._")
    for task in orchestrators:
        parts.append(
            f"## {task.name}\n\n```mermaid\n{render_mermaid(module, task, failures=failures)}\n```"
        )
    return "\n\n".join(parts) + "\n"
