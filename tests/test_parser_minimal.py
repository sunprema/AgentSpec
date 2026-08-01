"""Parser integration tests over the minimal_good fixture."""

import pytest

from agentspec.parser import parse_file


@pytest.fixture(scope="module")
def module(fixtures_dir):
    return parse_file(fixtures_dir / "minimal_good.aspec.py")


def test_parses_clean(module):
    assert module.errors == []
    assert module.docstring.startswith("Minimal conformant routine")
    assert set(module.schemas) == {"Triage", "FileReport"}
    assert set(module.tasks) == {"TriageMessage", "FileTicket", "InboxRoutine"}
    assert set(module.constants) == {"HOUSE_RULES"}


def test_schema_fields(module):
    triage = module.schemas["Triage"]
    assert [f.name for f in triage.fields] == ["actionable", "category", "summary"]
    assert triage.field("actionable").type.name == "bool"
    category = triage.field("category")
    assert category.type.kind == "enum"
    assert category.type.values == ["bug", "question", "noise"]
    summary = triage.field("summary")
    assert summary.type.name == "str"
    assert summary.max_length == 200


def test_worker_task(module):
    task = module.tasks["TriageMessage"]
    assert task.docstring.startswith("Classify one inbox message")
    assert [(i.name, i.type.name) for i in task.inputs] == [("message_text", "str")]
    assert task.returns.name == "Triage"
    assert not task.is_orchestrator

    (tool,) = task.tools
    assert tool.name == "read"
    assert tool.paths == ["<inbox>/**"]

    assert len(task.constraints) == 2
    shared, own = task.constraints
    assert shared.source == "HOUSE_RULES"
    assert shared.severity == "must"
    assert own.why == "name-dropping is the most common false positive"

    assert task.on_uncertain == {
        "actionable": False,
        "category": "noise",
        "summary": "unclear",
    }
    assert task.on_failure.kind == "literal"
    assert task.on_failure.literal["category"] == "noise"


def test_mutating_task_declares_undo(module):
    task = module.tasks["FileTicket"]
    assert task.undo.startswith("Close the created ticket")
    assert task.on_failure.kind == "abort"


def test_pipeline(module):
    routine = module.tasks["InboxRoutine"]
    assert routine.is_orchestrator
    assert [b.var for b in routine.pipeline] == ["triage", "ticket"]

    triage = routine.bind("triage")
    assert triage.task == "TriageMessage"
    assert triage.gate is None
    assert triage.kwargs["message_text"].path.parts == ["message_text"]

    ticket = routine.bind("ticket")
    assert ticket.task == "FileTicket"
    assert ticket.gate.path.parts == ["triage", "actionable"]
    assert ticket.kwargs["category"].path.dotted == "triage.category"
    assert ticket.kwargs["summary"].path.dotted == "triage.summary"

    assert routine.meta == {"version": "1.0.0"}


def test_root_task(module):
    assert module.root_task == "InboxRoutine"
