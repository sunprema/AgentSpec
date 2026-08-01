"""Derive an ExecutionPlan from a SpecModule's orchestrators."""

from agentspec.parser import PipelineBind, SpecModule, TaskDef
from agentspec.plan.model import ExecutionPlan, StepPlan


def build_plans(module: SpecModule) -> list[ExecutionPlan]:
    return [build_plan(module, task) for task in module.tasks.values() if task.is_orchestrator]


def build_plan(module: SpecModule, task: TaskDef) -> ExecutionPlan:
    binds = task.pipeline
    var_set = {b.var for b in binds}

    deps: dict[str, list[str]] = {}
    wave_of: dict[str, int] = {}
    for bind in binds:
        bind_deps = sorted(bind.referenced_roots() & var_set)
        deps[bind.var] = bind_deps
        # Sequential class-body order means real deps are already computed;
        # forward/self references (lint AS008/AS009) are simply ignored here.
        known = [wave_of[d] for d in bind_deps if d in wave_of]
        wave_of[bind.var] = (max(known) + 1) if known else 0

    wave_count = max(wave_of.values(), default=-1) + 1
    waves: list[list[str]] = [[] for _ in range(wave_count)]
    for bind in binds:
        waves[wave_of[bind.var]].append(bind.var)

    steps = [_step_plan(module, bind, wave_of[bind.var], deps[bind.var]) for bind in binds]
    plan = ExecutionPlan(
        orchestrator=task.name,
        file=module.path,
        steps=steps,
        waves=waves,
        gate_skips=_gate_skips(module, task),
        warnings=_false_concurrency(steps, waves),
    )
    return plan


def _step_plan(
    module: SpecModule, bind: PipelineBind, wave: int, depends_on: list[str]
) -> StepPlan:
    target = module.tasks.get(bind.task)
    exclusive = [t.name for t in (target.tools if target else []) if t.exclusive]
    gate = None
    if bind.gate is not None:
        gate = bind.gate.path.dotted if bind.gate.path else bind.gate.raw
    fanout_over = filter_raw = None
    if bind.fanout is not None:
        source = bind.fanout.source
        fanout_over = source.path.dotted if source.path else source.raw
        if bind.fanout.filter is not None:
            filter_raw = bind.fanout.filter.raw
    return StepPlan(
        var=bind.var,
        task=bind.task,
        wave=wave,
        depends_on=depends_on,
        gate=gate,
        fanout_over=fanout_over,
        filter=filter_raw,
        serialized=bool(bind.fanout is not None and exclusive),
        exclusive_tools=exclusive,
    )


def _required_refs(module: SpecModule, bind: PipelineBind, var_set: set[str]) -> set[str]:
    """The prior vars this bind cannot run without. Kwargs feeding an input
    declared `X | None` are tolerant of a skipped producer and excluded."""
    loop_var = bind.fanout.loop_var if bind.fanout is not None else None
    target = module.tasks.get(bind.task)
    optional = {i.name for i in target.inputs if i.type.optional} if target is not None else set()
    refs: set[str] = set()
    for name, ref in bind.kwargs.items():
        if ref.path is not None and ref.path.root != loop_var and name not in optional:
            refs.add(ref.path.root)
    if bind.gate is not None and bind.gate.path is not None:
        refs.add(bind.gate.path.root)
    if bind.fanout is not None and bind.fanout.source.path is not None:
        refs.add(bind.fanout.source.path.root)
    return refs & var_set


def _gate_skips(module: SpecModule, task: TaskDef) -> dict[str, list[str]]:
    var_set = {b.var for b in task.pipeline}
    required = {b.var: _required_refs(module, b, var_set) for b in task.pipeline}
    order = [b.var for b in task.pipeline]
    skips: dict[str, list[str]] = {}
    for bind in task.pipeline:
        if bind.gate is None:
            continue
        skipped = {bind.var}
        changed = True
        while changed:
            changed = False
            for var in order:
                if var not in skipped and required[var] & skipped:
                    skipped.add(var)
                    changed = True
        skips[bind.var] = [v for v in order if v in skipped and v != bind.var]
    return skips


def _false_concurrency(steps: list[StepPlan], waves: list[list[str]]) -> list[str]:
    """Two steps in the same wave holding the same exclusive tool would race
    on the resource — sequencing that matters must appear as a bind."""
    by_var = {s.var: s for s in steps}
    warnings = []
    for index, wave in enumerate(waves):
        holders: dict[str, list[str]] = {}
        for var in wave:
            for tool in by_var[var].exclusive_tools:
                holders.setdefault(tool, []).append(var)
        for tool, vars_ in holders.items():
            if len(vars_) > 1:
                warnings.append(
                    f"steps {', '.join(vars_)} run concurrently in wave {index} "
                    f"but each holds exclusive tool '{tool}' — missing bind?"
                )
    return warnings
