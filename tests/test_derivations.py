"""Derivation binds (spec 2.2): parsing, lint, plan, graph, orchestrate."""

import json

import pytest

from agentspec.graph import render_mermaid
from agentspec.lint import lint_module
from agentspec.parser import parse_source
from agentspec.plan import build_plan
from agentspec.run import orchestrate

SPEC = '''"""Route work by its verdict."""
from pydantic import BaseModel, Field
from agentspec import Task

class Verdict(BaseModel):
    ok: bool
    count: int = Field(ge=0)

class Note(BaseModel):
    noted: bool

class Check(Task):
    """Check the thing."""
    returns: Verdict
    on_failure = {"ok": False, "count": 0}

class Record(Task):
    """Record the routing outcome."""
    outcome: str
    returns: Note
    on_failure = "abort"

class Routine(Task):
    """Check, route, record."""
    returns: Note

    check = Check()
    route = {
        not check.ok: {"outcome": "failed", "count": 0},
        check.count > 3: {"outcome": "busy", "count": check.count},
        True: {"outcome": "fine", "count": check.count},
    }
    record = Record(outcome=route.outcome)

    on_failure = "abort"
'''


@pytest.fixture(scope="module")
def module():
    return parse_source(SPEC, "routed.aspec.py")


def test_parse_and_lint_clean(module):
    assert module.ok
    [route] = module.tasks["Routine"].derivations
    assert [row.raw for row in route.rows] == ["not check.ok", "check.count > 3", "True"]
    assert route.rows[1].atoms[0].op == ">" and route.rows[1].atoms[0].value == 3
    assert lint_module(module) == []


def test_plan_places_derivation(module):
    plan = build_plan(module, module.tasks["Routine"])
    route = plan.step("route")
    assert route.task == "<derived>"
    assert plan.step("record").depends_on == ["route"]
    assert "route" not in {v for skips in plan.gate_skips.values() for v in skips}


def test_graph_hexagon(module):
    out = render_mermaid(module, module.tasks["Routine"])
    assert '    route{{"route = derived"}}' in out
    assert "    check --> route" in out
    assert "    route --> record" in out


def _diag_codes(src):
    module = parse_source(src, "routed.aspec.py")
    return {d.code for d in module.diagnostics} | {d.code for d in lint_module(module)}


def test_p015_or_condition():
    src = SPEC.replace("not check.ok:", "not check.ok or check.count > 9:")
    assert "P015" in _diag_codes(src)


def test_p015_non_dict_row():
    src = SPEC.replace('{"outcome": "failed", "count": 0}', '"failed"')
    assert "P015" in _diag_codes(src)


def test_as043_missing_catch_all():
    src = SPEC.replace('        True: {"outcome": "fine", "count": check.count},\n', "")
    assert "AS043" in _diag_codes(src)


def test_as043_catch_all_not_last():
    src = SPEC.replace(
        '        check.count > 3: {"outcome": "busy", "count": check.count},\n'
        '        True: {"outcome": "fine", "count": check.count},\n',
        '        True: {"outcome": "fine", "count": check.count},\n'
        '        check.count > 3: {"outcome": "busy", "count": check.count},\n',
    )
    assert "AS043" in _diag_codes(src)


def test_as044_inconsistent_fields():
    src = SPEC.replace('{"outcome": "failed", "count": 0}', '{"outcome": "failed"}')
    assert "AS044" in _diag_codes(src)


def test_as008_unknown_root():
    src = SPEC.replace("not check.ok:", "not nothing.ok:")
    assert "AS008" in _diag_codes(src)


def test_as008_bind_referencing_later_derivation():
    src = SPEC.replace(
        "    route = {\n"
        '        not check.ok: {"outcome": "failed", "count": 0},\n'
        '        check.count > 3: {"outcome": "busy", "count": check.count},\n'
        '        True: {"outcome": "fine", "count": check.count},\n'
        "    }\n"
        "    record = Record(outcome=route.outcome)\n",
        "    record = Record(outcome=route.outcome)\n"
        "    route = {\n"
        '        not check.ok: {"outcome": "failed", "count": 0},\n'
        '        check.count > 3: {"outcome": "busy", "count": check.count},\n'
        '        True: {"outcome": "fine", "count": check.count},\n'
        "    }\n",
    )
    assert src != SPEC
    assert "AS008" in _diag_codes(src)


def test_as008_derivation_referencing_later_bind():
    src = SPEC.replace("not check.ok:", "not record.noted:")
    assert "AS008" in _diag_codes(src)


def test_as008_derivation_referencing_itself():
    src = SPEC.replace('"count": 0', '"count": route.count')
    assert "AS008" in _diag_codes(src)


def test_as009_cycle_across_bind_derivation_boundary():
    src = SPEC.replace(
        '    route = {\n        not check.ok: {"outcome": "failed", "count": 0},\n',
        '    route = {\n        not record.noted: {"outcome": "failed", "count": 0},\n',
    ).replace(
        "    record = Record(outcome=route.outcome)\n",
        "",
    )
    src = src.replace(
        "    check = Check()\n",
        "    check = Check()\n    record = Record(outcome=route.outcome)\n",
    )
    codes = _diag_codes(src)
    assert "AS009" in codes  # record -> route -> record
    assert "AS008" in codes  # and the forward reference itself


def test_as011_gate_on_later_derivation():
    src = SPEC.replace(
        "    route = {",
        '    early = Record(outcome="x") if route.go else None\n    route = {',
    )
    assert "AS011" in _diag_codes(src)


def test_as020_row_literal_outside_consumer_enum():
    src = SPEC.replace("    outcome: str", '    outcome: Enum["failed", "busy", "fine"]').replace(
        "from agentspec import Task", "from agentspec import Task, Enum"
    )
    assert "AS020" not in _diag_codes(src)  # the literal join is a subset
    bad = src.replace('{"outcome": "failed", "count": 0}', '{"outcome": "exploded", "count": 0}')
    assert "AS020" in _diag_codes(bad)


def test_as020_derivation_field_into_wrong_kind():
    src = SPEC.replace("    outcome: str", "    outcome: str\n    count: int").replace(
        "Record(outcome=route.outcome)", "Record(outcome=route.outcome, count=route.outcome)"
    )
    assert "AS020" in _diag_codes(src)  # Enum['busy','failed','fine'] into int


def test_int_rows_flow_into_int_input():
    src = (
        SPEC.replace('"count": check.count', '"count": 1')
        .replace("    outcome: str", "    outcome: str\n    count: int")
        .replace(
            "Record(outcome=route.outcome)", "Record(outcome=route.outcome, count=route.count)"
        )
    )
    assert not {"AS020", "AS021", "AS022"} & _diag_codes(src)


def test_copied_row_value_stays_opaque():
    # route.count copies check.count in two rows — no literal join, no verdict
    src = SPEC.replace("    outcome: str", "    outcome: str\n    count: str").replace(
        "Record(outcome=route.outcome)", "Record(outcome=route.outcome, count=route.count)"
    )
    assert "AS020" not in _diag_codes(src)


def test_as022_field_access_on_joined_enum():
    src = SPEC.replace("route.outcome", "route.outcome.loud")
    assert "AS022" in _diag_codes(src)


def test_as041_as042_target_derivations():
    enum_spec = SPEC.replace(
        "class Note(BaseModel):\n    noted: bool",
        "class Note(BaseModel):\n    noted: bool\n\nclass Report(BaseModel):\n"
        '    outcome: Enum["failed", "busy", "fine", "stuck"]',
    ).replace("from agentspec import Task", "from agentspec import Task, Enum")
    enum_spec = enum_spec.replace(
        '    """Check, route, record."""\n    returns: Note',
        '    """Check, route, record."""\n    returns: Report',
    )
    codes = _diag_codes(enum_spec)
    assert "AS042" in codes  # 'stuck' unreachable
    bad = enum_spec.replace(
        '{"outcome": "failed", "count": 0}', '{"outcome": "exploded", "count": 0}'
    )
    assert "AS041" in _diag_codes(bad)


def test_orchestrate_evaluates_mechanically(tmp_path):
    spec = tmp_path / "routed.aspec.py"
    spec.write_text(SPEC)

    def adapter(prompt):
        if "# Task: Check" in prompt:
            return json.dumps({"ok": True, "count": 7})
        if "# Task: Record" in prompt:
            assert '"outcome": "busy"' in prompt  # derived value reached the consumer
            return json.dumps({"noted": True})
        if "# Task: Routine" in prompt:
            return json.dumps({"noted": True})
        raise AssertionError(prompt[:120])

    result = orchestrate(spec, adapter)
    assert result.status == "conforming"
    route = next(s for s in result.steps if s.var == "route")
    assert route.status == "ok" and route.task == "<derived>"


def test_orchestrate_derivation_survives_skips(tmp_path):
    """The producer fails into its declared fallback (ok=False) — the first
    row matches and the copied count is still mechanical."""
    spec = tmp_path / "routed.aspec.py"
    spec.write_text(SPEC)

    def adapter(prompt):
        if "# Task: Check" in prompt:
            return json.dumps({"ok": False, "count": 0})
        if "# Task: Record" in prompt:
            assert '"outcome": "failed"' in prompt
            return json.dumps({"noted": True})
        if "# Task: Routine" in prompt:
            return json.dumps({"noted": True})
        raise AssertionError(prompt[:120])

    assert orchestrate(spec, adapter).status == "conforming"


def test_diff_rows_by_condition():
    from agentspec.diff import diff_modules

    changed = SPEC.replace(
        '{"outcome": "busy", "count": check.count}', '{"outcome": "swamped", "count": check.count}'
    )
    dropped = SPEC.replace(
        '        check.count > 3: {"outcome": "busy", "count": check.count},\n', ""
    )
    base = parse_source(SPEC, "a.aspec.py")
    codes = [c.code for c in diff_modules(base, parse_source(changed, "b.aspec.py"))]
    assert codes == ["derivation-row-changed"]
    codes = [c.code for c in diff_modules(base, parse_source(dropped, "b.aspec.py"))]
    assert codes == ["derivation-row-removed"]


def test_lsp_hover_and_outline():
    from agentspec.lsp.features import document_symbols, hover

    module = parse_source(SPEC, "/tmp/routed.aspec.py")
    lines = SPEC.split("\n")
    row = next(i for i, r in enumerate(lines) if "record = Record(outcome=route.outcome)" in r)
    char = lines[row].index("route.outcome") + 2
    md = hover(module, "/tmp/routed.aspec.py", SPEC, row, char)["contents"]["value"]
    assert "**route** — derivation" in md
    assert "`check.count > 3` → outcome='busy'" in md

    symbols = document_symbols(module, "/tmp/routed.aspec.py", SPEC)
    routine = next(s for s in symbols if s["name"] == "Routine")
    assert any(c["name"] == "route = derived" for c in routine["children"])
