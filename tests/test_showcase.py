"""Showcases stay honest: lint-clean, fmt-canonical, stable diff story."""

from pathlib import Path

import pytest

from agentspec.diff import diff_modules
from agentspec.eval import run_eval
from agentspec.lint import lint_module
from agentspec.parser import parse_file
from agentspec.plan import build_plan

SHOWCASE = Path(__file__).parent.parent / "showcase"


@pytest.fixture(scope="module")
def depbot():
    return parse_file(SHOWCASE / "depbot" / "depbot.aspec.py")


def test_depbot_lints_clean_strict(depbot):
    assert lint_module(depbot) == []


def test_depbot_is_fmt_canonical():
    from agentspec.fmt import format_source

    path = SHOWCASE / "depbot" / "depbot.aspec.py"
    source = path.read_text()
    assert format_source(source, str(path)) == source


def test_depbot_plan_shape(depbot):
    plan = build_plan(depbot, depbot.tasks["DepbotRun"])
    assert plan.warnings == []  # pr/cleanup complementary gates: not a race
    cleanup = plan.step("cleanup")
    assert cleanup.gate == "not tests.passed"
    # notify runs on every terminal path: no false gate ever skips it
    assert all("notify" not in skips for skips in plan.gate_skips.values())
    assert plan.step("route").task == "<derived>"


def test_risky_variant_diff_story(depbot):
    risky = parse_file(SHOWCASE / "depbot" / "depbot-risky.aspec.py")
    changes = diff_modules(depbot, risky)
    widening = {(c.code, c.owner) for c in changes if c.direction == "widens"}
    assert widening == {
        ("tool-surface-widened", "OpenPr"),
        ("rule-weakened", "OpenPr"),
        ("undo-removed", "OpenPr"),
    }
    # and lint independently flags the dropped undo on the exclusive tool
    assert any(d.code == "AS034" for d in lint_module(risky))


def test_eval_artifact_is_well_formed():
    # a canned conservative reply proves the eval file parses and runs;
    # real behavior pinning needs a real adapter
    reply = '{"selected": false, "name": "", "target": "", "kind": "patch", "rationale": "canned"}'
    report = run_eval(SHOWCASE / "depbot" / "SelectUpdate.eval.toml", lambda prompt: reply)
    assert len(report.results) == 3
    assert report.results[2].passed  # empty queue -> selected false


def test_export_is_committed_and_current():
    html = (SHOWCASE / "depbot" / "depbot.html").read_text()
    assert "window.EMBEDDED_SPEC" in html
    assert '"root": "DepbotRun"' in html


# ---------------- oncall-triage ----------------


@pytest.fixture(scope="module")
def oncall():
    return parse_file(SHOWCASE / "oncall-triage" / "oncall-triage.aspec.py")


def test_oncall_lints_clean_strict(oncall):
    assert lint_module(oncall) == []


def test_oncall_is_fmt_canonical():
    from agentspec.fmt import format_source

    path = SHOWCASE / "oncall-triage" / "oncall-triage.aspec.py"
    source = path.read_text()
    assert format_source(source, str(path)) == source


def test_oncall_blindness_still_pages(oncall):
    """The showcase's core safety property: a failed hypothesis or missing
    diagnostics can never silence the pager on an actionable alert."""
    plan = build_plan(oncall, oncall.tasks["OncallRun"])
    assert plan.step("page").gate == "triage.page"  # gated on the derived value
    # hypo's false gate must skip nothing but itself — page and ticket
    # tolerate a missing statement (str | None)
    assert plan.gate_skips["hypo"] == []
    # the triage derivation floors severity at sev2 when diagnostics failed
    triage = oncall.tasks["OncallRun"].derivation("triage")
    blind_row = next(r for r in triage.rows if r.raw == "not diag.collected")
    assert blind_row.output["severity"].value == "sev2"
    assert blind_row.output["page"].value is True


def test_oncall_escalation_is_declared(oncall):
    policy = oncall.tasks["PageOncall"].on_failure
    assert policy.kind == "escalate"
    assert policy.channel == "secondary-oncall"
    assert policy.timeout_s == 600
    assert policy.then.kind == "literal"


def test_oncall_risky_diff_story(oncall):
    risky = parse_file(SHOWCASE / "oncall-triage" / "oncall-triage-risky.aspec.py")
    changes = diff_modules(oncall, risky)
    widening = [c for c in changes if c.direction == "widens"]
    assert len(widening) == 7
    surface = next(c for c in widening if c.code == "tool-surface-widened")
    assert "delete pod" in surface.detail
    # the weakened shared doctrine reports on every task it binds
    weakened_owners = {c.owner for c in widening if c.code == "rule-weakened"}
    assert len(weakened_owners) == 6


def test_oncall_eval_artifact_is_well_formed():
    reply = (
        '{"parsed": true, "actionable": true, "service": "checkout",'
        ' "env": "prod", "customer_facing": true, "summary": "canned"}'
    )
    report = run_eval(SHOWCASE / "oncall-triage" / "ParseAlert.eval.toml", lambda prompt: reply)
    assert len(report.results) == 3
    assert report.results[1].passed  # the injection case expects actionable=true


def test_oncall_export_is_committed_and_current():
    html = (SHOWCASE / "oncall-triage" / "oncall-triage.html").read_text()
    assert "window.EMBEDDED_SPEC" in html
    assert '"root": "OncallRun"' in html
