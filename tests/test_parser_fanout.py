"""Parser tests for fan-out, filters, and list types (fanout_routine fixture)."""

import pytest

from agentspec.parser import parse_file


@pytest.fixture(scope="module")
def module(fixtures_dir):
    return parse_file(fixtures_dir / "fanout_routine.aspec.py")


def test_parses_clean(module):
    assert module.errors == []
    assert module.root_task == "ScanRoutine"


def test_list_typed_field(module):
    findings = module.schemas["ScanResult"].field("findings")
    assert findings.type.kind == "list"
    assert findings.type.item.name == "Finding"


def test_fanout_over_step_result(module):
    plans = module.tasks["ScanRoutine"].bind("plans")
    assert plans.task == "TriageFinding"
    assert plans.fanout.loop_var == "f"
    assert plans.fanout.source.path.dotted == "scan.findings"
    assert plans.fanout.filter is None
    # item fields are addressed through the loop variable
    assert plans.kwargs["finding"].path.parts == ["f"]


def test_filtered_fanout_over_prior_fanout(module):
    pages = module.tasks["ScanRoutine"].bind("pages")
    assert pages.fanout.source.path.parts == ["plans"]
    filt = pages.fanout.filter
    assert filt.valid
    assert filt.op == "in"
    assert filt.path.dotted == "p.action"
    assert filt.values == ["page"]


def test_on_item_failure(module):
    assert module.tasks["TriageFinding"].on_item_failure == "skip_and_report"
