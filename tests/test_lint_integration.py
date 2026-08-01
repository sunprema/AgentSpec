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


def test_bookbank_has_no_errors_and_one_true_warning(fixtures_dir):
    """GenerateBook carries 16 constraints (3 shared + 13 of its own), one over
    the ~15 ceiling of spec §5. That is a real finding against the reference
    spec — the fix is the spec author's call (decompose GenerateBook), so the
    warning stays pinned here rather than silenced."""
    diags = lint_module(parse_file(fixtures_dir / "bookbank_routine.aspec.py"))
    assert [d for d in diags if d.severity == "error"] == []
    assert [d.code for d in diags] == ["AS031"]
    assert "GenerateBook" in diags[0].message


def test_bad_module_statement_reports_errors(fixtures_dir):
    diags = lint_module(parse_file(fixtures_dir / "bad_module_statement.aspec.py"))
    assert {"P002", "P004", "P006"} <= {d.code for d in diags}
