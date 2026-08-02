"""Dev mode: clarifications at declared doubt points, gaps reported.

Production semantics are unattended; these tests pin all three design
rules — askability derives from declared doubt, declines fall back to the
declared path, and unattended runs treat questions as violations.
"""

import json

import pytest

from agentspec.eval import build_output_model
from agentspec.eval.runner import render_prompt
from agentspec.parser import parse_source
from agentspec.run import orchestrate, run_routine
from agentspec.run.guard import guarded_call
from agentspec.run.single import has_declared_doubt

SPEC = '''"""Dev-mode fixture."""
from pydantic import BaseModel
from agentspec import Task

class Out(BaseModel):
    ok: bool
    note: str

class Sure(Task):
    """A task that declared no doubt: it never gets to ask."""
    returns: Out
    on_failure = "abort"

class Unsure(Task):
    """A task with a declared doubt path."""
    hint: str
    returns: Out
    on_uncertain = {"ok": False, "note": "could not decide"}
    on_failure = {"ok": False, "note": "failed"}

class Routine(Task):
    """Run both."""
    returns: Out
    a = Sure()
    b = Unsure(hint="see dispatch")
    on_uncertain = {"ok": False, "note": "routine uncertain"}
    on_failure = "abort"
'''


@pytest.fixture(scope="module")
def module():
    return parse_source(SPEC, "devmode.aspec.py")


def _model(module, task_name):
    return build_output_model(module, module.tasks[task_name].returns.name)


# ---------------- the guard loop ----------------


def test_ask_then_conform(module):
    prompts = []

    def adapter(prompt):
        prompts.append(prompt)
        if "Answer from the developer: use the blue config" in prompt:
            return json.dumps({"ok": True, "note": "used blue config"})
        return json.dumps({"question": "which config should I use?"})

    outcome = guarded_call(
        adapter,
        "base prompt",
        _model(module, "Unsure"),
        ask=lambda q: "use the blue config",
    )
    assert outcome.output == {"ok": True, "note": "used blue config"}
    assert len(outcome.clarifications) == 1
    assert outcome.clarifications[0].question == "which config should I use?"
    assert outcome.clarifications[0].answer == "use the blue config"
    assert outcome.attempts == 2  # question round + answer round


def test_decline_falls_back_to_declared_path(module):
    def adapter(prompt):
        if "declined to answer" in prompt:
            return json.dumps({"ok": False, "note": "could not decide"})
        return json.dumps({"question": "really?"})

    outcome = guarded_call(adapter, "base", _model(module, "Unsure"), ask=lambda q: None)
    assert outcome.output == {"ok": False, "note": "could not decide"}
    assert outcome.clarifications[0].answer is None


def test_one_question_budget(module):
    def adapter(prompt):
        return json.dumps({"question": "again?"})  # asks forever

    outcome = guarded_call(
        adapter, "base", _model(module, "Unsure"), ask=lambda q: "answered", max_repairs=1
    )
    assert outcome.output is None
    assert len(outcome.clarifications) == 1  # the budget held


def test_unattended_question_is_a_violation(module):
    replies = iter(
        [
            json.dumps({"question": "may I ask?"}),
            json.dumps({"ok": False, "note": "proceeded without asking"}),
        ]
    )

    def adapter(prompt):
        if "not permitted in unattended dispatch" in prompt:
            return next(replies)  # the repair prompt carries the message
        return next(replies)

    outcome = guarded_call(adapter, "base", _model(module, "Unsure"))  # no ask
    assert outcome.output == {"ok": False, "note": "proceeded without asking"}
    assert any("not permitted in unattended dispatch" in f for f in outcome.failures)
    assert outcome.clarifications == []


# ---------------- doubt gating ----------------


def test_askability_derives_from_declared_doubt(module):
    assert has_declared_doubt(module.tasks["Unsure"])
    assert not has_declared_doubt(module.tasks["Sure"])

    sure_prompt = render_prompt(
        module, module.tasks["Sure"], {}, _model(module, "Sure"), allow_question=False
    )
    unsure_prompt = render_prompt(
        module, module.tasks["Unsure"], {"hint": "x"}, _model(module, "Unsure"), allow_question=True
    )
    assert "Never ask questions" in sure_prompt
    assert '{"question": "..."}' in unsure_prompt


# ---------------- end to end ----------------


def test_orchestrate_dev_run_collects_gaps(tmp_path):
    spec = tmp_path / "devmode.aspec.py"
    spec.write_text(SPEC)

    def adapter(prompt):
        if "# Task: Sure" in prompt:
            assert "Never ask questions" in prompt  # no doubt declared: no permission
            return json.dumps({"ok": True, "note": "sure"})
        if "# Task: Unsure" in prompt:
            if "Answer from the developer: blue" in prompt:
                return json.dumps({"ok": True, "note": "blue"})
            return json.dumps({"question": "blue or green?"})
        if "# Task: Routine" in prompt:
            return json.dumps({"ok": True, "note": "done"})
        raise AssertionError(prompt[:120])

    result = orchestrate(spec, adapter, ask=lambda q: "blue")
    assert result.status == "conforming"
    [gap] = result.clarifications
    assert gap.task == "Unsure"
    assert gap.question == "blue or green?"
    assert gap.answer == "blue"


def test_single_mode_dev_run(tmp_path):
    spec = tmp_path / "devmode.aspec.py"
    spec.write_text(SPEC)

    def adapter(prompt):
        if "Answer from the developer: proceed" in prompt:
            return json.dumps({"ok": True, "note": "proceeded"})
        if '{"question": "..."}' in prompt:
            return json.dumps({"question": "proceed?"})
        return json.dumps({"ok": True, "note": "no question offered"})

    result = run_routine(spec, adapter, ask=lambda q: "proceed")
    assert result.status == "conforming"
    assert [c.task for c in result.clarifications] == ["Routine"]


def test_cli_advertises_dev_flag():
    from agentspec.cli import build_parser

    args = build_parser().parse_args(["run", "x.aspec.py", "--adapter-cmd", "cat", "--dev"])
    assert args.dev is True
