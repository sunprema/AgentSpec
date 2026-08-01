"""Eval runner: contract validation, declarative assertions, and the
adapter boundary — all exercised with fake adapters, no model involved."""

import json

import pytest
from pydantic import ValidationError

from agentspec.eval import (
    EvalError,
    build_output_model,
    check_expectations,
    extract_json,
    run_eval,
)
from agentspec.parser import parse_file

CONSERVATIVE_ISSUE_REF = {
    "found": False,
    "proceed": False,
    "number": 0,
    "redo_requested": False,
    "already_in_progress": False,
    "stale_branch": "",
}


@pytest.fixture(scope="module")
def bookbank(fixtures_dir):
    return parse_file(fixtures_dir / "bookbank_routine.aspec.py")


def test_output_model_enforces_the_declared_contract(bookbank):
    report = build_output_model(bookbank, "RunReport")
    good = {
        "outcome": "published_draft",
        "stopped_at": "complete",
        "validator_errors": 0,
        "summary": "ok",
        "operator_action": "",
    }
    assert report.model_validate(good).outcome == "published_draft"

    with pytest.raises(ValidationError):  # closed enum
        report.model_validate({**good, "outcome": "made_up_outcome"})
    with pytest.raises(ValidationError):  # Field(ge=0)
        report.model_validate({**good, "validator_errors": -1})
    with pytest.raises(ValidationError):  # Field(max_length=500)
        report.model_validate({**good, "summary": "x" * 501})
    with pytest.raises(ValidationError):  # extra fields are forbidden
        report.model_validate({**good, "invented": True})
    with pytest.raises(ValidationError):  # missing required field
        report.model_validate({k: v for k, v in good.items() if k != "summary"})


def test_output_model_handles_lists_and_optionals(fixtures_dir):
    module = parse_file(fixtures_dir / "fanout_routine.aspec.py")
    scan = build_output_model(module, "ScanResult")
    ok = scan.model_validate(
        {"found": True, "findings": [{"id": "a", "severity": "high"}], "count": 1}
    )
    assert ok.findings[0].severity == "high"
    with pytest.raises(ValidationError):  # nested closed enum
        scan.model_validate(
            {"found": True, "findings": [{"id": "a", "severity": "urgent"}], "count": 1}
        )


def test_extract_json():
    assert extract_json('{"ok": true}') == {"ok": True}
    assert extract_json('Sure! Here it is:\n```json\n{"ok": true}\n```') == {"ok": True}
    with pytest.raises(EvalError):
        extract_json("no json here")


def test_check_expectations_operators():
    output = {"tier": "a", "value": 42, "summary": "needs review", "plan": {"n": 3}}
    assert check_expectations({"tier": "a"}, output) == []
    assert check_expectations({"tier": {"in": ["a", "b"]}}, output) == []
    assert check_expectations({"value": {"gte": 40, "lte": 50}}, output) == []
    assert check_expectations({"summary": {"contains": "review"}}, output) == []
    assert check_expectations({"plan.n": 3}, output) == []  # dotted paths
    assert check_expectations({"tier": {"not_in": ["c"]}}, output) == []

    failures = check_expectations({"tier": "b", "value": {"gte": 100}, "missing": 1}, output)
    assert len(failures) == 3
    assert any("not present" in f for f in failures)


def write_eval(tmp_path, fixtures_dir, body):
    eval_file = tmp_path / "case.eval.toml"
    spec = fixtures_dir / "minimal_good.aspec.py"
    eval_file.write_text(f'spec = "{spec}"\n{body}')
    return eval_file


TRIAGE_EVAL = """task = "TriageMessage"

[[case]]
name = "noise stays unfiled"
[case.inputs]
message_text = "btw the product name came up at lunch"
[case.expect]
actionable = false
category = { in = ["noise", "question"] }
"""


def test_run_eval_end_to_end(tmp_path, fixtures_dir):
    eval_file = write_eval(tmp_path, fixtures_dir, TRIAGE_EVAL)
    prompts = []

    def adapter(prompt):
        prompts.append(prompt)
        reply = {"actionable": False, "category": "noise", "summary": "small talk"}
        return f"Verdict follows.\n{json.dumps(reply)}"

    report = run_eval(eval_file, adapter)
    assert report.passed
    (result,) = report.results
    assert result.output["category"] == "noise"
    # the prompt carries the task's contract
    (prompt,) = prompts
    assert "Task: TriageMessage" in prompt
    assert "name-dropping is the most common false positive" in prompt  # rule why
    assert "Output contract" in prompt
    assert '"message_text"' in prompt


def test_run_eval_schema_violation_is_a_case_failure(tmp_path, fixtures_dir):
    eval_file = write_eval(tmp_path, fixtures_dir, TRIAGE_EVAL)
    report = run_eval(
        eval_file,
        lambda _p: '{"actionable": false, "category": "not_an_enum_value", "summary": ""}',
    )
    assert not report.passed
    assert any("schema: category" in f for f in report.results[0].failures)


def test_run_eval_adapter_error_is_a_case_failure(tmp_path, fixtures_dir):
    eval_file = write_eval(tmp_path, fixtures_dir, TRIAGE_EVAL)

    def broken(_prompt):
        raise RuntimeError("boom")

    report = run_eval(eval_file, broken)
    assert not report.passed
    assert "adapter: boom" in report.results[0].failures[0]


def test_run_eval_rejects_bad_files(tmp_path, fixtures_dir):
    with pytest.raises(EvalError, match="unknown input"):
        run_eval(
            write_eval(
                tmp_path,
                fixtures_dir,
                'task = "TriageMessage"\n[[case]]\nname = "x"\n[case.inputs]\nbogus = "y"\n',
            ),
            lambda _p: "{}",
        )
    with pytest.raises(EvalError, match="required input"):
        run_eval(
            write_eval(tmp_path, fixtures_dir, 'task = "TriageMessage"\n[[case]]\nname = "x"\n'),
            lambda _p: "{}",
        )
    with pytest.raises(EvalError, match="not found"):
        run_eval(
            write_eval(tmp_path, fixtures_dir, 'task = "Nope"\n'),
            lambda _p: "{}",
        )


def test_bookbank_select_issue_eval_artifact():
    """The checked-in SelectIssue eval is well-formed: a conservative
    on_uncertain reply satisfies the first two cases (doubt de-escalates)
    and correctly fails the explicit-redo case."""
    from pathlib import Path

    eval_path = Path(__file__).parent.parent / "docs" / "evals" / "SelectIssue.eval.toml"
    report = run_eval(eval_path, lambda _p: json.dumps(CONSERVATIVE_ISSUE_REF))
    assert report.task == "SelectIssue"
    assert [r.passed for r in report.results] == [True, True, False]
    assert "redo_requested" in report.results[2].failures[0]
