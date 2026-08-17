"""Preview of the guarded prompt a task would receive — the exact
render_prompt() the runtime calls (spec §9's "pruned contract"), given
either recorded trace output or synthesized example inputs. Read-only: it
never runs a model and never mutates the spec."""

from typing import Any

from agentspec.eval import build_output_model, render_prompt
from agentspec.parser import PipelineBind, SpecModule, TaskDef, TaskInput, TypeExpr
from agentspec.run.single import has_declared_doubt


def render_task_prompt(
    module: SpecModule,
    task_name: str,
    *,
    dev_mode: bool = False,
    trace: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    """(prompt, used_trace). Raises KeyError for an unknown task, ValueError
    for the orchestrator itself (its reducer prompt combines every step's
    collected results — not previewable this way) or a task with no named
    returns schema."""
    task = module.tasks[task_name]
    if task.is_orchestrator:
        raise ValueError(
            f"'{task_name}' is the orchestrator — its reducer prompt combines "
            "every step's collected results, not previewable here"
        )
    if (
        task.returns is None
        or task.returns.kind != "name"
        or task.returns.name not in module.schemas
    ):
        raise ValueError(f"'{task_name}' has no named returns schema to render a prompt for")

    output_model = build_output_model(module, task.returns.name)
    bind, extra_rules = _pipeline_context(module, task_name)
    inputs, used_trace = _resolve_inputs(module, task, bind, trace)
    allow_question = dev_mode and has_declared_doubt(task)
    prompt = render_prompt(
        module, task, inputs, output_model, extra_rules=extra_rules, allow_question=allow_question
    )
    return prompt, used_trace


def _pipeline_context(
    module: SpecModule, task_name: str
) -> tuple[PipelineBind | None, list | None]:
    """The first orchestrator whose pipeline calls this task: (its bind for
    this step, the orchestrator's own constraints — inherited by every step,
    per spec §5). (None, None) if no orchestrator in this file calls it."""
    for candidate in module.tasks.values():
        if not candidate.is_orchestrator:
            continue
        bind = next((b for b in candidate.pipeline if b.task == task_name), None)
        if bind is not None:
            return bind, candidate.constraints
    return None, None


def _resolve_inputs(
    module: SpecModule, task: TaskDef, bind: PipelineBind | None, trace: dict[str, Any] | None
) -> tuple[dict[str, Any], bool]:
    steps_by_var = {}
    for step in (trace or {}).get("steps") or []:
        if isinstance(step, dict) and step.get("var"):
            steps_by_var[step["var"]] = step
    # Fan-out kwargs bind through the loop var, not a plain prior step — out
    # of scope for this preview; fall through to example values for those.
    usable_bind = bind if (bind is not None and bind.fanout is None) else None

    inputs: dict[str, Any] = {}
    used_trace = False
    for inp in task.inputs:
        value, from_trace = _resolve_one(module, inp, usable_bind, steps_by_var)
        inputs[inp.name] = value
        used_trace = used_trace or from_trace
    return inputs, used_trace


def _resolve_one(
    module: SpecModule,
    inp: TaskInput,
    bind: PipelineBind | None,
    steps_by_var: dict[str, dict[str, Any]],
) -> tuple[Any, bool]:
    ref = bind.kwargs.get(inp.name) if bind is not None else None
    if ref is not None:
        if ref.kind == "literal":
            return ref.value, False
        if ref.kind == "path" and ref.path is not None:
            parts = ref.path.parts
            step = steps_by_var.get(parts[0])
            if step is not None and step.get("output") is not None:
                value: Any = step["output"]
                for part in parts[1:]:
                    if isinstance(value, dict) and part in value:
                        value = value[part]
                    else:
                        value = None
                        break
                if value is not None:
                    return value, True
    return _example_value(module, inp.type, inp.name), False


def _example_value(
    module: SpecModule, type_expr: TypeExpr, name: str, ge: float | None = None
) -> Any:
    """A synthesized, obviously-a-placeholder value matching the declared
    type — enough to see the prompt's real shape without a live run."""
    if type_expr.kind == "enum" and type_expr.values:
        return type_expr.values[0]
    if type_expr.kind == "list":
        return [_example_value(module, type_expr.item, name)] if type_expr.item else []
    if type_expr.kind == "name":
        if type_expr.name == "str":
            return f"<{name}>"
        if type_expr.name == "int":
            return int(ge) if ge is not None else 0
        if type_expr.name == "float":
            return float(ge) if ge is not None else 0.0
        if type_expr.name == "bool":
            return False
        if type_expr.name in module.schemas:
            return {
                f.name: _example_value(module, f.type, f.name, f.ge)
                for f in module.schemas[type_expr.name].fields
            }
    return f"<{name}>"
