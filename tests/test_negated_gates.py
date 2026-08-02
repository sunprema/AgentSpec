"""Negated gates (spec 2.1): `X(...) if not cond else None` runs on falsity."""

import json

import pytest

from agentspec.diff import diff_modules
from agentspec.lint import lint_module
from agentspec.parser import parse_source
from agentspec.plan import build_plan, render_text
from agentspec.run import orchestrate

SPEC = '''"""Escalate when triage says a message is NOT actionable."""
from pydantic import BaseModel
from agentspec import Task

class Verdict(BaseModel):
    actionable: bool

class Note(BaseModel):
    noted: bool

class Triage(Task):
    """Decide whether the message is actionable."""
    message: str
    returns: Verdict
    on_failure = "abort"

class RecordNoise(Task):
    """Record the message as noise."""
    returns: Note
    on_failure = "abort"

class Routine(Task):
    """Triage, and record noise when nothing is actionable."""
    message: str
    returns: Note

    verdict = Triage(message=message)
    noise   = RecordNoise() if not verdict.actionable else None

    on_failure = "abort"
'''


@pytest.fixture(scope="module")
def module():
    return parse_source(SPEC, "negated.aspec.py")


def test_parses_with_negated_flag(module):
    assert module.ok
    bind = module.tasks["Routine"].bind("noise")
    assert bind.gate_negated is True
    assert bind.gate.path.dotted == "verdict.actionable"
    assert bind.gate_condition() == "not verdict.actionable"


def test_lints_clean(module):
    assert [d for d in lint_module(module) if d.severity == "error"] == []


def test_plan_renders_negated_condition(module):
    plan = build_plan(module, module.tasks["Routine"])
    step = plan.step("noise")
    assert step.gate == "not verdict.actionable"
    assert "not verdict.actionable" in render_text(plan)
    # skip analysis is direction-agnostic: the gated step is still the skip target
    assert plan.gate_skips["noise"] == []


def test_diff_sees_negation_as_condition_change():
    positive = SPEC.replace("if not verdict.actionable", "if verdict.actionable")
    changes = diff_modules(parse_source(positive, "a.aspec.py"), parse_source(SPEC, "b.aspec.py"))
    change = next(c for c in changes if c.code == "gate-condition-changed")
    assert change.old == "verdict.actionable"
    assert change.new == "not verdict.actionable"


def _adapter(actionable):
    def adapter(prompt):
        if "# Task: Triage" in prompt:
            return json.dumps({"actionable": actionable})
        if "# Task: RecordNoise" in prompt:
            return json.dumps({"noted": True})
        if "# Task: Routine" in prompt:
            return json.dumps({"noted": not actionable})
        raise AssertionError(f"unexpected prompt:\n{prompt[:200]}")

    return adapter


def test_orchestrate_runs_step_on_falsity(tmp_path):
    spec = tmp_path / "negated.aspec.py"
    spec.write_text(SPEC)

    result = orchestrate(spec, _adapter(actionable=False), inputs={"message": "hi"})
    assert result.status == "conforming"
    assert [(s.var, s.status) for s in result.steps] == [
        ("verdict", "ok"),
        ("noise", "ok"),
    ]

    result = orchestrate(spec, _adapter(actionable=True), inputs={"message": "hi"})
    assert result.status == "conforming"
    assert [(s.var, s.status) for s in result.steps] == [
        ("verdict", "ok"),
        ("noise", "skipped"),
    ]
