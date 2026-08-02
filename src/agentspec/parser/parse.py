"""AST → SpecModel. Specs are parsed as data; they are never imported or executed."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from agentspec.diagnostics import Diagnostic
from agentspec.parser.model import (
    AttrPath,
    DerivationAtom,
    DerivationBind,
    DerivationRow,
    FailurePolicy,
    FanOut,
    Filter,
    ImportRecord,
    PipelineBind,
    Rule,
    SchemaDef,
    SchemaField,
    SourceLoc,
    SpecConstant,
    SpecModule,
    TaskDef,
    TaskInput,
    ToolDecl,
    TypeExpr,
    UnknownAttr,
    ValueRef,
)

FRAMEWORK_MODULES = {"agentspec", "pydantic", "typing", "__future__"}
RESERVED_ATTRS = {
    "tools",
    "constraints",
    "undo",
    "on_uncertain",
    "on_failure",
    "on_item_failure",
    "meta",
}
SEVERITIES = {"must", "should", "may"}
ITEM_FAILURE_MODES = {"skip_and_report", "abort"}
ENUM_BASES = {"Enum", "Literal"}


class _NotLiteral(Exception):
    """The expression cannot be evaluated as a spec literal."""


def parse_file(
    path: str | Path,
    *,
    cache: dict[Path, SpecModule] | None = None,
    _stack: frozenset[Path] = frozenset(),
) -> SpecModule:
    """Parse a `.aspec.py` file, resolving sibling-spec imports statically."""
    resolved = Path(path).resolve()
    if cache is None:
        cache = {}
    if resolved in cache:
        return cache[resolved]
    module = _Parser(
        source=resolved.read_text(),
        filename=str(resolved),
        base_dir=resolved.parent,
        cache=cache,
        stack=_stack | {resolved},
    ).parse()
    cache[resolved] = module
    return module


def parse_source(
    source: str,
    filename: str = "<spec>",
    *,
    base_dir: str | Path | None = None,
) -> SpecModule:
    """Parse spec source text (imports resolve relative to `base_dir`)."""
    return _Parser(
        source=source,
        filename=filename,
        base_dir=Path(base_dir) if base_dir else Path.cwd(),
        cache={},
        stack=frozenset(),
    ).parse()


class _Parser:
    def __init__(
        self,
        source: str,
        filename: str,
        base_dir: Path,
        cache: dict[Path, SpecModule],
        stack: frozenset[Path],
    ) -> None:
        self.source = source
        self.filename = filename
        self.base_dir = base_dir
        self.cache = cache
        self.stack = stack
        self.module = SpecModule(path=filename)

    # -- infrastructure -----------------------------------------------------

    def _diag(
        self,
        code: str,
        message: str,
        node: ast.AST | None = None,
        severity: str = "error",
    ) -> None:
        self.module.diagnostics.append(
            Diagnostic(
                code=code,
                message=message,
                file=self.filename,
                line=getattr(node, "lineno", 0) or 0,
                col=getattr(node, "col_offset", 0) or 0,
                severity=severity,  # type: ignore[arg-type]
            )
        )

    def _loc(self, node: ast.AST) -> SourceLoc:
        return SourceLoc(
            file=self.filename,
            line=getattr(node, "lineno", 0) or 0,
            col=getattr(node, "col_offset", 0) or 0,
        )

    # -- module level -------------------------------------------------------

    def parse(self) -> SpecModule:
        try:
            tree = ast.parse(self.source, self.filename)
        except SyntaxError as exc:
            self.module.diagnostics.append(
                Diagnostic(
                    code="P001",
                    message=f"syntax error: {exc.msg}",
                    file=self.filename,
                    line=exc.lineno or 0,
                    col=exc.offset or 0,
                )
            )
            return self.module

        self.module.docstring = ast.get_docstring(tree)
        for index, stmt in enumerate(tree.body):
            if (
                index == 0
                and isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                continue  # module docstring
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                self._import(stmt)
            elif isinstance(stmt, ast.Assign):
                self._constant(stmt)
            elif isinstance(stmt, ast.ClassDef):
                self._class(stmt)
            else:
                self._diag(
                    "P002",
                    "only a docstring, imports, constants, and class definitions "
                    "are allowed at module level",
                    stmt,
                )
        return self.module

    def _import(self, stmt: ast.Import | ast.ImportFrom) -> None:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                top = alias.name.split(".")[0]
                if top in FRAMEWORK_MODULES:
                    self.module.imports.append(
                        ImportRecord(module=alias.name, kind="framework", loc=self._loc(stmt))
                    )
                else:
                    self._diag(
                        "P006",
                        f"import of '{alias.name}' is not allowed; specs may import "
                        "only agentspec, pydantic, typing, and sibling .aspec.py files",
                        stmt,
                    )
            return

        module_name = stmt.module or ""
        names = [alias.name for alias in stmt.names]
        if module_name.split(".")[0] in FRAMEWORK_MODULES:
            self.module.imports.append(
                ImportRecord(module=module_name, names=names, kind="framework", loc=self._loc(stmt))
            )
            return

        spec_path = self.base_dir / f"{module_name}.aspec.py"
        if not spec_path.exists():
            self._diag(
                "P006",
                f"cannot resolve import '{module_name}': no {spec_path.name} next to this spec",
                stmt,
            )
            return
        resolved = spec_path.resolve()
        if resolved in self.stack:
            self._diag("P012", f"import cycle involving '{module_name}'", stmt)
            return
        imported = parse_file(resolved, cache=self.cache, _stack=self.stack)
        self.module.diagnostics.extend(imported.diagnostics)
        self.module.imports.append(
            ImportRecord(
                module=module_name,
                names=names,
                kind="spec",
                resolved_path=str(resolved),
                loc=self._loc(stmt),
            )
        )
        for alias in stmt.names:
            name, target = alias.name, alias.asname or alias.name
            if name == "*":
                self._diag("P006", "star imports are not allowed", stmt)
            elif name in imported.constants:
                self.module.constants[target] = imported.constants[name]
            elif name in imported.schemas:
                self.module.schemas[target] = imported.schemas[name]
            elif name in imported.tasks:
                self.module.tasks[target] = imported.tasks[name]
            else:
                self._diag("P006", f"'{name}' is not defined in {spec_path.name}", stmt)

    def _constant(self, stmt: ast.Assign) -> None:
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            self._diag("P002", "module constants must be single-name assignments", stmt)
            return
        name = stmt.targets[0].id
        self._check_duplicate(name, stmt)
        rules = self._rules(stmt.value, strict=False)
        try:
            value: Any = self._literal(stmt.value)
            has_value = True
        except _NotLiteral:
            value, has_value = None, False
        if rules is None and not has_value:
            self._diag(
                "P004",
                f"module constant '{name}' must be a literal or a rule list",
                stmt,
            )
            return
        self.module.constants[name] = SpecConstant(
            name=name, value=value, rules=rules, loc=self._loc(stmt)
        )

    def _check_duplicate(self, name: str, node: ast.AST) -> None:
        if (
            name in self.module.schemas
            or name in self.module.tasks
            or name in self.module.constants
        ):
            self._diag("P014", f"duplicate definition of '{name}'", node)

    def _class(self, cls: ast.ClassDef) -> None:
        self._check_duplicate(cls.name, cls)
        kind = self._classify(cls)
        if kind == "schema":
            self._schema(cls)
        elif kind == "task":
            self._task(cls)
        else:
            self._diag(
                "P003",
                f"class '{cls.name}' must subclass BaseModel (schema) or Task",
                cls,
            )

    def _classify(self, cls: ast.ClassDef) -> str | None:
        for base in cls.bases:
            name = (
                base.id
                if isinstance(base, ast.Name)
                else (base.attr if isinstance(base, ast.Attribute) else None)
            )
            if name == "Task" or name in self.module.tasks:
                return "task"
            if name == "BaseModel" or name in self.module.schemas:
                return "schema"
        return None

    # -- literals and rules -------------------------------------------------

    def _literal(self, node: ast.expr) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.List):
            return [self._literal(e) for e in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(self._literal(e) for e in node.elts)
        if isinstance(node, ast.Dict):
            out: dict[Any, Any] = {}
            for key, value in zip(node.keys, node.values, strict=True):
                if key is None:  # ** expansion
                    raise _NotLiteral
                out[self._literal(key)] = self._literal(value)
            return out
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            operand = self._literal(node.operand)
            if isinstance(operand, (int, float)):
                return -operand
            raise _NotLiteral
        if isinstance(node, ast.Name):
            const = self.module.constants.get(node.id)
            if const is not None and const.value is not None:
                return const.value
            raise _NotLiteral
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self._literal(node.left), self._literal(node.right)
            if isinstance(left, list) and isinstance(right, list):
                return left + right
            raise _NotLiteral
        raise _NotLiteral

    def _rules(self, node: ast.expr, *, strict: bool) -> list[Rule] | None:
        """Parse a rule list: a literal list, a constant name, or a + composition.

        strict=True (a task's `constraints`) reports malformed entries;
        strict=False (probing a module constant) returns None on mismatch.
        """
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._rules(node.left, strict=strict)
            right = self._rules(node.right, strict=strict)
            if left is None or right is None:
                return ((left or []) + (right or [])) if strict else None
            return left + right
        if isinstance(node, ast.Name):
            const = self.module.constants.get(node.id)
            if const is not None and const.rules is not None:
                return [rule.model_copy(update={"source": node.id}) for rule in const.rules]
            if strict:
                self._diag("P005", f"'{node.id}' is not a known rule list", node)
                return []
            return None
        if isinstance(node, ast.List):
            rules: list[Rule] = []
            for elt in node.elts:
                rule = self._rule(elt, strict=strict)
                if rule is None and not strict:
                    return None
                if rule is not None:
                    rules.append(rule)
            return rules
        if strict:
            self._diag(
                "P005",
                "constraints must be a list of rules, optionally composed with +",
                node,
            )
            return []
        return None

    _RULE_KWARGS = {"why", "severity", "since"}

    def _rule(self, elt: ast.expr, *, strict: bool) -> Rule | None:
        if isinstance(elt, ast.Call) and self._callee(elt) == "Rule":
            return self._rule_call(elt, strict=strict)
        if strict:
            self._diag(
                "P005",
                'each rule must be Rule("id", "text", why=..., severity=..., '
                "since=...); tuple and bare-string rules were removed in spec 2.0",
                elt,
            )
        return None

    def _rule_call(self, call: ast.Call, *, strict: bool) -> Rule | None:
        def string_value(node: ast.expr, what: str) -> str | None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if strict:
                self._diag("P005", f"Rule {what} must be a string literal", node)
            return None

        if len(call.args) != 2:
            if strict:
                self._diag(
                    "P005",
                    'Rule takes exactly two positional arguments: Rule("id", "text", ...)',
                    call,
                )
            return None
        rule_id = string_value(call.args[0], "id")
        text = string_value(call.args[1], "text")
        if rule_id is None or text is None:
            return None
        if not rule_id.strip():
            if strict:
                self._diag("P005", "Rule id must not be empty", call.args[0])
            return None

        fields: dict[str, str] = {}
        for kw in call.keywords:
            if kw.arg not in self._RULE_KWARGS:
                if strict:
                    self._diag(
                        "P005",
                        f"unknown Rule argument '{kw.arg}'; allowed: {sorted(self._RULE_KWARGS)}",
                        kw.value,
                    )
                return None
            value = string_value(kw.value, kw.arg)
            if value is None:
                return None
            fields[kw.arg] = value

        severity = fields.get("severity", "must")
        if severity not in SEVERITIES:
            if strict:
                self._diag(
                    "P005",
                    f"rule severity must be one of {sorted(SEVERITIES)}, got '{severity}'",
                    call,
                )
            severity = "must"
        return Rule(
            id=rule_id,
            text=text,
            why=fields.get("why"),
            severity=severity,
            since=fields.get("since"),
            loc=self._loc(call),
        )

    # -- schemas ------------------------------------------------------------

    def _schema(self, cls: ast.ClassDef) -> None:
        schema = SchemaDef(name=cls.name, docstring=ast.get_docstring(cls), loc=self._loc(cls))
        for index, stmt in enumerate(cls.body):
            if (
                index == 0
                and isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                continue  # docstring
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                schema.fields.append(self._schema_field(stmt))
            else:
                self._diag(
                    "P009",
                    f"schema '{cls.name}' may contain only annotated fields",
                    stmt,
                )
        self.module.schemas[cls.name] = schema

    def _schema_field(self, stmt: ast.AnnAssign) -> SchemaField:
        assert isinstance(stmt.target, ast.Name)
        field = SchemaField(
            name=stmt.target.id,
            type=self._type_expr(stmt.annotation),
            loc=self._loc(stmt),
        )
        value = stmt.value
        if value is None:
            return field
        if isinstance(value, ast.Call) and self._callee(value) == "Field":
            if value.args:
                try:
                    field.default = self._literal(value.args[0])
                    field.has_default = True
                except _NotLiteral:
                    self._diag("P013", "Field default must be a literal", value.args[0])
            for kw in value.keywords:
                try:
                    kw_value = self._literal(kw.value)
                except _NotLiteral:
                    self._diag("P013", f"Field({kw.arg}=...) must be a literal", kw.value)
                    continue
                if kw.arg in {"ge", "le", "max_length"}:
                    setattr(field, kw.arg, kw_value)
                elif kw.arg == "default":
                    field.default = kw_value
                    field.has_default = True
                elif kw.arg is not None:
                    field.extra[kw.arg] = kw_value
            return field
        try:
            field.default = self._literal(value)
            field.has_default = True
        except _NotLiteral:
            self._diag("P013", "schema field default must be a literal", value)
        return field

    # -- types --------------------------------------------------------------

    def _type_expr(self, node: ast.expr) -> TypeExpr:
        if isinstance(node, ast.Name):
            return TypeExpr(kind="name", name=node.id)
        if isinstance(node, ast.Attribute):
            return TypeExpr(kind="name", name=node.attr)
        if isinstance(node, ast.Constant) and node.value is None:
            return TypeExpr(kind="name", name="None")
        if isinstance(node, ast.Subscript):
            base = node.value
            base_name = (
                base.id
                if isinstance(base, ast.Name)
                else (base.attr if isinstance(base, ast.Attribute) else None)
            )
            if base_name in ENUM_BASES:
                elts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
                values: list[Any] = []
                for elt in elts:
                    if isinstance(elt, ast.Constant):
                        values.append(elt.value)
                    else:
                        self._diag("P011", "Enum/Literal values must be literals", elt)
                return TypeExpr(kind="enum", values=values)
            if base_name == "list":
                return TypeExpr(kind="list", item=self._type_expr(node.slice))
            self._diag("P011", f"unsupported annotation: {ast.unparse(node)}", node)
            return TypeExpr(kind="unknown", raw=ast.unparse(node))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            operands = self._flatten_union(node)
            non_none = [
                op
                for op in operands
                if not (isinstance(op, ast.Constant) and op.value is None)
                and not (isinstance(op, ast.Name) and op.id == "None")
            ]
            if len(non_none) == 1 and len(operands) == 2:
                inner = self._type_expr(non_none[0])
                inner.optional = True
                return inner
            self._diag("P011", "only 'X | None' unions are supported", node)
            return TypeExpr(kind="unknown", raw=ast.unparse(node))
        self._diag("P011", f"unsupported annotation: {ast.unparse(node)}", node)
        return TypeExpr(kind="unknown", raw=ast.unparse(node))

    def _flatten_union(self, node: ast.expr) -> list[ast.expr]:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return self._flatten_union(node.left) + self._flatten_union(node.right)
        return [node]

    # -- tasks --------------------------------------------------------------

    def _task(self, cls: ast.ClassDef) -> None:
        task = TaskDef(name=cls.name, docstring=ast.get_docstring(cls), loc=self._loc(cls))
        for index, stmt in enumerate(cls.body):
            if (
                index == 0
                and isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                continue  # docstring
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                type_expr = self._type_expr(stmt.annotation)
                if stmt.target.id == "returns":
                    task.returns = type_expr
                else:
                    task.inputs.append(
                        TaskInput(name=stmt.target.id, type=type_expr, loc=self._loc(stmt))
                    )
            elif (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ):
                self._task_assign(task, stmt.targets[0].id, stmt)
            else:
                self._diag(
                    "P009",
                    f"forbidden statement in task '{cls.name}': only annotated "
                    "fields, reserved attributes, and pipeline binds are allowed",
                    stmt,
                )
        self.module.tasks[cls.name] = task

    def _task_assign(self, task: TaskDef, name: str, stmt: ast.Assign) -> None:
        value = stmt.value
        if name == "tools":
            task.tools = self._tools(value)
        elif name == "constraints":
            task.constraints = self._rules(value, strict=True) or []
        elif name == "undo":
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                task.undo = value.value
            else:
                self._diag("P008", "undo must be a string", value)
        elif name == "on_uncertain":
            try:
                literal = self._literal(value)
            except _NotLiteral:
                literal = None
            if isinstance(literal, dict):
                task.on_uncertain = literal
            else:
                self._diag("P008", "on_uncertain must be a literal dict output", value)
        elif name == "on_failure":
            task.on_failure = self._failure(value)
        elif name == "on_item_failure":
            if isinstance(value, ast.Constant) and value.value in ITEM_FAILURE_MODES:
                task.on_item_failure = value.value
            else:
                self._diag(
                    "P008",
                    f"on_item_failure must be one of {sorted(ITEM_FAILURE_MODES)}",
                    value,
                )
        elif name == "meta":
            try:
                literal = self._literal(value)
            except _NotLiteral:
                literal = None
            if isinstance(literal, dict):
                task.meta = literal
            else:
                self._diag("P008", "meta must be a literal dict", value)
        elif isinstance(value, ast.Dict) and not all(
            isinstance(k, ast.Constant) and isinstance(k.value, str) for k in value.keys
        ):
            derivation = self._derivation_bind(name, value, stmt)
            if derivation is not None:
                task.derivations.append(derivation)
        else:
            bind = self._bind(name, value, stmt)
            if bind is not None:
                task.pipeline.append(bind)
            else:
                task.unknown_attrs.append(
                    UnknownAttr(name=name, raw=ast.unparse(value), loc=self._loc(stmt))
                )

    def _tools(self, node: ast.expr) -> list[ToolDecl]:
        if not isinstance(node, ast.List):
            self._diag("P007", "tools must be a list of Tool(...) declarations", node)
            return []
        tools = []
        for elt in node.elts:
            tool = self._tool(elt)
            if tool is not None:
                tools.append(tool)
        return tools

    def _tool(self, node: ast.expr) -> ToolDecl | None:
        if not (isinstance(node, ast.Call) and self._callee(node) == "Tool"):
            self._diag("P007", "expected a Tool(...) declaration", node)
            return None
        if len(node.args) != 1 or not (
            isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
        ):
            self._diag("P007", "Tool takes exactly one positional argument: its name", node)
            return None
        tool = ToolDecl(name=node.args[0].value, loc=self._loc(node))
        for kw in node.keywords:
            if kw.arg in {"ops", "scripts", "paths"}:
                try:
                    value = self._literal(kw.value)
                except _NotLiteral:
                    value = None
                if isinstance(value, list) and all(isinstance(v, str) for v in value):
                    setattr(tool, kw.arg, value)
                else:
                    self._diag("P007", f"Tool {kw.arg} must be a list of strings", kw.value)
            elif kw.arg in {"strict", "exclusive"}:
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, bool):
                    setattr(tool, kw.arg, kw.value.value)
                else:
                    self._diag("P007", f"Tool {kw.arg} must be True or False", kw.value)
            elif kw.arg is not None:
                self._diag(
                    "P007",
                    f"unknown Tool keyword '{kw.arg}'",
                    kw.value,
                    severity="warning",
                )
                tool.extra[kw.arg] = ast.unparse(kw.value)
        return tool

    def _failure(self, node: ast.expr) -> FailurePolicy | None:
        if isinstance(node, ast.Constant) and node.value == "abort":
            return FailurePolicy(kind="abort", loc=self._loc(node))
        if isinstance(node, ast.Dict):
            try:
                literal = self._literal(node)
            except _NotLiteral:
                self._diag("P008", "a failure output must contain only literal values", node)
                return None
            return FailurePolicy(kind="literal", literal=literal, loc=self._loc(node))
        if isinstance(node, ast.Call) and self._callee(node) in {"Retry", "Escalate"}:
            kind = "retry" if self._callee(node) == "Retry" else "escalate"
            policy = FailurePolicy(kind=kind, loc=self._loc(node))
            if node.args:
                self._diag("P008", f"{self._callee(node)} takes keyword arguments only", node)
            for kw in node.keywords:
                if kw.arg == "then":
                    policy.then = self._failure(kw.value)
                elif kw.arg in {"max", "backoff_s", "timeout_s"}:
                    try:
                        value = self._literal(kw.value)
                    except _NotLiteral:
                        value = None
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        setattr(policy, kw.arg, value)
                    else:
                        self._diag("P008", f"{kw.arg} must be a number", kw.value)
                elif kw.arg == "channel":
                    if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        policy.channel = kw.value.value
                    else:
                        self._diag("P008", "channel must be a string", kw.value)
                else:
                    self._diag(
                        "P008",
                        f"unknown {self._callee(node)} keyword '{kw.arg}'",
                        kw.value,
                        severity="warning",
                    )
            return policy
        self._diag(
            "P008",
            "on_failure must be 'abort', a literal output, Retry(...), or Escalate(...)",
            node,
        )
        return None

    # -- the pipeline (spec §7) ---------------------------------------------

    def _bind(self, var: str, value: ast.expr, stmt: ast.Assign) -> PipelineBind | None:
        gate: ValueRef | None = None
        gate_negated = False
        inner = value
        if isinstance(inner, ast.IfExp):
            if not (isinstance(inner.orelse, ast.Constant) and inner.orelse.value is None):
                self._diag("P010", "a gate's else branch must be None", inner.orelse)
            test = inner.test
            if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
                gate_negated = True
                test = test.operand
            gate = self._valueref(test)
            inner = inner.body
        if isinstance(inner, ast.Call):
            return self._call_bind(var, inner, stmt, gate, gate_negated)
        if isinstance(inner, ast.ListComp):
            return self._fanout_bind(var, inner, stmt, gate, gate_negated)
        return None

    _COMPARE_OPS: dict[type, str] = {
        ast.Eq: "==",
        ast.NotEq: "!=",
        ast.Gt: ">",
        ast.GtE: ">=",
        ast.Lt: "<",
        ast.LtE: "<=",
        ast.In: "in",
    }

    def _derivation_bind(self, var: str, node: ast.Dict, stmt: ast.Assign) -> DerivationBind | None:
        """A cond-dict (spec 2.2): `{condition: {field: value}, ..., True: {...}}`,
        first match wins top to bottom."""
        rows = []
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                self._diag("P015", "** expansion is not allowed in a derivation", node)
                continue
            atoms = self._condition_atoms(key)
            if atoms is None:
                continue
            output = self._derivation_output(value)
            if output is None:
                continue
            rows.append(
                DerivationRow(
                    atoms=atoms,
                    raw=ast.unparse(key),
                    output=output,
                    loc=self._loc(key),
                )
            )
        if not rows:
            return None
        return DerivationBind(var=var, rows=rows, raw=ast.unparse(stmt.value), loc=self._loc(stmt))

    def _condition_atoms(self, node: ast.expr) -> list[DerivationAtom] | None:
        """The caged condition grammar: `True`, a path, `not path`,
        comparisons against literals, joined by `and` only."""
        if isinstance(node, ast.Constant) and node.value is True:
            return []  # the catch-all
        operands = (
            node.values if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And) else [node]
        )
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            self._diag(
                "P015",
                "derivation conditions may not use 'or' — split into separate rows",
                node,
            )
            return None
        atoms = []
        for operand in operands:
            atom = self._condition_atom(operand)
            if atom is None:
                return None
            atoms.append(atom)
        return atoms

    def _condition_atom(self, node: ast.expr) -> DerivationAtom | None:
        negated = False
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            negated, node = True, node.operand
        if isinstance(node, ast.Compare):
            if negated or len(node.ops) != 1:
                self._diag("P015", "derivation comparisons cannot be chained or negated", node)
                return None
            op = self._COMPARE_OPS.get(type(node.ops[0]))
            path = self._attr_path(node.left)
            if op is None or path is None:
                self._diag(
                    "P015",
                    "a derivation comparison must be `path <op> literal` with "
                    f"op in {sorted(self._COMPARE_OPS.values())}",
                    node,
                )
                return None
            try:
                literal = self._literal(node.comparators[0])
            except _NotLiteral:
                self._diag("P015", "a derivation comparison must compare against a literal", node)
                return None
            return DerivationAtom(path=path, op=op, value=literal)
        path = self._attr_path(node)
        if path is None:
            self._diag(
                "P015",
                "a derivation condition must be True, a boolean path, `not path`, "
                "or `path <op> literal`, joined by 'and'",
                node,
            )
            return None
        return DerivationAtom(path=path, negated=negated)

    def _derivation_output(self, node: ast.expr) -> dict[str, ValueRef] | None:
        if not isinstance(node, ast.Dict):
            self._diag(
                "P015",
                'each derivation row must yield a dict: {"field": literal-or-path, ...}',
                node,
            )
            return None
        output: dict[str, ValueRef] = {}
        for key, value in zip(node.keys, node.values, strict=True):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                self._diag("P015", "derivation output fields must be string keys", node)
                return None
            output[key.value] = self._valueref(value)
        return output

    def _attr_path(self, node: ast.expr) -> AttrPath | None:
        ref = self._valueref(node)
        return ref.path

    def _call_bind(
        self,
        var: str,
        call: ast.Call,
        stmt: ast.Assign,
        gate: ValueRef | None,
        gate_negated: bool = False,
    ) -> PipelineBind | None:
        if not isinstance(call.func, ast.Name):
            return None
        kwargs: dict[str, ValueRef] = {}
        for kw in call.keywords:
            if kw.arg is None:
                self._diag("P010", "** expansion is not allowed in a pipeline bind", kw.value)
                continue
            kwargs[kw.arg] = self._valueref(kw.value)
        if call.args:
            self._diag("P010", "pipeline binds must use keyword arguments", call)
        return PipelineBind(
            var=var,
            task=call.func.id,
            kwargs=kwargs,
            gate=gate,
            gate_negated=gate_negated,
            raw=ast.unparse(stmt.value),
            loc=self._loc(stmt),
        )

    def _fanout_bind(
        self,
        var: str,
        comp: ast.ListComp,
        stmt: ast.Assign,
        gate: ValueRef | None,
        gate_negated: bool = False,
    ) -> PipelineBind | None:
        if not isinstance(comp.elt, ast.Call):
            self._diag("P010", "a fan-out must apply one task per item", comp)
            return None
        bind = self._call_bind(var, comp.elt, stmt, gate, gate_negated)
        if bind is None:
            return None
        if len(comp.generators) != 1:
            self._diag("P010", "a fan-out must have exactly one for-clause", comp)
        gen = comp.generators[0]
        if gen.is_async:
            self._diag("P010", "async comprehensions are not allowed", comp)
        if isinstance(gen.target, ast.Name):
            loop_var = gen.target.id
        else:
            self._diag("P010", "the fan-out loop variable must be a single name", gen.target)
            loop_var = ""
        filt: Filter | None = None
        if gen.ifs:
            if len(gen.ifs) > 1:
                self._diag("P010", "a fan-out may have at most one filter", comp)
            filt = self._filter(gen.ifs[0])
        bind.fanout = FanOut(loop_var=loop_var, source=self._valueref(gen.iter), filter=filt)
        return bind

    def _filter(self, node: ast.expr) -> Filter:
        raw = ast.unparse(node)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
            parts = self._attr_parts(node.left)
            comparator = node.comparators[0]
            if parts is not None:
                if isinstance(node.ops[0], ast.In) and isinstance(
                    comparator, (ast.List, ast.Tuple)
                ):
                    try:
                        values = [self._literal(e) for e in comparator.elts]
                    except _NotLiteral:
                        return Filter(valid=False, raw=raw)
                    return Filter(
                        valid=True, path=AttrPath(parts=parts), op="in", values=values, raw=raw
                    )
                if isinstance(node.ops[0], ast.Eq) and isinstance(comparator, ast.Constant):
                    return Filter(
                        valid=True,
                        path=AttrPath(parts=parts),
                        op="==",
                        values=[comparator.value],
                        raw=raw,
                    )
        return Filter(valid=False, raw=raw)

    def _valueref(self, node: ast.expr) -> ValueRef:
        parts = self._attr_parts(node)
        if parts is not None:
            return ValueRef(
                kind="path",
                path=AttrPath(parts=parts),
                raw=ast.unparse(node),
                loc=self._loc(node),
            )
        try:
            return ValueRef(
                kind="literal",
                value=self._literal(node),
                raw=ast.unparse(node),
                loc=self._loc(node),
            )
        except _NotLiteral:
            return ValueRef(kind="invalid", raw=ast.unparse(node), loc=self._loc(node))

    def _attr_parts(self, node: ast.expr) -> list[str] | None:
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
            return list(reversed(parts))
        return None

    def _callee(self, call: ast.Call) -> str | None:
        func = call.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None
