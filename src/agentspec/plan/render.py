"""Render an ExecutionPlan as wave-by-wave text."""

from agentspec.plan.model import ExecutionPlan, StepPlan


def render_text(plan: ExecutionPlan) -> str:
    lines = [
        f"{plan.orchestrator} — {len(plan.steps)} steps across "
        f"{len(plan.waves)} waves ({plan.file})"
    ]
    by_var = {s.var: s for s in plan.steps}
    for index, wave in enumerate(plan.waves):
        lines.append(f"wave {index}:")
        for var in wave:
            lines.append(f"  {_render_step(by_var[var])}")
    gated = {var: skips for var, skips in plan.gate_skips.items() if skips}
    if gated:
        lines.append("false gates skip:")
        for var, skips in gated.items():
            gate = by_var[var].gate
            lines.append(f"  {var} [{gate}] → {', '.join(skips)}")
    if plan.warnings:
        lines.append("warnings:")
        lines.extend(f"  {warning}" for warning in plan.warnings)
    return "\n".join(lines)


def _render_step(step: StepPlan) -> str:
    parts = [f"{step.var} = {step.task}"]
    if step.fanout_over is not None:
        note = f"fan-out over {step.fanout_over}"
        if step.filter is not None:
            note += f" if {step.filter}"
        if step.serialized:
            note += "; serialized per item"
        parts.append(f"[{note}]")
    if step.gate is not None:
        parts.append(f"[gate: {step.gate}]")
    if len(step.depends_on) > 1:
        parts.append(f"[after: {', '.join(step.depends_on)}]")
    return "  ".join(parts)
