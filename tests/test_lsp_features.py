"""`aspec lsp` phase L1: hover, go-to-definition, document symbols.

Positions are located by searching the fixture text, so the tests survive
fixture reformatting.
"""

import pytest

from agentspec.lsp.features import (
    definition,
    document_symbols,
    enclosing_class,
    hover,
    word_chain_at,
)
from agentspec.parser import parse_source

FILE = "/tmp/minimal.aspec.py"
URI = "file://" + FILE


@pytest.fixture(scope="module")
def text(fixtures_dir):
    return (fixtures_dir / "minimal_good.aspec.py").read_text()


@pytest.fixture(scope="module")
def module(text):
    return parse_source(text, filename=FILE)


def pos_of(text, line_needle, word, occurrence=0):
    """(0-based line, char of `word`) for the first line containing the needle."""
    for i, row in enumerate(text.split("\n")):
        if line_needle in row:
            col = -1
            for _ in range(occurrence + 1):
                col = row.index(word, col + 1)
            return i, col
    raise AssertionError(f"needle not found: {line_needle!r}")


def hover_at(module, text, line, char):
    result = hover(module, FILE, text, line, char)
    return result["contents"]["value"] if result else None


# ---------------- word chains ----------------


def test_word_chain_extraction():
    assert word_chain_at("x = issue.number", 0, 5) == (["issue", "number"], 0)
    assert word_chain_at("x = issue.number", 0, 12) == (["issue", "number"], 1)
    assert word_chain_at("a.b.c", 0, 4) == (["a", "b", "c"], 2)
    assert word_chain_at("   ", 0, 1) is None
    assert word_chain_at("x", 5, 0) is None  # line out of range


# ---------------- hover ----------------


def test_hover_task_name(module, text):
    line, char = pos_of(text, "triage = TriageMessage", "TriageMessage")
    md = hover_at(module, text, line, char)
    assert "**TriageMessage** — task" in md
    assert "Classify one inbox message" in md
    assert "returns `Triage`" in md


def test_hover_schema_name(module, text):
    line, char = pos_of(text, "class Triage(BaseModel)", "Triage")
    md = hover_at(module, text, line, char)
    assert "**Triage** — schema" in md
    assert "summary: str  [max_length=200]" in md


def test_hover_bind_var_and_result_field(module, text):
    # `triage.actionable` in the ticket gate: hover the var, then the field
    line, char = pos_of(text, "if triage.actionable", "triage", occurrence=0)
    md = hover_at(module, text, line, char)
    assert "**triage** = TriageMessage" in md

    line, char = pos_of(text, "if triage.actionable", "actionable")
    md = hover_at(module, text, line, char)
    assert "**Triage.actionable**: `bool`" in md
    assert "produced by **TriageMessage**" in md


def test_hover_field_with_bounds(module, text):
    line, char = pos_of(text, "summary=triage.summary", "summary", occurrence=1)
    md = hover_at(module, text, line, char)
    assert "**Triage.summary**: `str`" in md
    assert "max_length=200" in md


def test_hover_task_input(module, text):
    line, char = pos_of(text, "triage = TriageMessage(message_text=message_text)", "message_text")
    md = hover_at(module, text, line, char)
    assert "task input" in md and "`str`" in md


def test_hover_rule_constant(module, text):
    line, char = pos_of(text, "constraints = HOUSE_RULES +", "HOUSE_RULES")
    md = hover_at(module, text, line, char)
    assert "**HOUSE_RULES** — rule list (1 rule(s))" in md


def test_hover_rule_first_line(module, text):
    line, char = pos_of(text, '"mentions-are-not-intent",', "mentions-are-not-intent")
    md = hover_at(module, text, line, char + 3)
    assert "**mentions-are-not-intent** — must-rule" in md
    assert "*why:* name-dropping" in md


def test_hover_rule_id_string(module, text):
    line, char = pos_of(text, '"inbox-read-only",', "inbox-read-only")
    md = hover_at(module, text, line, char + 3)
    assert "**inbox-read-only** — must-rule" in md
    assert "Never modify anything outside the inbox directory" in md


def test_hover_nothing(module, text):
    assert hover_at(module, text, 0, 0) is None  # module docstring


# ---------------- definition ----------------


def def_line(module, text, line, char):
    loc = definition(module, URI, FILE, text, line, char)
    assert loc is not None and loc["uri"] == URI
    return loc["range"]["start"]["line"]


def test_definition_task_class(module, text):
    line, char = pos_of(text, "triage = TriageMessage", "TriageMessage")
    target = def_line(module, text, line, char)
    expected, _ = pos_of(text, "class TriageMessage(Task)", "class")
    assert target == expected


def test_definition_bind_var(module, text):
    line, char = pos_of(text, "if triage.actionable", "triage")
    target = def_line(module, text, line, char)
    expected, _ = pos_of(text, "triage = TriageMessage", "triage")
    assert target == expected


def test_definition_result_field_jumps_to_schema(module, text):
    line, char = pos_of(text, "if triage.actionable", "actionable")
    target = def_line(module, text, line, char)
    expected, _ = pos_of(text, "actionable: bool", "actionable")
    assert target == expected


# ---------------- document symbols ----------------


def test_document_symbols_outline(module, text):
    symbols = document_symbols(module, FILE, text)
    by_name = {s["name"]: s for s in symbols}
    assert by_name["HOUSE_RULES"]["detail"] == "1 rule(s)"
    assert by_name["Triage"]["detail"] == "schema"
    assert [c["name"] for c in by_name["Triage"]["children"]] == [
        "actionable: bool",
        "category: Enum['bug', 'question', 'noise']",
        "summary: str",
    ]
    assert by_name["InboxRoutine"]["detail"] == "orchestrator"
    assert [c["name"] for c in by_name["InboxRoutine"]["children"]] == [
        "triage = TriageMessage",
        "ticket = FileTicket",
    ]
    # ranges nest: a task's range covers its binds
    routine = by_name["InboxRoutine"]
    assert routine["range"]["start"]["line"] < routine["children"][0]["range"]["start"]["line"]
    assert routine["range"]["end"]["line"] >= routine["children"][-1]["range"]["start"]["line"]


def test_enclosing_class(module, text):
    line, _ = pos_of(text, "if triage.actionable", "triage")
    encl = enclosing_class(module, FILE, line)
    assert encl is not None and encl.name == "InboxRoutine"
    assert enclosing_class(module, FILE, 0) is None


# ---------------- teaching hovers ----------------


def test_hover_tool_name_teaches_substitution(module, text):
    line, char = pos_of(text, 'Tool("gh", ops=["issue create"])', "gh")
    md = hover_at(module, text, line, char + 1)
    assert "**gh** — tool capability" in md
    assert "ops: issue create" in md
    assert "MAY substitute" in md


def test_hover_strict_tool(text):
    src = text.replace(
        'Tool("gh", ops=["issue create"])', 'Tool("gh", ops=["issue create"], strict=True)'
    )
    from agentspec.parser import parse_source as ps

    module_v = ps(src, filename=FILE)
    lines = src.split("\n")
    row = next(i for i, r in enumerate(lines) if "strict=True" in r)
    char = lines[row].index('"gh"') + 2
    result = hover(module_v, FILE, src, row, char)
    md = result["contents"]["value"]
    assert "this exact mechanism or nothing" in md


def test_hover_reserved_attrs(module, text):
    line, char = pos_of(text, "    undo = ", "undo")
    md = hover_at(module, text, line, char + 2)
    assert "**undo**" in md and "saga unwind" in md and "NOT unwound" in md

    line, char = pos_of(text, '    on_uncertain = {"actionable"', "on_uncertain")
    md = hover_at(module, text, line, char + 2)
    assert "never a failure" in md


def test_hover_optional_input_teaches_skip_propagation(fixtures_dir):
    path = str(fixtures_dir / "bookbank_routine.aspec.py")
    bb_text = (fixtures_dir / "bookbank_routine.aspec.py").read_text()
    bb_module = parse_source(bb_text, filename=path)
    lines = bb_text.split("\n")
    row = next(i for i, r in enumerate(lines) if "workspace: Workspace | None" in r)
    char = lines[row].index("workspace") + 3
    result = hover(bb_module, path, bb_text, row, char)
    md = result["contents"]["value"]
    assert "Workspace | None" in md
    assert "skips do NOT propagate" in md
