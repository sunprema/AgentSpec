"""Guarded execution: bounded repair, declared failure semantics, orchestrated
data flow, fan-out, gate skips, and the saga unwind — all with fake adapters."""

import json
import re

import pytest

from agentspec.eval import build_output_model
from agentspec.parser import parse_file
from agentspec.run import RunError, guarded_call, orchestrate, run_routine

FILE_REPORT = {"filed": False, "tracker_id": ""}


def parse_prompt(prompt):
    """Recover (task_name, inputs) from a rendered step prompt."""
    match = re.search(r"^# Task: (\w+)$", prompt, re.M)
    task = match.group(1) if match else None
    inputs_text = prompt.split("# Inputs\n", 1)[1].split("\n\n# ", 1)[0]
    return task, json.loads(inputs_text)


# -- the guard ---------------------------------------------------------------


def test_guarded_call_repairs_with_verbatim_violations(fixtures_dir):
    module = parse_file(fixtures_dir / "minimal_good.aspec.py")
    output_model = build_output_model(module, "FileReport")
    prompts = []

    def adapter(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            return '{"filed": "not-a-bool and missing tracker_id"}'
        return '{"filed": true, "tracker_id": "T-1"}'

    outcome = guarded_call(adapter, "do it", output_model, max_repairs=2)
    assert outcome.output == {"filed": True, "tracker_id": "T-1"}
    assert outcome.attempts == 2
    repair = prompts[1]
    assert "did not conform" in repair
    assert "tracker_id" in repair  # the verbatim violation
    assert '{"filed": "not-a-bool and missing tracker_id"}' in repair  # previous reply


def test_guarded_call_gives_up_after_bounded_repairs(fixtures_dir):
    module = parse_file(fixtures_dir / "minimal_good.aspec.py")
    output_model = build_output_model(module, "FileReport")
    calls = []

    def adapter(prompt):
        calls.append(prompt)
        return "still not json"

    outcome = guarded_call(adapter, "do it", output_model, max_repairs=2)
    assert outcome.output is None
    assert outcome.attempts == 3  # 1 initial + 2 repairs
    assert len(calls) == 3


ENVELOPE_BLOCK = (
    "```json envelope\n"
    '{"substitutions": [{"tool": "gh", "used": "github-mcp", '
    '"reason": "gh binary missing"}],\n'
    ' "rule_conflicts": [{"rules": ["a-rule", "b-rule"], '
    '"resolution": "took the conservative branch"}]}\n'
    "```\n"
)


def test_guarded_call_parses_envelope_and_strips_it_from_the_contract(fixtures_dir):
    module = parse_file(fixtures_dir / "minimal_good.aspec.py")
    output_model = build_output_model(module, "FileReport")
    reply = ENVELOPE_BLOCK + json.dumps(FILE_REPORT)

    outcome = guarded_call(lambda _p: reply, "do it", output_model, max_repairs=0)
    assert outcome.output == FILE_REPORT  # the block's braces don't confuse extraction
    (sub,) = outcome.substitutions
    assert (sub.tool, sub.used, sub.reason) == ("gh", "github-mcp", "gh binary missing")
    (conflict,) = outcome.rule_conflicts
    assert conflict.rules == ["a-rule", "b-rule"]
    assert outcome.envelope_warning == ""


def test_guarded_call_malformed_envelope_never_fails_a_conforming_run(fixtures_dir):
    module = parse_file(fixtures_dir / "minimal_good.aspec.py")
    output_model = build_output_model(module, "FileReport")
    reply = "```json envelope\nnot json at all\n```\n" + json.dumps(FILE_REPORT)

    outcome = guarded_call(lambda _p: reply, "do it", output_model, max_repairs=0)
    assert outcome.output == FILE_REPORT
    assert outcome.substitutions == []
    assert "malformed envelope" in outcome.envelope_warning


# -- single-agent run --------------------------------------------------------


def test_run_routine_conforming_with_report(fixtures_dir):
    def adapter(prompt):
        assert "# Root task: InboxRoutine" in prompt
        assert "class InboxRoutine(Task):" in prompt  # whole spec included
        assert "untrusted input" in prompt
        return "## Run report\nverified by command: nothing; all inferred.\n" + json.dumps(
            FILE_REPORT
        )

    result = run_routine(fixtures_dir / "minimal_good.aspec.py", adapter, context="triage my inbox")
    assert result.status == "conforming"
    assert result.output == FILE_REPORT
    assert "verified by command" in result.report
    assert result.adapter_calls == 1


def test_run_routine_collects_the_envelope(fixtures_dir):
    def adapter(prompt):
        assert "json envelope" in prompt  # the reporting channel is instructed
        return "## Run report\nverified: nothing.\n" + ENVELOPE_BLOCK + json.dumps(FILE_REPORT)

    result = run_routine(fixtures_dir / "minimal_good.aspec.py", adapter)
    assert result.status == "conforming"
    assert result.output == FILE_REPORT
    (sub,) = result.substitutions
    assert sub.task == "InboxRoutine"  # harness tags the record with its task
    assert sub.tool == "gh"
    (conflict,) = result.rule_conflicts
    assert conflict.task == "InboxRoutine"
    assert conflict.resolution == "took the conservative branch"


def test_run_routine_abort_when_conformance_unreachable(fixtures_dir):
    result = run_routine(
        fixtures_dir / "minimal_good.aspec.py", lambda _p: "garbage", max_repairs=1
    )
    assert result.status == "aborted"  # InboxRoutine declares on_failure = "abort"
    assert result.output is None
    assert result.adapter_calls == 2  # 1 + 1 repair
    assert any("violation" in note for note in result.notes)


def test_run_routine_declared_literal_fallback(fixtures_dir):
    result = run_routine(
        fixtures_dir / "minimal_good.aspec.py",
        lambda _p: "garbage",
        task_name="TriageMessage",
        inputs={"message_text": "hi"},
        max_repairs=0,
    )
    assert result.status == "declared_fallback"
    assert result.output == {"actionable": False, "category": "noise", "summary": "triage failed"}


def test_run_routine_retry_policy(tmp_path):
    spec = tmp_path / "retrying.aspec.py"
    spec.write_text(
        '"""D."""\n'
        "from pydantic import BaseModel\n"
        "from agentspec import Task, Retry\n\n"
        "class Out(BaseModel):\n"
        "    ok: bool\n\n"
        "class T(Task):\n"
        '    """Do."""\n'
        "    returns: Out\n"
        '    on_failure = Retry(max=2, backoff_s=1, then={"ok": False})\n'
    )
    calls = []

    def adapter(prompt):
        calls.append(prompt)
        return "junk"

    result = run_routine(spec, adapter, max_repairs=0)
    assert result.status == "declared_fallback"
    assert result.output == {"ok": False}
    assert result.adapter_calls == 3  # initial + 2 declared retries


# -- orchestrated run --------------------------------------------------------


def scan_routine_adapter(prompt):
    task, inputs = parse_prompt(prompt)
    if task == "Scan":
        return json.dumps(
            {
                "found": True,
                "findings": [
                    {"id": "a", "severity": "high"},
                    {"id": "b", "severity": "low"},
                ],
                "count": 2,
            }
        )
    if task == "TriageFinding":
        action = "page" if inputs["finding"]["severity"] == "high" else "ignore"
        return json.dumps({"id": inputs["finding"]["id"], "action": action})
    if task == "PageOncall":
        assert inputs["plan_id"] == "a"
        return json.dumps({"paged": True})
    if task == "ScanRoutine":  # the reducer
        collected = inputs["collected_step_results"]
        assert len(collected["plans"]) == 2
        assert len(collected["pages"]) == 1
        return json.dumps({"total": 2, "paged_count": 1})
    raise AssertionError(f"unexpected task {task}")


def test_orchestrate_fanout_filter_and_reduction(fixtures_dir):
    result = orchestrate(fixtures_dir / "fanout_routine.aspec.py", scan_routine_adapter)
    assert result.status == "conforming"
    assert result.output == {"total": 2, "paged_count": 1}
    assert [(s.var, s.status) for s in result.steps] == [
        ("scan", "ok"),
        ("plans", "ok"),
        ("pages", "ok"),
    ]
    assert result.adapter_calls == 5  # scan + 2 triage + 1 page + reducer


def test_orchestrate_item_failure_skips_and_reports(fixtures_dir):
    def adapter(prompt):
        task, inputs = parse_prompt(prompt)
        if task == "TriageFinding" and inputs["finding"]["id"] == "b":
            return "junk"  # this item never conforms
        if task == "ScanRoutine":  # reducer sees the surviving item only
            assert len(inputs["collected_step_results"]["plans"]) == 1
            return json.dumps({"total": 2, "paged_count": 1})
        return scan_routine_adapter(prompt)

    result = orchestrate(fixtures_dir / "fanout_routine.aspec.py", adapter, max_repairs=0)
    assert result.status == "conforming"
    plans = next(s for s in result.steps if s.var == "plans")
    assert plans.status == "ok"
    assert len(plans.output) == 1  # item b dropped
    assert any("plans[1]" in note and "skipped" in note for note in result.notes)


def test_orchestrate_gate_skip_flows_to_reducer(fixtures_dir):
    def adapter(prompt):
        task, inputs = parse_prompt(prompt)
        if task == "TriageMessage":
            return json.dumps({"actionable": False, "category": "noise", "summary": "meh"})
        if task == "InboxRoutine":
            assert inputs["collected_step_results"]["ticket"] is None  # skipped
            assert "env" in inputs and "run_id" in inputs["env"]
            return json.dumps(FILE_REPORT)
        raise AssertionError(f"unexpected task {task}")

    result = orchestrate(
        fixtures_dir / "minimal_good.aspec.py",
        adapter,
        inputs={"message_text": "just chatter"},
    )
    assert result.status == "conforming"
    assert [(s.var, s.status) for s in result.steps] == [
        ("triage", "ok"),
        ("ticket", "skipped"),
    ]
    assert result.adapter_calls == 2  # triage + reducer; the gate cost nothing


def test_orchestrate_abort_unwinds_in_reverse(tmp_path):
    spec = tmp_path / "unwind.aspec.py"
    spec.write_text('''"""D."""
from pydantic import BaseModel
from agentspec import Task

class Out(BaseModel):
    ok: bool

class A(Task):
    """Create the thing."""
    returns: Out
    undo = "Delete the thing A created."
    on_failure = "abort"

class B(Task):
    """Break."""
    returns: Out
    on_failure = "abort"

class R(Task):
    """Route."""
    returns: Out
    a = A()
    b = B() if a.ok else None
    on_failure = "abort"
''')
    undo_prompts = []

    def adapter(prompt):
        if "saga unwind" in prompt:
            undo_prompts.append(prompt)
            return "deleted the thing"
        task, _ = parse_prompt(prompt)
        if task == "A":
            return '{"ok": true}'
        return "junk"  # B never conforms

    result = orchestrate(spec, adapter, max_repairs=0)
    assert result.status == "aborted"
    assert [(s.var, s.status) for s in result.steps] == [("a", "unwound"), ("b", "failed")]
    (undo_prompt,) = undo_prompts
    assert "Delete the thing A created." in undo_prompt
    assert any("unwound a" in note for note in result.notes)
    assert result.steps[0].undo_report == "deleted the thing"  # structured in the envelope


def test_orchestrate_tags_envelope_records_per_step(fixtures_dir):
    def adapter(prompt):
        task, _ = parse_prompt(prompt)
        if task == "TriageMessage":
            return ENVELOPE_BLOCK + json.dumps(
                {"actionable": False, "category": "noise", "summary": "meh"}
            )
        return json.dumps(FILE_REPORT)

    result = orchestrate(
        fixtures_dir / "minimal_good.aspec.py",
        adapter,
        inputs={"message_text": "just chatter"},
    )
    assert result.status == "conforming"
    (sub,) = result.substitutions
    assert (sub.task, sub.tool, sub.used) == ("TriageMessage", "gh", "github-mcp")
    (conflict,) = result.rule_conflicts
    assert conflict.task == "TriageMessage"


def test_orchestrate_inherits_parent_constraints(fixtures_dir):
    seen = {}

    def adapter(prompt):
        task, _ = parse_prompt(prompt)
        seen[task] = prompt
        if task == "TriageMessage":
            return json.dumps({"actionable": False, "category": "noise", "summary": "x"})
        return json.dumps(FILE_REPORT)

    orchestrate(
        fixtures_dir / "minimal_good.aspec.py",
        adapter,
        inputs={"message_text": "hello"},
    )
    # HOUSE_RULES binds the step both via its own constraints and inherited —
    # deduped, so it appears exactly once.
    assert seen["TriageMessage"].count("Never modify anything outside the inbox") == 1


def test_orchestrate_requires_a_pipeline(fixtures_dir):
    with pytest.raises(RunError, match="no pipeline"):
        orchestrate(
            fixtures_dir / "minimal_good.aspec.py",
            lambda _p: "{}",
            task_name="TriageMessage",
        )
