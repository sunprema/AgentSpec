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
        "PublishResult",
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
        "PublishBook",
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
    assert len(report.field("stopped_at").type.values) == 7
    assert report.field("summary").max_length == 500
    assert report.field("validator_errors").ge == 0


def test_tool_declarations(module):
    build = module.tasks["GenerateBook"]
    tools = {tool.name: tool for tool in build.tools}
    assert len(build.tools) == 7
    assert "gh" not in tools  # moved to PublishBook
    assert tools["bookbank-plugin"].strict
    assert tools["bookbank-plugin"].ops == ["create-book-from-issue"]
    assert tools["git"].exclusive
    assert tools["git"].risk_of("push") == "mutate"  # push no longer here
    assert len(tools["python3"].scripts) == 3
    assert tools["read"].paths[0] == "<plugin_root>/skills/**"


def test_publish_book_tool_declarations(module):
    publish = module.tasks["PublishBook"]
    tools = {tool.name: tool for tool in publish.tools}
    assert len(publish.tools) == 2
    assert tools["git"].exclusive
    assert tools["git"].risk_of("push") == "irreversible"
    assert tools["gh"].ops == ["pr create"]


def test_op_risk_tagging(module):
    select = module.tasks["SelectIssue"]
    gh = next(t for t in select.tools if t.name == "gh")
    assert all(gh.risk_of(op) == "read" for op in gh.ops)


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
    assert len(alert.inputs) == 8
    optional = [i for i in alert.inputs if i.type.optional]
    assert len(optional) == 6  # everything but the derived outcome input
    assert alert.inputs[0].type.name == "Workspace"
    outcome = next(i for i in alert.inputs if i.name == "outcome")
    assert not outcome.type.optional and outcome.type.name == "str"


def test_pipeline_and_gates(module):
    routine = module.tasks["BookbankRun"]
    assert [b.var for b in routine.pipeline] == [
        "workspace",
        "plugin",
        "issue",
        "mark",
        "build",
        "publish",
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
        "publish": "build.built",
        "art": "publish.published",
        "notify": "publish.published",
        "alert": None,
    }
    build = routine.bind("build")
    assert build.kwargs["stale_branch"].path.dotted == "issue.stale_branch"
    # the join: PushAlert receives whole prior results by bare name
    alert = routine.bind("alert")
    assert alert.kwargs["workspace"].path.parts == ["workspace"]
    assert len(alert.kwargs) == 8
    assert alert.kwargs["outcome"].path.dotted == "outcomes.outcome"
    assert alert.kwargs["alert_baseline"].path.dotted == "outcomes.alert"


def test_orchestrator_declarations(module):
    routine = module.tasks["BookbankRun"]
    assert routine.meta["version"] == "2.4.0"
    assert routine.on_failure.kind == "abort"
    assert set(routine.on_uncertain) == {
        "outcome",
        "stopped_at",
        "validator_errors",
        "summary",
        "operator_action",
    }
    assert len(routine.constraints) == 3 + 7  # UNATTENDED + its own
    [outcomes] = routine.derivations
    assert outcomes.var == "outcomes" and outcomes.style == "outcomes"
    assert len(outcomes.rows) == 9 and outcomes.has_catch_all
    assert all({"outcome", "alert"} <= set(row.output) for row in outcomes.rows)
