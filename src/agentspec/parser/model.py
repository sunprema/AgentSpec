"""The SpecModel: the typed, in-memory form of a parsed `.aspec.py` file.

Every later tool (lint, plan, graph, fmt) consumes this model; nothing
outside the parser touches raw AST.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from agentspec.diagnostics import Diagnostic


class SourceLoc(BaseModel):
    file: str
    line: int = 0
    col: int = 0


class TypeExpr(BaseModel):
    """A type annotation: a named type, a closed Enum/Literal set, or a list."""

    kind: Literal["name", "enum", "list", "unknown"] = "name"
    name: str | None = None  # kind == "name"
    values: list[Any] = Field(default_factory=list)  # kind == "enum"
    item: "TypeExpr | None" = None  # kind == "list"
    optional: bool = False  # `X | None`
    raw: str | None = None  # kind == "unknown"

    def render(self) -> str:
        if self.kind == "enum":
            base = "Enum[" + ", ".join(repr(v) for v in self.values) + "]"
        elif self.kind == "list":
            base = f"list[{self.item.render() if self.item else '?'}]"
        elif self.kind == "name":
            base = self.name or "?"
        else:
            base = self.raw or "?"
        return base + (" | None" if self.optional else "")


class SchemaField(BaseModel):
    name: str
    type: TypeExpr
    # Field(...) bounds are part of the contract (spec §3).
    ge: float | None = None
    le: float | None = None
    max_length: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    has_default: bool = False
    default: Any = None
    loc: SourceLoc


class SchemaDef(BaseModel):
    name: str
    docstring: str | None = None
    fields: list[SchemaField] = Field(default_factory=list)
    loc: SourceLoc

    def field(self, name: str) -> SchemaField | None:
        return next((f for f in self.fields if f.name == name), None)


class Rule(BaseModel):
    """One constraint: Rule("id", "text", why=..., severity=..., since=...)
    (spec §5). The id is the rule's reviewer-facing name and the anchor for
    diff, lint, and reports."""

    id: str
    text: str
    why: str | None = None
    severity: Literal["must", "should", "may"] = "must"
    since: str | None = None  # provenance: spec revision / incident
    source: str | None = None  # constant name it was composed from, if any
    loc: SourceLoc


RISK_RANK: dict[str, int] = {"read": 0, "mutate": 1, "irreversible": 2}
DEFAULT_RISK = "mutate"  # an untagged op is assumed to mutate (conservative)


class ToolDecl(BaseModel):
    name: str
    ops: list[str] = Field(default_factory=list)
    # spec 2.4: op → declared risk ("read" | "mutate" | "irreversible").
    # Only explicitly tagged ops appear here; risk_of() applies the default.
    op_risks: dict[str, str] = Field(default_factory=dict)
    scripts: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    strict: bool = False
    exclusive: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)
    loc: SourceLoc

    def risk_of(self, op: str) -> str:
        return self.op_risks.get(op, DEFAULT_RISK)

    def max_risk(self) -> str:
        """The tool's highest op risk; an unrestricted surface (no ops) is
        assumed to mutate."""
        if not self.ops:
            return DEFAULT_RISK
        return max((self.risk_of(op) for op in self.ops), key=lambda r: RISK_RANK[r])

    def op_display(self) -> list[str]:
        """Op names, annotated with explicitly declared risk."""
        return [f"{op} ({self.op_risks[op]})" if op in self.op_risks else op for op in self.ops]


class FailurePolicy(BaseModel):
    """on_failure / on_uncertain-adjacent declarations (spec §8)."""

    kind: Literal["abort", "literal", "retry", "escalate"]
    literal: Any = None  # kind == "literal"
    max: int | None = None  # kind == "retry"
    backoff_s: float | None = None  # kind == "retry"
    channel: str | None = None  # kind == "escalate"
    timeout_s: float | None = None  # kind == "escalate"
    then: "FailurePolicy | None" = None  # retry/escalate continuation
    loc: SourceLoc


class AttrPath(BaseModel):
    """A dotted reference: an input, a prior step's field, a loop var, or env."""

    parts: list[str]

    @property
    def root(self) -> str:
        return self.parts[0]

    @property
    def dotted(self) -> str:
        return ".".join(self.parts)


class ValueRef(BaseModel):
    """A value in a pipeline bind: an attribute path or a literal."""

    kind: Literal["path", "literal", "invalid"]
    path: AttrPath | None = None
    value: Any = None
    raw: str
    loc: SourceLoc


class Filter(BaseModel):
    """A fan-out filter, caged to `path in [literals]` or `path == literal`."""

    valid: bool
    path: AttrPath | None = None
    op: Literal["in", "=="] | None = None
    values: list[Any] = Field(default_factory=list)
    raw: str


class FanOut(BaseModel):
    loop_var: str
    source: ValueRef
    filter: Filter | None = None


class PipelineBind(BaseModel):
    """One class-body assignment in an orchestrator: `var = TaskName(...)`,
    optionally gated (`... if cond else None`) or fanned out (a comprehension)."""

    var: str
    task: str
    kwargs: dict[str, ValueRef] = Field(default_factory=dict)
    gate: ValueRef | None = None
    gate_negated: bool = False  # `if not cond` — runs when the field is false
    fanout: FanOut | None = None
    raw: str
    loc: SourceLoc

    def gate_condition(self) -> str | None:
        """The gate as display text: `issue.proceed` or `not build.built`."""
        if self.gate is None:
            return None
        base = self.gate.path.dotted if self.gate.path else self.gate.raw
        return f"not {base}" if self.gate_negated else base

    def referenced_roots(self) -> set[str]:
        """Roots of every attribute path this bind references (its own
        fan-out loop variable excluded)."""
        loop_var = self.fanout.loop_var if self.fanout is not None else None
        roots = {
            ref.path.root
            for ref in self.kwargs.values()
            if ref.path is not None and ref.path.root != loop_var
        }
        if self.gate is not None and self.gate.path is not None:
            roots.add(self.gate.path.root)
        if self.fanout is not None:
            if self.fanout.source.path is not None:
                roots.add(self.fanout.source.path.root)
            filt = self.fanout.filter
            if filt is not None and filt.path is not None and filt.path.root != loop_var:
                roots.add(filt.path.root)
        return roots


class DerivationAtom(BaseModel):
    """One caged condition atom: a boolean path, its negation, or a
    comparison against a literal."""

    path: AttrPath
    negated: bool = False
    op: Literal["==", "!=", ">", ">=", "<", "<=", "in"] | None = None
    value: Any = None


class DerivationRow(BaseModel):
    """`(condition, {field: literal-or-path})` — one row of a Cond.
    Empty atoms = the `(True, ...)` catch-all."""

    atoms: list[DerivationAtom] = Field(default_factory=list)
    raw: str  # the condition's source text ("True" for the catch-all)
    output: dict[str, ValueRef] = Field(default_factory=dict)
    loc: SourceLoc

    @property
    def is_catch_all(self) -> bool:
        return not self.atoms


class DerivationBind(BaseModel):
    """A mechanically-evaluated pipeline value (spec 2.2): first row whose
    condition holds wins, top to bottom. Always yields a value — skips do
    not propagate into or through a derivation.

    style="outcomes" (spec 2.6): the bind was declared as a list of
    Outcome(...) entries — a specialized derivation whose rows all carry
    an `outcome` (the ending's name) and an `alert` (boolean) field."""

    var: str
    rows: list[DerivationRow] = Field(default_factory=list)
    style: Literal["cond", "outcomes"] = "cond"
    raw: str
    loc: SourceLoc

    @property
    def has_catch_all(self) -> bool:
        return any(row.is_catch_all for row in self.rows)

    def referenced_roots(self) -> set[str]:
        roots = set()
        for row in self.rows:
            for atom in row.atoms:
                roots.add(atom.path.root)
            for ref in row.output.values():
                if ref.path is not None:
                    roots.add(ref.path.root)
        return roots


class TaskInput(BaseModel):
    name: str
    type: TypeExpr
    loc: SourceLoc


class UnknownAttr(BaseModel):
    """A non-reserved assignment that is not a pipeline bind (lint fodder)."""

    name: str
    raw: str
    loc: SourceLoc


class TaskDef(BaseModel):
    name: str
    docstring: str | None = None
    inputs: list[TaskInput] = Field(default_factory=list)
    returns: TypeExpr | None = None
    tools: list[ToolDecl] = Field(default_factory=list)
    constraints: list[Rule] = Field(default_factory=list)
    undo: str | None = None
    on_uncertain: dict[str, Any] | None = None
    on_failure: FailurePolicy | None = None
    on_item_failure: str | None = None
    meta: dict[str, Any] | None = None
    pipeline: list[PipelineBind] = Field(default_factory=list)
    derivations: list[DerivationBind] = Field(default_factory=list)
    unknown_attrs: list[UnknownAttr] = Field(default_factory=list)
    loc: SourceLoc

    @property
    def is_orchestrator(self) -> bool:
        return bool(self.pipeline)

    def bind(self, var: str) -> PipelineBind | None:
        return next((b for b in self.pipeline if b.var == var), None)

    def derivation(self, var: str) -> DerivationBind | None:
        return next((d for d in self.derivations if d.var == var), None)


class SpecConstant(BaseModel):
    """A module-level constant: a literal value, a rule list, or both readings."""

    name: str
    value: Any = None
    rules: list[Rule] | None = None
    loc: SourceLoc


class ImportRecord(BaseModel):
    module: str
    names: list[str] = Field(default_factory=list)
    kind: Literal["framework", "spec"]
    resolved_path: str | None = None
    loc: SourceLoc


class SpecModule(BaseModel):
    path: str
    docstring: str | None = None
    imports: list[ImportRecord] = Field(default_factory=list)
    constants: dict[str, SpecConstant] = Field(default_factory=dict)
    schemas: dict[str, SchemaDef] = Field(default_factory=dict)
    tasks: dict[str, TaskDef] = Field(default_factory=dict)
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def root_tasks(self) -> list[str]:
        """Tasks no other task references (spec §9: the root task)."""
        referenced = {b.task for t in self.tasks.values() for b in t.pipeline}
        return [name for name in self.tasks if name not in referenced]

    @property
    def root_task(self) -> str | None:
        roots = self.root_tasks()
        return roots[0] if len(roots) == 1 else None


TypeExpr.model_rebuild()
FailurePolicy.model_rebuild()
