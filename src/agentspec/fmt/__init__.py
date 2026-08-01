"""Canonical formatting for spec files.

Two stages:

1. **Spec-specific ordering** — each task class body is reordered into the
   canonical sequence: docstring, inputs, `returns`, pipeline binds (their
   relative order is semantic and preserved), then the reserved attributes
   in spec §4 order (`tools`, `constraints`, `undo`, `on_uncertain`,
   `on_item_failure`, `on_failure`, `meta`). The reorder works on source
   line ranges, so comments and blank lines travel with the statement that
   follows them.
2. **Layout** — `ruff format --isolated` (a runtime dependency) as the
   canonical layout engine: deterministic wrapping, quoting, and spacing,
   independent of any project ruff configuration.

Module level is never reordered: the order of constants and classes can be
semantic (definition-before-reference).
"""

import ast
import subprocess
import sys

RESERVED_RANK = {
    "tools": 4,
    "constraints": 5,
    "undo": 6,
    "on_uncertain": 7,
    "on_item_failure": 8,
    "on_failure": 9,
    "meta": 10,
}
BIND_RANK = 3  # pipeline binds sit between `returns` and the reserved attrs


class FormatError(Exception):
    """The source cannot be formatted (syntax error or ruff failure)."""


def format_source(source: str, filename: str = "<spec>") -> str:
    try:
        tree = ast.parse(source, filename)
    except SyntaxError as exc:
        raise FormatError(f"{filename}:{exc.lineno or 0}: syntax error: {exc.msg}") from exc
    reordered = _reorder_tasks(source, tree)
    return _ruff_format(reordered, filename)


def _task_class_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = {
                base.id
                if isinstance(base, ast.Name)
                else (base.attr if isinstance(base, ast.Attribute) else None)
                for base in node.bases
            }
            if "Task" in bases or bases & names:
                names.add(node.name)
    return names


def _reorder_tasks(source: str, tree: ast.Module) -> str:
    lines = source.splitlines(keepends=True)
    task_names = _task_class_names(tree)
    # Bottom-up so earlier replacements don't shift later line numbers.
    for node in reversed(tree.body):
        if isinstance(node, ast.ClassDef) and node.name in task_names:
            replacement = _reorder_class(node, lines)
            if replacement is not None:
                start, end, text = replacement
                lines[start:end] = [text]
    return "".join(lines)


def _reorder_class(node: ast.ClassDef, lines: list[str]) -> tuple[int, int, str] | None:
    stmts = node.body
    if len(stmts) < 2:
        return None
    # Segments must be whole-line: bail out on `a = 1; b = 2` style bodies.
    for prev, stmt in zip(stmts, stmts[1:], strict=False):
        if stmt.lineno <= (prev.end_lineno or prev.lineno):
            return None

    # Segment i spans from the end of statement i-1 (exclusive) to the end of
    # statement i — so comments and blank lines travel with what follows them.
    segments: list[str] = []
    region_start = stmts[0].lineno - 1
    for index, stmt in enumerate(stmts):
        start = region_start if index == 0 else (stmts[index - 1].end_lineno or 0)
        segments.append("".join(lines[start : stmt.end_lineno or 0]))

    order = sorted(range(len(stmts)), key=lambda i: (_rank(stmts[i], i), i))
    if order == list(range(len(stmts))):
        return None
    region_end = stmts[-1].end_lineno or 0
    return region_start, region_end, "".join(segments[i] for i in order)


def _rank(stmt: ast.stmt, index: int) -> int:
    if (
        index == 0
        and isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    ):
        return 0  # docstring
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        return 2 if stmt.target.id == "returns" else 1
    if (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
    ):
        return RESERVED_RANK.get(stmt.targets[0].id, BIND_RANK)
    return 99  # unknown statements keep their relative position at the end


def _ruff_format(code: str, filename: str) -> str:
    stdin_name = filename if filename.endswith(".py") else "spec.aspec.py"
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--isolated", "--stdin-filename", stdin_name, "-"],
        input=code,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise FormatError(f"ruff format failed: {result.stderr.strip()}")
    return result.stdout
