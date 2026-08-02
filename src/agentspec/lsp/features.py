"""LSP features: hover, go-to-definition, document symbols (L1);
completions and code actions (L2).

Everything answers from the SpecModel: the cursor position selects a dotted
identifier chain in the document text, and the chain resolves contextually —
class names anywhere; bind variables, their result fields, and task inputs
inside the enclosing task; rules by their id string or their first line.
Completions tolerate a document that currently fails to parse: the server
hands them the last good SpecModel alongside the fresh text.
"""

import re
from pathlib import Path
from typing import Any

from agentspec.parser import (
    PipelineBind,
    Rule,
    SchemaDef,
    SourceLoc,
    SpecModule,
    TaskDef,
    ToolDecl,
    TypeExpr,
)

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# LSP SymbolKind
_KIND_CLASS = 5
_KIND_FIELD = 8
_KIND_VARIABLE = 13
_KIND_CONSTANT = 14
_KIND_KEY = 20
_KIND_STRUCT = 23

# LSP CompletionItemKind
_COMPLETE_FIELD = 5
_COMPLETE_VARIABLE = 6
_COMPLETE_ENUM_MEMBER = 20

# The closed `env` namespace (spec §10)
ENV_MEMBERS = {
    "now": "ISO timestamp",
    "cwd": "working directory",
    "user": "user name",
    "platform": "platform name",
    "run_id": "unique run id",
}

# Reserved-attribute semantics, taught at the trap sites (spec §4–§8)
ATTR_DOCS = {
    "tools": "The task's declared capability surface. Capabilities not "
    "declared here are out of bounds even if available; substitution swaps "
    "mechanisms, it never widens capability (spec §6).",
    "constraints": "The task's rules. A parent task's constraints bind every "
    "step inside it; a child may add stricter rules, never weaken a parent "
    "must (spec §5).",
    "undo": "The task's declared reversal. It runs ONLY during an abort's "
    "saga unwind, in reverse completion order — a task that fails into a "
    "literal fallback is NOT unwound (spec §8).",
    "on_uncertain": "The output to return when the model genuinely cannot "
    "decide. Choosing it is always legitimate and never a failure; doubt "
    "should de-escalate to the conservative branch (spec §8).",
    "on_failure": "Applied exactly as declared — the agent never invents its "
    "own recovery. 'abort' stops and unwinds completed steps via their "
    "undo; a literal keeps the run going with that declared output (spec §8).",
    "on_item_failure": "Fan-out only: 'skip_and_report' drops the failing "
    "item, surfaces it in the output, and continues the batch; 'abort' "
    "fails the whole step (spec §8).",
    "returns": "The task's output contract — always a named schema. Field "
    "bounds and closed enums are binding at runtime (spec §3).",
    "meta": "Free-form task metadata; the version lives here (spec §4).",
}

# Rule severities (spec §5)
SEVERITY_DOCS = {
    "must": "Inviolable — never broken. A child task may add stricter rules; "
    "it may never weaken a parent's must. This is the default when a rule "
    "omits its severity.",
    "should": "Binding unless it conflicts with a must — the must wins. When "
    "two rules conflict, the more conservative action wins.",
    "may": "Explicit permission, not obligation — the agent can take it or "
    "leave it; every other rule still applies.",
}


def _render(t: TypeExpr | None) -> str:
    return "?" if t is None else t.render()


# ---------------- position → identifier chain ----------------


def word_chain_at(text: str, line: int, character: int) -> tuple[list[str], int] | None:
    """The dotted chain around an LSP position, and which segment holds the
    cursor: `iss|ue.number` → (["issue", "number"], 0)."""
    lines = text.split("\n")
    if line >= len(lines):
        return None
    row = lines[line]
    hit = None
    for m in _IDENT.finditer(row):
        if m.start() <= character <= m.end():
            hit = m
            break
    if hit is None:
        return None
    segments, index = [hit.group()], 0
    # walk left through `.` links
    start = hit.start()
    while start >= 1 and row[start - 1] == ".":
        left = None
        for m in _IDENT.finditer(row, 0, start - 1):
            if m.end() == start - 1:
                left = m
        if left is None:
            break
        segments.insert(0, left.group())
        index += 1
        start = left.start()
    # walk right through `.` links
    end = hit.end()
    while end < len(row) and row[end] == ".":
        m = _IDENT.match(row, end + 1)
        if m is None:
            break
        segments.append(m.group())
        end = m.end()
    return segments, index


# ---------------- document structure ----------------


def _local_starts(module: SpecModule, filename: str) -> list[tuple[int, str, Any]]:
    """(1-based start line, kind, object) for this file's constructs, sorted."""
    starts: list[tuple[int, str, Any]] = []
    for schema in module.schemas.values():
        if schema.loc.file == filename:
            starts.append((schema.loc.line, "schema", schema))
    for task in module.tasks.values():
        if task.loc.file == filename:
            starts.append((task.loc.line, "task", task))
    for const in module.constants.values():
        if const.loc.file == filename:
            starts.append((const.loc.line, "constant", const))
    return sorted(starts, key=lambda s: s[0])


def enclosing_class(module: SpecModule, filename: str, line0: int) -> TaskDef | SchemaDef | None:
    """The task or schema whose body contains the 0-based line, if any."""
    line = line0 + 1
    found: TaskDef | SchemaDef | None = None
    for start, kind, obj in _local_starts(module, filename):
        if start > line:
            break
        found = obj if kind in ("schema", "task") else None
    return found


def _loc_range(loc: SourceLoc) -> dict[str, Any]:
    pos = {"line": max(loc.line - 1, 0), "character": max(loc.col, 0)}
    return {"start": pos, "end": pos}


def _location(doc_uri: str, doc_filename: str, loc: SourceLoc) -> dict[str, Any] | None:
    if loc.file == doc_filename:
        return {"uri": doc_uri, "range": _loc_range(loc)}
    path = Path(loc.file)
    if path.is_absolute():
        return {"uri": path.as_uri(), "range": _loc_range(loc)}
    return None


# ---------------- resolution ----------------


class Resolved:
    """One resolved construct: markdown for hover, a loc for definition."""

    def __init__(self, markdown: str, loc: SourceLoc | None):
        self.markdown = markdown
        self.loc = loc


def _returns_schema(module: SpecModule, task: TaskDef) -> SchemaDef | None:
    r = task.returns
    if r is not None and r.kind == "name" and r.name in module.schemas:
        return module.schemas[r.name]
    return None


def _task_md(module: SpecModule, task: TaskDef) -> str:
    kind = "orchestrator" if task.is_orchestrator else "task"
    parts = [f"**{task.name}** — {kind}"]
    if task.docstring:
        parts.append(task.docstring)
    facts = [f"returns `{_render(task.returns)}`"]
    if task.inputs:
        facts.append("inputs: " + ", ".join(f"`{i.name}: {_render(i.type)}`" for i in task.inputs))
    if task.tools:
        facts.append("tools: " + ", ".join(t.name + ("!" if t.strict else "") for t in task.tools))
    if task.constraints:
        musts = sum(1 for r in task.constraints if r.severity == "must")
        facts.append(f"rules: {len(task.constraints)} ({musts} must)")
    if task.on_failure is not None:
        facts.append(f"on_failure: {task.on_failure.kind}")
    parts.append("  \n".join(facts))
    return "\n\n".join(parts)


def _schema_md(schema: SchemaDef) -> str:
    lines = [f"**{schema.name}** — schema", "```"]
    for f in schema.fields:
        bounds = []
        if f.ge is not None:
            bounds.append(f"ge={f.ge:g}")
        if f.le is not None:
            bounds.append(f"le={f.le:g}")
        if f.max_length is not None:
            bounds.append(f"max_length={f.max_length}")
        suffix = f"  [{', '.join(bounds)}]" if bounds else ""
        lines.append(f"{f.name}: {_render(f.type)}{suffix}")
    lines.append("```")
    return "\n".join(lines)


def _field_md(schema: SchemaDef, name: str, producer: str | None) -> str | None:
    field = schema.field(name)
    if field is None:
        return None
    bounds = []
    if field.ge is not None:
        bounds.append(f"ge={field.ge:g}")
    if field.le is not None:
        bounds.append(f"le={field.le:g}")
    if field.max_length is not None:
        bounds.append(f"max_length={field.max_length}")
    md = f"**{schema.name}.{name}**: `{_render(field.type)}`"
    if bounds:
        md += f"  [{', '.join(bounds)}]"
    if producer:
        md += f"\n\nproduced by **{producer}**"
    return md


def _tool_md(tool: ToolDecl) -> str:
    parts = [f"**{tool.name}** — tool capability"]
    surface = []
    for attr in ("ops", "scripts", "paths"):
        values = getattr(tool, attr)
        if values:
            surface.append(f"{attr}: " + ", ".join(values))
    if surface:
        parts.append("\n".join(surface))
    if tool.strict:
        parts.append(
            "*strict* — this exact mechanism or nothing; if it is unusable, "
            "the task fails and its declared on_failure applies (spec §6)"
        )
    else:
        parts.append(
            "A Tool names a capability by its preferred mechanism, not a "
            "binary: if unavailable, an equivalent mechanism MAY substitute — "
            "covering only the declared surface, with the substitution "
            "recorded in the run report (spec §6)"
        )
    if tool.exclusive:
        parts.append(
            "*exclusive* — two concurrent steps must not hold it; "
            "fan-out over this tool serializes per item"
        )
    return "\n\n".join(parts)


def _derivation_md(derivation) -> str:
    lines = [
        f"**{derivation.var}** — derivation (mechanical; first match wins, "
        "top to bottom; never skipped)",
        "",
    ]
    for row in derivation.rows:
        values = ", ".join(
            f"{field}={ref.path.dotted if ref.path is not None else ref.value!r}"
            for field, ref in row.output.items()
        )
        lines.append(f"- `{row.raw}` → {values}")
    return "\n".join(lines)


def _bind_md(module: SpecModule, bind: PipelineBind) -> str:
    parts = [f"**{bind.var}** = {bind.task}(…)"]
    task = module.tasks.get(bind.task)
    if task is not None:
        parts.append(f"returns `{_render(task.returns)}`")
    if bind.gate is not None:
        parts.append(f"gated: runs only if `{bind.gate_condition()}`")
    if bind.fanout is not None:
        src = bind.fanout.source
        parts.append(f"fan-out over `{src.path.dotted if src.path else src.raw}`")
    return "\n\n".join(parts)


def _rule_md(rule: Rule) -> str:
    md = f"**{rule.id}** — {rule.severity}-rule\n\n{rule.text}"
    if rule.why:
        md += f"\n\n*why:* {rule.why}"
    if rule.since:
        md += f"\n\n*since:* {rule.since}"
    if rule.source:
        md += f"\n\n*from* `{rule.source}`"
    return md


def _all_rules(module: SpecModule):
    for task in module.tasks.values():
        yield from task.constraints
    for const in module.constants.values():
        yield from const.rules or []


def _string_at(row: str, character: int) -> str | None:
    """The content of the quoted single-line string containing the cursor."""
    for m in re.finditer(r'"([^"\\]*)"|\'([^\'\\]*)\'', row):
        if m.start() < character < m.end():
            return m.group(1) if m.group(1) is not None else m.group(2)
    return None


def resolve_at(
    module: SpecModule, filename: str, text: str, line: int, character: int
) -> Resolved | None:
    rows = text.split("\n")
    row = rows[line] if line < len(rows) else ""
    quoted = _string_at(row, character)
    encl = enclosing_class(module, filename, line)

    if quoted is not None:
        by_id = next((r for r in _all_rules(module) if r.id == quoted), None)
        if by_id is not None:
            return Resolved(_rule_md(by_id), by_id.loc)
        if isinstance(encl, TaskDef):
            tool = next((t for t in encl.tools if t.name == quoted), None)
            if tool is not None:
                return Resolved(_tool_md(tool), tool.loc)

    chain = word_chain_at(text, line, character)
    if chain is None:
        return _rule_at_line(module, filename, line)
    segments, index = chain
    root = segments[0]

    if len(segments) == 1 and root in SEVERITY_DOCS and quoted == root:
        return Resolved(f"**{root}** — rule severity\n\n{SEVERITY_DOCS[root]}", None)

    if (
        quoted is None
        and len(segments) == 1
        and root in ATTR_DOCS
        and re.match(rf"\s*{root}\s*[=:]", row)
    ):
        return Resolved(f"**{root}**\n\n{ATTR_DOCS[root]}", None)

    if index == 0:
        if root in module.tasks:
            return Resolved(_task_md(module, module.tasks[root]), module.tasks[root].loc)
        if root in module.schemas:
            return Resolved(_schema_md(module.schemas[root]), module.schemas[root].loc)
        if root in module.constants:
            const = module.constants[root]
            rules = const.rules or []
            md = f"**{root}** — rule list ({len(rules)} rule(s))" if rules else f"**{root}**"
            return Resolved(md, const.loc)

    if isinstance(encl, TaskDef):
        derivation = encl.derivation(root)
        if derivation is not None and (index == 0 or len(segments) == 1):
            return Resolved(_derivation_md(derivation), derivation.loc)
        bind = encl.bind(root)
        if bind is not None:
            if index == 0 or len(segments) == 1:
                return Resolved(_bind_md(module, bind), bind.loc)
            task = module.tasks.get(bind.task)
            schema = _returns_schema(module, task) if task else None
            if schema is not None:
                md = _field_md(schema, segments[1], producer=bind.task)
                field = schema.field(segments[1])
                if md is not None and field is not None:
                    return Resolved(md, field.loc)
            return None
        declared = next((i for i in encl.inputs if i.name == root), None)
        if declared is not None:
            md = f"**{encl.name}.{root}**: `{_render(declared.type)}` — task input"
            if declared.type.optional:
                md += (
                    "\n\noptional — upstream skips do NOT propagate through "
                    "this input (spec §7): the task still runs when this "
                    "value's producer was skipped"
                )
            if root == "freeform_context":
                md += (
                    "\n\nreserved name: unnamed freeform text from the "
                    "dispatch context flows into this input (spec §9)"
                )
            return Resolved(md, declared.loc)

    return _rule_at_line(module, filename, line)


def _rule_at_line(module: SpecModule, filename: str, line0: int) -> Resolved | None:
    """A rule hovered by its first line (rule tuples span lines; the loc is
    where the tuple starts)."""
    line = line0 + 1
    for task in module.tasks.values():
        for rule in task.constraints:
            if rule.loc.file == filename and rule.loc.line == line:
                return Resolved(_rule_md(rule), rule.loc)
    for const in module.constants.values():
        for rule in const.rules or []:
            if rule.loc.file == filename and rule.loc.line == line:
                return Resolved(_rule_md(rule), rule.loc)
    return None


# ---------------- the three features ----------------


def hover(
    module: SpecModule, filename: str, text: str, line: int, character: int
) -> dict[str, Any] | None:
    resolved = resolve_at(module, filename, text, line, character)
    if resolved is None:
        return None
    return {"contents": {"kind": "markdown", "value": resolved.markdown}}


def definition(
    module: SpecModule,
    doc_uri: str,
    filename: str,
    text: str,
    line: int,
    character: int,
) -> dict[str, Any] | None:
    resolved = resolve_at(module, filename, text, line, character)
    if resolved is None or resolved.loc is None:
        return None
    return _location(doc_uri, filename, resolved.loc)


_DOT_COMPLETION = re.compile(r"([A-Za-z_]\w*)\.\w*$")
_DICT_VALUE = re.compile(r'"(\w+)"\s*:\s*"[\w-]*$')
_COMPARISON = re.compile(r'([\w.]+)\s*(?:==|in\s*\[)\s*"[\w-]*$')


_SEVERITY_KWARG = re.compile(r'severity\s*=\s*"[a-z]*$')


def _field_item(schema: SchemaDef, field_name: str, producer: str | None) -> dict[str, Any]:
    field = schema.field(field_name)
    item: dict[str, Any] = {
        "label": field_name,
        "kind": _COMPLETE_FIELD,
        "detail": _render(field.type) if field else "",
    }
    if producer:
        item["documentation"] = f"{schema.name}.{field_name}, produced by {producer}"
    return item


def _enum_values_of(module: SpecModule, task: TaskDef | None, field_name: str) -> list[Any]:
    schema = _returns_schema(module, task) if task else None
    field = schema.field(field_name) if schema else None
    if field is None:
        return []
    t = field.type
    if t.kind == "enum":
        return list(t.values)
    return []


def completion(
    module: SpecModule, filename: str, text: str, line: int, character: int
) -> list[dict[str, Any]]:
    lines = text.split("\n")
    if line >= len(lines):
        return []
    prefix = lines[line][:character]
    encl = enclosing_class(module, filename, line)

    dotted = _DOT_COMPLETION.search(prefix)
    if dotted is not None:
        root = dotted.group(1)
        if root == "env":
            return [
                {"label": name, "kind": _COMPLETE_VARIABLE, "detail": detail}
                for name, detail in ENV_MEMBERS.items()
            ]
        if isinstance(encl, TaskDef):
            bind = encl.bind(root)
            if bind is not None:
                task = module.tasks.get(bind.task)
                schema = _returns_schema(module, task) if task else None
                if schema is not None:
                    return [_field_item(schema, f.name, bind.task) for f in schema.fields]
        return []

    if _SEVERITY_KWARG.search(prefix):
        # rule constants live at module level too, so no enclosing-task gate
        return [
            {
                "label": severity,
                "kind": _COMPLETE_ENUM_MEMBER,
                "detail": "rule severity",
                "documentation": doc,
            }
            for severity, doc in SEVERITY_DOCS.items()
        ]

    if not isinstance(encl, TaskDef):
        return []

    dict_value = _DICT_VALUE.search(prefix)
    if dict_value is not None:
        # `"field": "` inside an on_uncertain / on_failure literal: the keys
        # are the enclosing task's own output contract
        values = _enum_values_of(module, encl, dict_value.group(1))
        return [{"label": str(v), "kind": _COMPLETE_ENUM_MEMBER} for v in values]

    comparison = _COMPARISON.search(prefix)
    if comparison is not None:
        # `var.field == "` / `var.field in ["` — gates and filters
        parts = comparison.group(1).split(".")
        if len(parts) >= 2:
            bind = encl.bind(parts[0])
            task = module.tasks.get(bind.task) if bind else None
            values = _enum_values_of(module, task, parts[1])
            return [{"label": str(v), "kind": _COMPLETE_ENUM_MEMBER} for v in values]
    return []


def code_actions(
    module: SpecModule,
    doc_uri: str,
    text: str,
    diagnostics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Quickfixes for the diagnostics the client sent with the request.
    AS030 (must-rule without a why): insert `why="TODO..."` before the
    Rule call's closing paren — single-line Rule calls only; multi-line
    calls get no action rather than a wrong edit."""
    actions = []
    lines = text.split("\n")
    for diag in diagnostics:
        if diag.get("code") != "AS030":
            continue
        line = diag["range"]["start"]["line"]
        col = diag["range"]["start"]["character"]
        if line >= len(lines):
            continue
        close = _call_close_on_line(lines[line], col)
        if close is None:
            continue
        edit = {
            "range": {
                "start": {"line": line, "character": close},
                "end": {"line": line, "character": close},
            },
            "newText": ', why="TODO: state the why"',
        }
        actions.append(
            {
                "title": "Add a why to this must-rule",
                "kind": "quickfix",
                "diagnostics": [diag],
                "edit": {"changes": {doc_uri: [edit]}},
            }
        )
    return actions


def _call_close_on_line(row: str, col: int) -> int | None:
    """Index of the paren closing the call that starts at `col`, if the
    whole call sits on this one line."""
    depth = 0
    in_string = False
    quote = ""
    for i in range(col, len(row)):
        ch = row[i]
        if in_string:
            if ch == quote:
                in_string = False
            continue
        if ch in "\"'":
            in_string, quote = True, ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


def document_symbols(module: SpecModule, filename: str, text: str) -> list[dict[str, Any]]:
    starts = _local_starts(module, filename)
    last_line = len(text.split("\n"))
    symbols = []
    for i, (start, kind, obj) in enumerate(starts):
        end = (starts[i + 1][0] - 1) if i + 1 < len(starts) else last_line
        rng = {
            "start": {"line": max(start - 1, 0), "character": 0},
            "end": {"line": max(end - 1, 0), "character": 0},
        }
        sel = _loc_range(obj.loc)
        if kind == "schema":
            children = [
                {
                    "name": f"{f.name}: {_render(f.type)}",
                    "kind": _KIND_FIELD,
                    "range": _loc_range(f.loc),
                    "selectionRange": _loc_range(f.loc),
                }
                for f in obj.fields
            ]
            symbols.append(
                {
                    "name": obj.name,
                    "detail": "schema",
                    "kind": _KIND_STRUCT,
                    "range": rng,
                    "selectionRange": sel,
                    "children": children,
                }
            )
        elif kind == "task":
            children = (
                [
                    {
                        "name": f"{b.var} = {b.task}",
                        "kind": _KIND_VARIABLE,
                        "range": _loc_range(b.loc),
                        "selectionRange": _loc_range(b.loc),
                    }
                    for b in obj.pipeline
                ]
                + [
                    {
                        "name": f"{d.var} = derived",
                        "kind": _KIND_VARIABLE,
                        "range": _loc_range(d.loc),
                        "selectionRange": _loc_range(d.loc),
                    }
                    for d in obj.derivations
                ]
                + [
                    # inline rules only: composed constants outline under the constant
                    _rule_symbol(r)
                    for r in obj.constraints
                    if r.source is None and r.loc.file == filename
                ]
            )
            symbols.append(
                {
                    "name": obj.name,
                    "detail": "orchestrator" if obj.is_orchestrator else "task",
                    "kind": _KIND_CLASS,
                    "range": rng,
                    "selectionRange": sel,
                    "children": children,
                }
            )
        else:
            symbols.append(
                {
                    "name": obj.name,
                    "detail": f"{len(obj.rules)} rule(s)" if obj.rules else "constant",
                    "kind": _KIND_CONSTANT,
                    "range": rng,
                    "selectionRange": sel,
                    "children": [_rule_symbol(r) for r in obj.rules or []],
                }
            )
    return symbols


def _rule_symbol(rule: Rule) -> dict[str, Any]:
    return {
        "name": rule.id,
        "detail": rule.severity,
        "kind": _KIND_KEY,
        "range": _loc_range(rule.loc),
        "selectionRange": _loc_range(rule.loc),
    }
