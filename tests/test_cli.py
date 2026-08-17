import ast
import json

import pytest

from agentspec import __version__
from agentspec.cli import SUBCOMMANDS, main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"aspec {__version__}"


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "lint" in capsys.readouterr().out


def test_every_subcommand_is_implemented():
    # No stubs remain: every advertised subcommand has a handler.
    assert set(SUBCOMMANDS) == {
        "lint",
        "plan",
        "graph",
        "fmt",
        "diff",
        "eval",
        "run",
        "studio",
        "lsp",
        "new",
    }


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
        "from agentspec import Task, Rule\n\n"
        "class Out(BaseModel):\n"
        "    ok: bool\n\n"
        "class T(Task):\n"
        '    """Do."""\n'
        "    returns: Out\n"
        '    constraints = [Rule("be-careful", "Be careful")]\n'
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
    assert "BookbankRun — 10 steps across 8 waves" in out
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


UNFORMATTED_SPEC = (
    '"""D."""\n'
    "from pydantic import BaseModel\n"
    "from agentspec import Task\n\n"
    "class Out(BaseModel):\n"
    "    ok:bool\n\n"
    "class T(Task):\n"
    '    """Do."""\n'
    "    on_failure = 'abort'\n"
    "    returns: Out\n"
)


def test_fmt_check_then_write(capsys, tmp_path):
    spec = tmp_path / "spec.aspec.py"
    spec.write_text(UNFORMATTED_SPEC)

    assert main(["fmt", "--check", str(spec)]) == 1
    assert "would reformat" in capsys.readouterr().out
    assert spec.read_text() == UNFORMATTED_SPEC  # --check never writes

    assert main(["fmt", str(spec)]) == 0
    assert "reformatted" in capsys.readouterr().out
    content = spec.read_text()
    assert "ok: bool" in content
    assert content.index("returns: Out") < content.index('on_failure = "abort"')

    assert main(["fmt", "--check", str(spec)]) == 0
    assert "already formatted" in capsys.readouterr().out


def test_fmt_refuses_broken_syntax(capsys, tmp_path):
    spec = tmp_path / "broken.aspec.py"
    spec.write_text("def broken(:\n")
    assert main(["fmt", str(spec)]) == 1
    assert "syntax error" in capsys.readouterr().err


def _write_eval_setup(tmp_path, fixtures_dir, reply):
    adapter = tmp_path / "adapter.py"
    adapter.write_text(f"import sys\nsys.stdin.read()\nprint({reply!r})\n")
    eval_file = tmp_path / "triage.eval.toml"
    eval_file.write_text(
        f'spec = "{fixtures_dir / "minimal_good.aspec.py"}"\n'
        'task = "TriageMessage"\n\n'
        "[[case]]\n"
        'name = "noise stays unfiled"\n'
        "[case.inputs]\n"
        'message_text = "just small talk"\n'
        "[case.expect]\n"
        "actionable = false\n"
    )
    return eval_file, adapter


def test_eval_requires_an_adapter(capsys, tmp_path, fixtures_dir):
    eval_file, _ = _write_eval_setup(tmp_path, fixtures_dir, "{}")
    assert main(["eval", str(eval_file)]) == 2
    assert "--adapter-cmd" in capsys.readouterr().err


def test_eval_pass_and_fail_exit_codes(capsys, tmp_path, fixtures_dir):
    import sys as _sys

    good = '{"actionable": false, "category": "noise", "summary": "ok"}'
    eval_file, adapter = _write_eval_setup(tmp_path, fixtures_dir, good)
    cmd = f"{_sys.executable} {adapter}"
    assert main(["eval", "--adapter-cmd", cmd, str(eval_file)]) == 0
    out = capsys.readouterr().out
    assert "PASS noise stays unfiled" in out
    assert "1 case(s): 1 passed, 0 failed" in out

    bad = '{"actionable": true, "category": "bug", "summary": "file it"}'
    eval_file, adapter = _write_eval_setup(tmp_path, fixtures_dir, bad)
    assert main(["eval", "--adapter-cmd", f"{_sys.executable} {adapter}", str(eval_file)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_run_requires_an_adapter(capsys, fixtures_dir):
    assert main(["run", str(fixtures_dir / "minimal_good.aspec.py")]) == 2
    assert "--adapter-cmd" in capsys.readouterr().err


def test_run_single_agent_cli(capsys, tmp_path, fixtures_dir):
    import sys as _sys

    reply = '## Run report\\nall inferred\\n{"filed": false, "tracker_id": ""}'
    adapter = tmp_path / "adapter.py"
    adapter.write_text(f"import sys\nsys.stdin.read()\nprint('{reply}')\n")
    exit_code = main(
        [
            "run",
            "--adapter-cmd",
            f"{_sys.executable} {adapter}",
            "--context",
            "triage the inbox",
            "--single-agent",
            "--json",
            str(fixtures_dir / "minimal_good.aspec.py"),
        ]
    )
    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "conforming"
    assert result["mode"] == "single"
    assert result["output"] == {"filed": False, "tracker_id": ""}


def test_run_defaults_to_orchestrate_for_orchestrator_roots(capsys, tmp_path, fixtures_dir):
    import sys as _sys

    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        "import sys\n"
        "prompt = sys.stdin.read()\n"
        "if '# Task: TriageMessage' in prompt:\n"
        '    print(\'{"actionable": false, "category": "noise", "summary": "x"}\')\n'
        "else:\n"
        '    print(\'{"filed": false, "tracker_id": ""}\')\n'
    )
    exit_code = main(
        [
            "run",
            "--adapter-cmd",
            f"{_sys.executable} {adapter}",
            "--input",
            "message_text=hello",
            "--json",
            str(fixtures_dir / "minimal_good.aspec.py"),
        ]
    )
    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "orchestrate"  # no flag needed: the root has a pipeline
    assert result["status"] == "conforming"


def test_run_mode_flags_are_mutually_exclusive(capsys, fixtures_dir):
    exit_code = main(
        [
            "run",
            "--adapter-cmd",
            "true",
            "--orchestrate",
            "--single-agent",
            str(fixtures_dir / "minimal_good.aspec.py"),
        ]
    )
    assert exit_code == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_run_bad_input_flag(capsys, fixtures_dir):
    exit_code = main(
        [
            "run",
            "--adapter-cmd",
            "true",
            "--input",
            "not-a-pair",
            str(fixtures_dir / "minimal_good.aspec.py"),
        ]
    )
    assert exit_code == 2
    assert "NAME=VALUE" in capsys.readouterr().err


def test_eval_json_output(capsys, tmp_path, fixtures_dir):
    import sys as _sys

    good = '{"actionable": false, "category": "noise", "summary": "ok"}'
    eval_file, adapter = _write_eval_setup(tmp_path, fixtures_dir, good)
    cmd = f"{_sys.executable} {adapter}"
    assert main(["eval", "--json", "--adapter-cmd", cmd, str(eval_file)]) == 0
    (report,) = json.loads(capsys.readouterr().out)
    assert report["task"] == "TriageMessage"
    assert report["results"][0]["passed"] is True


def test_new_scaffold_is_clean(capsys, tmp_path, monkeypatch):
    target = tmp_path / "nightly_triage"
    assert main(["new", str(target)]) == 0
    out = capsys.readouterr().out
    assert "created" in out and "next:" in out
    spec = tmp_path / "nightly_triage.aspec.py"
    assert spec.exists()
    assert "Nightly Triage" in spec.read_text()

    # the scaffold holds the toolchain to its own standard
    assert main(["lint", "--strict", str(spec)]) == 0
    capsys.readouterr()
    assert main(["fmt", "--check", str(spec)]) == 0
    capsys.readouterr()

    # refuses to overwrite
    assert main(["new", str(target)]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err
