"""Execution-wave derivation: the BookBank plan must match the hand-derived
expectation, plus snippet tests for serialization and false concurrency."""

import pytest

from agentspec.parser import parse_file, parse_source
from agentspec.plan import build_plans


@pytest.fixture(scope="module")
def bookbank(fixtures_dir):
    (plan,) = build_plans(parse_file(fixtures_dir / "bookbank_routine.aspec.py"))
    return plan


def test_bookbank_waves(bookbank):
    # Hand-derived: a strict chain up to the build, then art and notify run
    # concurrently, then the alert joins everything.
    assert bookbank.orchestrator == "BookbankRun"
    assert bookbank.waves == [
        ["workspace"],
        ["plugin"],
        ["issue"],
        ["mark"],
        ["build"],
        ["art", "notify"],
        ["alert"],
    ]


def test_bookbank_joins_and_deps(bookbank):
    assert bookbank.step("build").depends_on == ["issue", "mark", "plugin"]
    assert bookbank.step("alert").depends_on == [
        "art",
        "build",
        "issue",
        "notify",
        "plugin",
        "workspace",
    ]
    assert bookbank.step("workspace").depends_on == []


def test_bookbank_gate_skips(bookbank):
    assert bookbank.gate_skips == {
        "plugin": ["issue", "mark", "build", "art", "notify"],
        "issue": ["mark", "build", "art", "notify"],
        "mark": ["build", "art", "notify"],
        "build": ["art", "notify"],
        "art": [],
        "notify": [],
    }


def test_alert_survives_every_gate(bookbank):
    """PushAlert's inputs are all `X | None`, so no false gate can skip it —
    the plan proves the 'last action on EVERY terminal path' constraint."""
    for skipped in bookbank.gate_skips.values():
        assert "alert" not in skipped


def test_bookbank_no_warnings(bookbank):
    assert bookbank.warnings == []
    assert not bookbank.step("build").serialized  # exclusive git, but not a fan-out
    assert bookbank.step("build").exclusive_tools == ["git"]


def test_fanout_fixture_waves(fixtures_dir):
    (plan,) = build_plans(parse_file(fixtures_dir / "fanout_routine.aspec.py"))
    assert plan.waves == [["scan"], ["plans"], ["pages"]]
    pages = plan.step("pages")
    assert pages.fanout_over == "plans"
    assert pages.filter == "p.action in ['page']"
    assert not pages.serialized


SNIPPET_HEADER = '''"""Doc."""
from pydantic import BaseModel
from agentspec import Task, Tool

class Out(BaseModel):
    ok: bool
    label: str

class Batch(BaseModel):
    items: list[Out]

class Scan(Task):
    """Scan."""
    returns: Batch

'''


def test_fanout_over_exclusive_tool_serializes():
    module = parse_source(
        SNIPPET_HEADER
        + '''class Mut(Task):
    """Mutate one item."""
    v: str
    returns: Out
    tools = [Tool("git", ops=["push"], exclusive=True)]
    undo = "Revert the push."
    on_item_failure = "abort"

class R(Task):
    """Route."""
    returns: Out
    scan = Scan()
    work = [Mut(v=i.label) for i in scan.items]
'''
    )
    (plan,) = build_plans(module)
    work = plan.step("work")
    assert work.serialized
    assert work.exclusive_tools == ["git"]


def test_false_concurrency_warning():
    module = parse_source(
        SNIPPET_HEADER
        + '''class A(Task):
    """A."""
    returns: Out
    tools = [Tool("git", ops=["push"], exclusive=True)]
    undo = "Revert."

class B(Task):
    """B."""
    returns: Out
    tools = [Tool("git", ops=["push"], exclusive=True)]
    undo = "Revert."

class R(Task):
    """Route."""
    returns: Out
    a = A()
    b = B()
'''
    )
    (plan,) = build_plans(module)
    assert plan.waves == [["a", "b"]]
    (warning,) = plan.warnings
    assert "a, b" in warning
    assert "'git'" in warning
    assert "missing bind?" in warning


def test_skip_does_not_propagate_through_optional_inputs():
    module = parse_source(
        SNIPPET_HEADER
        + '''class Work(Task):
    """Work."""
    returns: Out

class Report(Task):
    """Report, tolerating a skipped work step."""
    work: Out | None
    returns: Out

class R(Task):
    """Route."""
    returns: Out
    scan = Scan()
    work = Work() if scan.items else None
    report = Report(work=work)
'''
    )
    (plan,) = build_plans(module)
    assert plan.gate_skips == {"work": []}
