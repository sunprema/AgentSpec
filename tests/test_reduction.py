"""Reduction-table lift: prose mappings become data; AS041/AS042 checks."""

import pytest

from agentspec.lint import lint_module
from agentspec.parser import parse_file, parse_source
from agentspec.reduction import extract_tables

SPEC = '''"""Routine with a declared reduction mapping."""
from pydantic import BaseModel, Field
from agentspec import Task, Rule, Enum

class Work(BaseModel):
    ok: bool

class Report(BaseModel):
    outcome: Enum["failed", "done", "uncertain"]
    stage: Enum["work", "complete"]

class DoWork(Task):
    """Work."""
    returns: Work
    on_failure = "abort"

class Routine(Task):
    """Do the work and reduce."""
    returns: Report

    w = DoWork()

    constraints = [
        Rule("reduction-mapping",
             "Reduce to Report by this mapping, in order — first match wins: "
             "NOT w.ok -> ('failed', 'work'); "
             "otherwise -> ('done', 'complete')",
             why="a table, not a narration"),
    ]
    on_uncertain = {"outcome": "uncertain", "stage": "work"}
    on_failure = "abort"
'''


def test_bookbank_uses_the_construct_not_prose(fixtures_dir):
    # since spec 2.2 the routing lives in the `route` derivation; the prose
    # lift finds nothing to recover
    module = parse_file(fixtures_dir / "bookbank_routine.aspec.py")
    assert extract_tables(module) == []
    [route] = module.tasks["BookbankRun"].derivations
    assert len(route.rows) == 8
    assert route.rows[4].raw == "not mark.marked"
    assert route.rows[4].output["stopped_at"].value == "mark_started"


def test_small_mapping_lints_clean():
    module = parse_source(SPEC, "routine.aspec.py")
    [table] = extract_tables(module)
    assert [r.outcome for r in table.rows] == ["failed", "done"]
    assert table.outcome_field == "outcome" and table.stage_field == "stage"
    assert [d.code for d in lint_module(module)] == []


def test_as042_unreachable_enum_value():
    # without on_uncertain, 'uncertain' has no producer at all
    src = SPEC.replace('    on_uncertain = {"outcome": "uncertain", "stage": "work"}\n', "")
    diags = [d for d in lint_module(parse_source(src, "routine.aspec.py")) if d.code == "AS042"]
    assert len(diags) == 1
    assert "'uncertain'" in diags[0].message


def test_as041_row_value_outside_enum():
    src = SPEC.replace("('failed', 'work')", "('exploded', 'work')")
    diags = [d for d in lint_module(parse_source(src, "routine.aspec.py")) if d.code == "AS041"]
    assert len(diags) == 1
    assert "'exploded'" in diags[0].message


def test_non_mapping_rules_are_ignored():
    src = SPEC.replace("NOT w.ok -> ('failed', 'work'); ", "").replace(
        "otherwise -> ('done', 'complete')", "handle every case conservatively"
    )
    module = parse_source(src, "routine.aspec.py")
    assert extract_tables(module) == []


@pytest.mark.parametrize("axis", ["outcome", "stage"])
def test_axis_fields_detected_independently(axis):
    module = parse_source(SPEC, "routine.aspec.py")
    [table] = extract_tables(module)
    assert getattr(table, f"{axis}_field" if axis == "outcome" else "stage_field")
