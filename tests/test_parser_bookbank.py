"""The canonical fixture round-trips: every construct in the BookBank routine
must be accounted for in the SpecModel."""

import pytest

from agentspec.parser import parse_file


@pytest.fixture(scope="module")
def module(fixtures_dir):
    return parse_file(fixtures_dir / "bookbank_routine.aspec.py")


def test_parses_clean(module):
    assert module.errors == []
    assert module.docstring.startswith("BookBank headless book generation")


def test_inventory(module):
    assert set(module.schemas) == {
        "Workspace",
        "PluginStatus",
        "IssueRef",
        "StartMark",
        "BookBuild",
        "ImageRequests",
        "IssueNote",
        "Alert",
        "RunReport",
    }
    assert set(module.tasks) == {
        "ResolveWorkspace",
        "VerifyPlugin",
        "SelectIssue",
        "MarkStarted",
        "GenerateBook",
        "OpenImageRequests",
        "NotifyIssue",
        "PushAlert",
        "BookbankRun",
    }
    assert module.root_task == "BookbankRun"


def test_shared_rules(module):
    unattended = module.constants["UNATTENDED"].rules
    assert len(unattended) == 3
    assert all(rule.severity == "must" for rule in unattended)
    assert all(rule.why for rule in unattended)
    # Composition: every task's constraints start from the shared doctrine.
    for task in module.tasks.values():
        if task.constraints:
            assert [r.source for r in task.constraints[:3]] == ["UNATTENDED"] * 3


def test_run_report_contract(module):
    report = module.schemas["RunReport"]
    outcome = report.field("outcome")
    assert outcome.type.kind == "enum"
    assert len(outcome.type.values) == 8
    assert "published_with_errors" in outcome.type.values
    assert len(report.field("stopped_at").type.values) == 6
    assert report.field("summary").max_length == 500
    assert report.field("validator_errors").ge == 0


def test_tool_declarations(module):
    build = module.tasks["GenerateBook"]
    tools = {tool.name: tool for tool in build.tools}
    assert len(build.tools) == 8
    assert tools["bookbank-plugin"].strict
    assert tools["bookbank-plugin"].ops == ["create-book-from-issue"]
    assert tools["git"].exclusive
    assert len(tools["python3"].scripts) == 3
    assert tools["read"].paths[0] == "<plugin_root>/skills/**"


def test_failure_declarations(module):
    retry = module.tasks["NotifyIssue"].on_failure
    assert retry.kind == "retry"
    assert retry.max == 3
    assert retry.backoff_s == 30
    assert retry.then.kind == "literal"
    assert retry.then.literal == {"commented": False}

    assert module.tasks["MarkStarted"].on_failure.kind == "abort"
    assert module.tasks["MarkStarted"].undo.startswith("Remove the in-progress label")
    assert module.tasks["OpenImageRequests"].on_failure.kind == "literal"


def test_optional_inputs(module):
    alert = module.tasks["PushAlert"]
    assert len(alert.inputs) == 6
    assert all(i.type.optional for i in alert.inputs)
    assert alert.inputs[0].type.name == "Workspace"


def test_pipeline_and_gates(module):
    routine = module.tasks["BookbankRun"]
    assert [b.var for b in routine.pipeline] == [
        "workspace",
        "plugin",
        "issue",
        "mark",
        "build",
        "art",
        "notify",
        "alert",
    ]
    gates = {b.var: (b.gate.path.dotted if b.gate else None) for b in routine.pipeline}
    assert gates == {
        "workspace": None,
        "plugin": "workspace.resolved",
        "issue": "plugin.usable",
        "mark": "issue.proceed",
        "build": "mark.marked",
        "art": "build.built",
        "notify": "build.built",
        "alert": None,
    }
    build = routine.bind("build")
    assert build.kwargs["stale_branch"].path.dotted == "issue.stale_branch"
    # the join: PushAlert receives whole prior results by bare name
    alert = routine.bind("alert")
    assert alert.kwargs["workspace"].path.parts == ["workspace"]
    assert len(alert.kwargs) == 6


def test_orchestrator_declarations(module):
    routine = module.tasks["BookbankRun"]
    assert routine.meta["version"] == "2.0.4"
    assert routine.on_failure.kind == "abort"
    assert set(routine.on_uncertain) == {
        "outcome",
        "stopped_at",
        "validator_errors",
        "summary",
        "operator_action",
    }
    assert len(routine.constraints) == 3 + 7  # UNATTENDED + its own
