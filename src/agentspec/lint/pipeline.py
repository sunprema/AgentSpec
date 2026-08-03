"""Pipeline checks over each orchestrator: AS006 filter cage, AS007 undefined
tasks, AS008 unbound names, AS009 cycles, AS011 gates, AS020/AS021/AS022
type flow across every bind."""

from collections.abc import Iterator

from agentspec.diagnostics import Diagnostic
from agentspec.lint.catalog import mk
from agentspec.lint.context import (
    BoundLater,
    NoSuchField,
    Resolved,
    Scope,
    UnknownName,
    compatible,
    literal_compatible,
)
from agentspec.parser import (
    AttrPath,
    FanOut,
    PipelineBind,
    SourceLoc,
    SpecModule,
    TaskDef,
    TypeExpr,
)


def check_pipelines(module: SpecModule) -> Iterator[Diagnostic]:
    for task in module.tasks.values():
        if not task.pipeline:
            continue
        scope = Scope(module, task)
        for index, bind in enumerate(task.pipeline):
            yield from _check_bind(module, scope, index, bind)
        yield from _check_cycles(task)


def _resolve(
    scope: Scope,
    path: AttrPath,
    at: int,
    fanout: FanOut | None,
    loc: SourceLoc,
) -> tuple[Resolved | None, Diagnostic | None]:
    try:
        return scope.resolve(path, at=at, fanout=fanout), None
    except (UnknownName, BoundLater) as exc:
        return None, mk("AS008", exc.message, loc)
    except NoSuchField as exc:
        return None, mk("AS022", exc.message, loc)


def _check_bind(
    module: SpecModule, scope: Scope, index: int, bind: PipelineBind
) -> Iterator[Diagnostic]:
    target = module.tasks.get(bind.task)
    if target is None:
        yield mk(
            "AS007",
            f"'{bind.var}' references undefined task '{bind.task}'",
            bind.loc,
        )
    if bind.gate is not None:
        yield from _check_gate(scope, index, bind)
    if bind.fanout is not None:
        yield from _check_fanout(scope, index, bind)
    if target is not None:
        yield from _check_kwargs(module, scope, index, bind, target)


def _check_derived_gate(scope: Scope, bind: PipelineBind, path: AttrPath) -> Iterator[Diagnostic]:
    """A gate may reference a derivation field when every row yields a
    boolean literal for it — a derived go/no-go is deterministic."""
    derivation = scope.derivations[path.root]
    if len(path.parts) != 2:
        yield mk(
            "AS011",
            f"gate on '{bind.var}' must be a single field of derivation "
            f"'{path.root}', got '{path.dotted}'",
            bind.gate.loc if bind.gate else bind.loc,
        )
        return
    field = path.parts[1]
    values = [row.output.get(field) for row in derivation.rows]
    if any(v is None for v in values) or not all(
        v is not None and v.path is None and isinstance(v.value, bool) for v in values
    ):
        yield mk(
            "AS011",
            f"gate on '{bind.var}': derivation field '{path.dotted}' must be "
            "a boolean literal in every row",
            bind.gate.loc if bind.gate else bind.loc,
        )


def _check_gate(scope: Scope, index: int, bind: PipelineBind) -> Iterator[Diagnostic]:
    gate = bind.gate
    assert gate is not None
    if gate.kind != "path" or gate.path is None:
        yield mk(
            "AS011",
            f"gate on '{bind.var}' must be a boolean result field of a prior "
            f"step, got '{gate.raw}'",
            gate.loc,
        )
        return
    if gate.path.root in scope.derivations:
        if not scope.is_prior_derivation(gate.path.root, index):
            yield mk(
                "AS011",
                f"gate on '{bind.var}' must reference a prior derivation, got '{gate.raw}'",
                gate.loc,
            )
            return
        yield from _check_derived_gate(scope, bind, gate.path)
        return
    if not scope.is_prior_bind(gate.path.root, index):
        yield mk(
            "AS011",
            f"gate on '{bind.var}' must reference a prior step's result, got '{gate.raw}'",
            gate.loc,
        )
        return
    resolved, diag = _resolve(scope, gate.path, index, None, gate.loc)
    if diag is not None:
        yield diag
        return
    assert resolved is not None
    if resolved.type is None:
        return
    if resolved.is_list or not (resolved.type.kind == "name" and resolved.type.name == "bool"):
        yield mk(
            "AS011",
            f"gate '{gate.path.dotted}' on '{bind.var}' is not a boolean field",
            gate.loc,
        )


def _check_fanout(scope: Scope, index: int, bind: PipelineBind) -> Iterator[Diagnostic]:
    fanout = bind.fanout
    assert fanout is not None
    if fanout.source.kind != "path" or fanout.source.path is None:
        yield mk(
            "AS021",
            f"fan-out '{bind.var}' must iterate a list-valued path, got '{fanout.source.raw}'",
            fanout.source.loc,
        )
    else:
        resolved, diag = _resolve(scope, fanout.source.path, index, None, fanout.source.loc)
        if diag is not None:
            yield diag
        elif resolved is not None and resolved.type is not None and not resolved.is_list:
            yield mk(
                "AS021",
                f"fan-out '{bind.var}' iterates '{fanout.source.path.dotted}', which is not a list",
                fanout.source.loc,
            )
    if fanout.filter is not None:
        if not fanout.filter.valid:
            yield mk(
                "AS006",
                f"filter on '{bind.var}' must be 'path in [literals]' or "
                f"'path == literal', got '{fanout.filter.raw}' — anything richer "
                "is judgment and belongs in a rule",
                bind.loc,
            )
        elif fanout.filter.path is not None:
            _resolved, diag = _resolve(scope, fanout.filter.path, index, fanout, bind.loc)
            if diag is not None:
                yield diag


def _check_kwargs(
    module: SpecModule,
    scope: Scope,
    index: int,
    bind: PipelineBind,
    target: TaskDef,
) -> Iterator[Diagnostic]:
    inputs = {i.name: i for i in target.inputs}
    missing = [i.name for i in target.inputs if i.name not in bind.kwargs and not i.type.optional]
    if missing:
        yield mk(
            "AS020",
            f"bind '{bind.var}' does not supply input(s) "
            f"{', '.join(missing)} of task '{target.name}'",
            bind.loc,
        )
    for name, ref in bind.kwargs.items():
        input_ = inputs.get(name)
        if input_ is None:
            yield mk(
                "AS020",
                f"task '{target.name}' has no input '{name}'",
                ref.loc,
            )
            continue
        if ref.kind == "invalid":
            yield mk(
                "AS020",
                f"value for '{name}' must be a simple attribute path or a literal, got '{ref.raw}'",
                ref.loc,
            )
            continue
        if ref.kind == "literal":
            if literal_compatible(ref.value, input_.type) is False:
                yield mk(
                    "AS020",
                    f"literal {ref.raw} does not satisfy input '{name}' of task '{target.name}'",
                    ref.loc,
                )
            continue
        assert ref.path is not None
        resolved, diag = _resolve(scope, ref.path, index, bind.fanout, ref.loc)
        if diag is not None:
            yield diag
            continue
        assert resolved is not None
        if resolved.type is None:
            continue
        if resolved.is_list != (input_.type.kind == "list"):
            shape = "a list" if resolved.is_list else "not a list"
            yield mk(
                "AS021",
                f"'{ref.path.dotted}' is {shape} but input '{name}' of "
                f"task '{target.name}' expects the opposite",
                ref.loc,
            )
            continue
        if resolved.listy:
            verdict = compatible(module, resolved.item_type(), input_.type.item)
        else:
            verdict = compatible(module, resolved.type, input_.type)
        if verdict is False:
            yield mk(
                "AS020",
                f"'{ref.path.dotted}' ({_describe(resolved.type)}) does not "
                f"match input '{name}' of task '{target.name}' "
                f"({_describe(input_.type)})",
                ref.loc,
            )


def _describe(type_expr: TypeExpr | None) -> str:
    if type_expr is None:
        return "unknown"
    if type_expr.kind == "enum":
        return "Enum[" + ", ".join(repr(v) for v in type_expr.values) + "]"
    if type_expr.kind == "list":
        return f"list[{_describe(type_expr.item)}]"
    return type_expr.name or type_expr.kind


def _check_cycles(task: TaskDef) -> Iterator[Diagnostic]:
    items = list(task.pipeline) + list(task.derivations)
    var_set = {item.var for item in items}
    loc_by_var = {item.var: item.loc for item in items}
    graph: dict[str, set[str]] = {item.var: item.referenced_roots() & var_set for item in items}

    color = dict.fromkeys(graph, 0)  # 0 white, 1 gray, 2 black
    path: list[str] = []
    cycles: list[list[str]] = []

    def dfs(node: str) -> None:
        color[node] = 1
        path.append(node)
        for neighbor in sorted(graph[node]):
            if color[neighbor] == 1:
                cycles.append(path[path.index(neighbor) :] + [neighbor])
            elif color[neighbor] == 0:
                dfs(neighbor)
        path.pop()
        color[node] = 2

    for node in graph:
        if color[node] == 0:
            dfs(node)
    for cycle in cycles:
        yield mk("AS009", f"dependency cycle: {' -> '.join(cycle)}", loc_by_var[cycle[0]])
