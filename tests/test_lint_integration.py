"""Lint over the real fixtures: the good ones stay clean, and BookBank's one
true finding stays pinned."""

from agentspec.lint import lint_module
from agentspec.parser import parse_file


def test_minimal_good_is_clean(fixtures_dir):
    assert lint_module(parse_file(fixtures_dir / "minimal_good.aspec.py")) == []


def test_fanout_routine_is_clean(fixtures_dir):
    assert lint_module(parse_file(fixtures_dir / "fanout_routine.aspec.py")) == []


def test_imports_main_is_clean(fixtures_dir):
    assert lint_module(parse_file(fixtures_dir / "imports_main.aspec.py")) == []


def test_bookbank_is_clean(fixtures_dir):
    """Used to carry a pinned AS031 warning (GenerateBook: 16 constraints,
    one over the ~15 ceiling of spec §5) — plan/toolchain.md always called
    the fix a spec-author decision, not a lint bug: decompose GenerateBook.
    v2.4.0 did exactly that (GenerateBook / PublishBook), so the file lints
    clean with no findings left to pin."""
    diags = lint_module(parse_file(fixtures_dir / "bookbank_routine.aspec.py"))
    assert diags == []


def test_bad_module_statement_reports_errors(fixtures_dir):
    diags = lint_module(parse_file(fixtures_dir / "bad_module_statement.aspec.py"))
    assert {"P002", "P004", "P006"} <= {d.code for d in diags}
