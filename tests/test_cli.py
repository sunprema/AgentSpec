import ast
import json

import pytest

from agentspec import __version__
from agentspec.cli import SUBCOMMANDS, main

STUB_COMMANDS = [c for c in SUBCOMMANDS if c not in ("lint", "plan", "graph")]


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"aspec {__version__}"


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "lint" in capsys.readouterr().out


@pytest.mark.parametrize("command", STUB_COMMANDS)
def test_unimplemented_subcommands_are_stubs(command, capsys, fixtures_dir):
    spec = str(fixtures_dir / "minimal_good.aspec.py")
    assert main([command, spec]) == 2
    assert "not implemented" in capsys.readouterr().err


@pytest.mark.parametrize(
    "fixture",
    [
        "bookbank_routine.aspec.py",
        "minimal_good.aspec.py",
        "fanout_routine.aspec.py",
        "bad_module_statement.aspec.py",
        "imports_main.aspec.py",
        "shared_rules.aspec.py",
    ],
)
def test_fixtures_are_parseable_python(fixture, fixtures_dir):
    # Every fixture must be syntactically valid Python — even the bad ones,
    # whose badness is semantic (conformance), not syntactic.
    ast.parse((fixtures_dir / fixture).read_text(), filename=fixture)


def test_lint_clean_spec_exits_zero(capsys, fixtures_dir):
    assert main(["lint", str(fixtures_dir / "minimal_good.aspec.py")]) == 0
    assert capsys.readouterr().out.strip() == "clean"


def test_lint_bad_spec_exits_one(capsys, fixtures_dir):
    assert main(["lint", str(fixtures_dir / "bad_module_statement.aspec.py")]) == 1
    out = capsys.readouterr().out
    assert "P002" in out
    assert "error(s)" in out


def test_lint_json_output(capsys, fixtures_dir):
    assert main(["lint", "--json", str(fixtures_dir / "minimal_good.aspec.py")]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_lint_strict_promotes_warnings(capsys, tmp_path):
    spec = tmp_path / "warn_only.aspec.py"
    spec.write_text(
        '"""D."""\n'
        "from pydantic import BaseModel\n"
        "from agentspec import Task\n\n"
        "class Out(BaseModel):\n"
        "    ok: bool\n\n"
        "class T(Task):\n"
        '    """Do."""\n'
        "    returns: Out\n"
        '    constraints = ["Be careful"]\n'
    )
    assert main(["lint", str(spec)]) == 0  # warnings alone stay exit 0
    capsys.readouterr()
    assert main(["lint", "--strict", str(spec)]) == 2
    assert "AS030" in capsys.readouterr().out


def test_lint_missing_file(capsys):
    assert main(["lint", "no_such_file.aspec.py"]) == 1
    assert "aspec lint:" in capsys.readouterr().err


def test_plan_text_output(capsys, fixtures_dir):
    assert main(["plan", str(fixtures_dir / "bookbank_routine.aspec.py")]) == 0
    out = capsys.readouterr().out
    assert "BookbankRun — 8 steps across 7 waves" in out
    assert "plugin = VerifyPlugin  [gate: workspace.resolved]" in out
    assert "false gates skip:" in out


def test_plan_json_output(capsys, fixtures_dir):
    assert main(["plan", "--json", str(fixtures_dir / "fanout_routine.aspec.py")]) == 0
    (plan,) = json.loads(capsys.readouterr().out)
    assert plan["orchestrator"] == "ScanRoutine"
    assert plan["waves"] == [["scan"], ["plans"], ["pages"]]


def test_plan_refuses_broken_spec(capsys, fixtures_dir):
    assert main(["plan", str(fixtures_dir / "bad_module_statement.aspec.py")]) == 1
    assert "parse errors" in capsys.readouterr().err


def test_plan_no_orchestrator(capsys, fixtures_dir):
    assert main(["plan", str(fixtures_dir / "shared_rules.aspec.py")]) == 0
    assert "no orchestrator" in capsys.readouterr().out


def test_graph_stdout(capsys, fixtures_dir):
    assert main(["graph", str(fixtures_dir / "minimal_good.aspec.py")]) == 0
    out = capsys.readouterr().out
    assert "```mermaid\nflowchart TD" in out
    assert '    triage -. "triage.actionable" .-> ticket' in out


def test_graph_out_file(capsys, tmp_path, fixtures_dir):
    out_file = tmp_path / "graph.md"
    assert (
        main(
            [
                "graph",
                "--failures",
                "--out",
                str(out_file),
                str(fixtures_dir / "bookbank_routine.aspec.py"),
            ]
        )
        == 0
    )
    assert f"wrote {out_file}" in capsys.readouterr().out
    content = out_file.read_text()
    assert "## BookbankRun" in content
    assert "_unwind" in content


def test_graph_refuses_broken_spec(capsys, fixtures_dir):
    assert main(["graph", str(fixtures_dir / "bad_module_statement.aspec.py")]) == 1
    assert "parse errors" in capsys.readouterr().err
