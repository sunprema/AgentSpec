"""Declared endings (spec 2.6): Outcome lists as specialized derivations —
parsing, lint reachability and alert coverage, gates on outcomes.alert,
mechanical evaluation, graph rendering."""

import json

import pytest

from agentspec.graph import render_mermaid
from agentspec.lint import lint_module
from agentspec.parser import parse_source
from agentspec.run import orchestrate

SPEC = '''"""Check the thing and end deliberately."""
from pydantic import BaseModel, Field
from agentspec import Task, Enum, Outcome

class Verdict(BaseModel):
    ok: bool
    count: int = Field(ge=0)

class Note(BaseModel):
    noted: bool

class Report(BaseModel):
    outcome: Enum["failed", "busy", "fine"]
    n: int

class Check(Task):
    """Check the thing."""
    returns: Verdict
    on_failure = {"ok": False, "count": 0}

class Page(Task):
    """Page a human."""
    outcome: str
    returns: Note
    on_failure = "abort"

class Routine(Task):
    """Check, end, page if the ending says so."""
    returns: Report

    check = Check()
    outcomes = [
        Outcome("failed", when=not check.ok, alert=True, n=0),
        Outcome("busy", when=check.count > 3, alert=True, n=check.count),
        Outcome("fine", when=True, alert=False, n=check.count),
    ]
    page = Page(outcome=outcomes.outcome) if outcomes.alert else None

    on_failure = "abort"
'''


@pytest.fixture(scope="module")
def module():
    return parse_source(SPEC, "ended.aspec.py")


def _diag_codes(src):
    module = parse_source(src, "ended.aspec.py")
    return {d.code for d in module.diagnostics} | {d.code for d in lint_module(module)}


# -- parsing -----------------------------------------------------------------


def test_outcomes_parse_as_a_specialized_derivation(module):
    assert module.ok
    [outcomes] = module.tasks["Routine"].derivations
    assert outcomes.var == "outcomes" and outcomes.style == "outcomes"
    assert [row.raw for row in outcomes.rows] == ["not check.ok", "check.count > 3", "True"]
    assert outcomes.has_catch_all
    first = outcomes.rows[0].output
    assert first["outcome"].value == "failed"
    assert first["alert"].value is True
    assert first["n"].value == 0
    assert outcomes.rows[1].output["n"].path.dotted == "check.count"


def test_lints_clean_and_gate_on_alert_is_legal(module):
    assert lint_module(module) == []


def test_p016_missing_when_alert_or_bad_entries():
    no_when = SPEC.replace(
        'Outcome("fine", when=True, alert=False, n=check.count)',
        'Outcome("fine", alert=False, n=check.count)',
    )
    assert "P016" in _diag_codes(no_when)

    no_alert = SPEC.replace(
        'Outcome("fine", when=True, alert=False, n=check.count)',
        'Outcome("fine", when=True, n=check.count)',
    )
    assert "P016" in _diag_codes(no_alert)

    stray = SPEC.replace(
        'Outcome("failed", when=not check.ok, alert=True, n=0),',
        'Outcome("failed", when=not check.ok, alert=True, n=0), "junk",',
    )
    assert "P016" in _diag_codes(stray)


# -- lint --------------------------------------------------------------------


def test_as043_when_no_catch_all_ending():
    src = SPEC.replace('        Outcome("fine", when=True, alert=False, n=check.count),\n', "")
    assert src != SPEC
    codes = _diag_codes(src)
    assert "AS043" in codes  # no True ending
    assert "AS042" in codes  # and 'fine' becomes unreachable


def test_as041_outcome_name_outside_returns_enum():
    src = SPEC.replace('Outcome("busy",', 'Outcome("swamped",')
    assert "AS041" in _diag_codes(src)


def test_as042_enum_value_no_ending_produces():
    src = SPEC.replace('Enum["failed", "busy", "fine"]', 'Enum["failed", "busy", "fine", "stuck"]')
    assert "AS042" in _diag_codes(src)


def test_as049_duplicate_condition_is_a_dead_ending():
    src = SPEC.replace(
        '        Outcome("busy", when=check.count > 3, alert=True, n=check.count),\n',
        '        Outcome("busy", when=check.count > 3, alert=True, n=check.count),\n'
        '        Outcome("failed", when=check.count > 3, alert=True, n=0),\n',
    )
    assert src != SPEC
    assert "AS049" in _diag_codes(src)


def test_same_name_reachable_two_ways_is_legal():
    src = SPEC.replace(
        '        Outcome("busy", when=check.count > 3, alert=True, n=check.count),\n',
        '        Outcome("busy", when=check.count > 3, alert=True, n=check.count),\n'
        '        Outcome("busy", when=check.count > 1, alert=True, n=check.count),\n',
    )
    assert src != SPEC
    module = parse_source(src, "ended.aspec.py")
    assert lint_module(module) == []


# -- execution and graph -----------------------------------------------------


def test_orchestrate_matches_endings_mechanically(tmp_path):
    spec = tmp_path / "ended.aspec.py"
    spec.write_text(SPEC)

    def adapter(prompt):
        if "# Task: Check" in prompt:
            return json.dumps({"ok": True, "count": 9})
        if "# Task: Page" in prompt:
            assert '"outcome": "busy"' in prompt
            return json.dumps({"noted": True})
        if "# Task: Routine" in prompt:
            return json.dumps({"outcome": "busy", "n": 9})
        raise AssertionError(prompt[:120])

    result = orchestrate(spec, adapter)
    assert result.status == "conforming"
    outcomes = next(s for s in result.steps if s.var == "outcomes")
    assert outcomes.status == "ok"
    assert next(s for s in result.steps if s.var == "page").status == "ok"


def test_orchestrate_quiet_ending_skips_the_alert_gate(tmp_path):
    spec = tmp_path / "ended.aspec.py"
    spec.write_text(SPEC)

    def adapter(prompt):
        if "# Task: Check" in prompt:
            return json.dumps({"ok": True, "count": 1})
        if "# Task: Routine" in prompt:
            return json.dumps({"outcome": "fine", "n": 1})
        raise AssertionError(prompt[:120])

    result = orchestrate(spec, adapter)
    assert result.status == "conforming"
    assert next(s for s in result.steps if s.var == "page").status == "skipped"


def test_graph_labels_declared_endings(module):
    out = render_mermaid(module, module.tasks["Routine"])
    assert '    outcomes{{"outcomes = declared endings"}}' in out
    assert "    check --> outcomes" in out
    assert '    outcomes -. "outcomes.alert" .-> page' in out
