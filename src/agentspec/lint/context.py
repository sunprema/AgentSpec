"""Name and type resolution inside an orchestrator's pipeline (spec §7, §10)."""

from __future__ import annotations

from dataclasses import dataclass

from agentspec.parser import AttrPath, FanOut, SpecModule, TaskDef, TypeExpr

ENV_FIELDS = {"now", "cwd", "user", "platform", "run_id"}
BUILTIN_TYPES = {"str", "int", "float", "bool"}


class ResolutionError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnknownName(ResolutionError):
    """Root is not an input, a prior step, env, or the loop variable (AS008)."""


class BoundLater(ResolutionError):
    """Root is a pipeline variable that does not exist yet (AS008)."""


class NoSuchField(ResolutionError):
    """A path component does not exist on the source type (AS022)."""


@dataclass
class Resolved:
    type: TypeExpr | None  # None: resolution gave up (unknown named type)
    listy: bool = False  # a fan-out's collected results

    @property
    def is_list(self) -> bool:
        return self.listy or (self.type is not None and self.type.kind == "list")

    def item_type(self) -> TypeExpr | None:
        if self.type is None:
            return None
        if self.type.kind == "list":
            return self.type.item
        if self.listy:
            return self.type
        return None


class Scope:
    """Resolves attribute paths referenced by the binds of one orchestrator."""

    def __init__(self, module: SpecModule, task: TaskDef) -> None:
        self.module = module
        self.task = task
        self.inputs = {i.name: i.type for i in task.inputs}
        self.bind_index = {b.var: i for i, b in enumerate(task.pipeline)}
        self.derivations = {d.var: d for d in task.derivations}

    def is_prior_bind(self, name: str, at: int) -> bool:
        return name in self.bind_index and self.bind_index[name] < at

    def resolve(self, path: AttrPath, *, at: int, fanout: FanOut | None = None) -> Resolved:
        """Resolve a path referenced by the bind at pipeline index `at`."""
        root, rest = path.parts[0], path.parts[1:]
        if fanout is not None and root == fanout.loop_var:
            return self._walk(self._fanout_item(fanout, at), rest, listy=False)
        if root == "env":
            if len(rest) != 1 or rest[0] not in ENV_FIELDS:
                raise NoSuchField(
                    f"'{path.dotted}' does not exist: env is a closed namespace "
                    f"({', '.join(sorted(ENV_FIELDS))})"
                )
            return Resolved(TypeExpr(kind="name", name="str"))
        if root in self.inputs:
            return self._walk(self.inputs[root], rest, listy=False)
        if root in self.derivations:
            derivation = self.derivations[root]
            fields = {field for row in derivation.rows for field in row.output}
            if rest and rest[0] not in fields:
                raise NoSuchField(
                    f"derivation '{root}' has no field '{rest[0]}' "
                    f"(fields: {', '.join(sorted(fields))})"
                )
            # field types are inferred from row literals downstream; the
            # walk stops here with an open type
            return Resolved(None, False)
        if root in self.bind_index:
            index = self.bind_index[root]
            if index >= at:
                raise BoundLater(
                    f"'{root}' is not bound until later in the pipeline — "
                    "a name must exist before it is referenced"
                )
            bind = self.task.pipeline[index]
            target = self.module.tasks.get(bind.task)
            returns = target.returns if target is not None else None
            return self._walk(returns, rest, listy=bind.fanout is not None)
        raise UnknownName(f"'{root}' is not an input, a prior step, env, or the loop variable")

    def _fanout_item(self, fanout: FanOut, at: int) -> TypeExpr | None:
        if fanout.source.path is None:
            return None
        return self.resolve(fanout.source.path, at=at).item_type()

    def _walk(self, current: TypeExpr | None, rest: list[str], *, listy: bool) -> Resolved:
        for part in rest:
            if current is None:
                return Resolved(None, listy)
            if current.kind == "list":
                listy, current = True, current.item
                if current is None:
                    return Resolved(None, listy)
            if current.kind == "enum" or (current.kind == "name" and current.name in BUILTIN_TYPES):
                label = "a closed enum" if current.kind == "enum" else f"'{current.name}'"
                raise NoSuchField(f"cannot access '.{part}' on {label}")
            if current.kind == "name" and current.name in self.module.schemas:
                field = self.module.schemas[current.name].field(part)
                if field is None:
                    raise NoSuchField(f"schema '{current.name}' has no field '{part}'")
                current = field.type
            else:
                return Resolved(None, listy)  # unknown named type: give up quietly
        return Resolved(current, listy)


def compatible(
    module: SpecModule, produced: TypeExpr | None, consumed: TypeExpr | None
) -> bool | None:
    """Whether a produced type satisfies a consumed input. None = cannot judge."""
    if produced is None or consumed is None:
        return None
    if produced.kind == "unknown" or consumed.kind == "unknown":
        return None
    if consumed.kind == "list" or produced.kind == "list":
        if produced.kind != consumed.kind:
            return False
        return compatible(module, produced.item, consumed.item)
    if consumed.kind == "enum":
        if produced.kind == "enum":
            return set(produced.values) <= set(consumed.values)
        return None  # str into a closed set is not statically checkable
    # consumed is a named type
    if produced.kind == "enum":
        if consumed.name == "str":
            return True  # an Enum may widen safely into str (spec §7)
        if consumed.name in BUILTIN_TYPES or consumed.name in module.schemas:
            return False
        return None
    if produced.name == consumed.name:
        return True
    if produced.name == "int" and consumed.name == "float":
        return True
    known_p = produced.name in BUILTIN_TYPES or produced.name in module.schemas
    known_c = consumed.name in BUILTIN_TYPES or consumed.name in module.schemas
    return False if (known_p and known_c) else None


def literal_compatible(value: object, consumed: TypeExpr) -> bool | None:
    """Whether a literal kwarg satisfies a consumed input. None = cannot judge."""
    if value is None:
        return consumed.optional
    if consumed.kind == "enum":
        return value in consumed.values
    if consumed.kind == "list":
        return isinstance(value, list)
    if consumed.kind == "name":
        if consumed.name == "bool":
            return isinstance(value, bool)
        if consumed.name == "int":
            return isinstance(value, int) and not isinstance(value, bool)
        if consumed.name == "float":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if consumed.name == "str":
            return isinstance(value, str)
    return None
