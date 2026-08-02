"""`aspec diff`: semantic comparison — direction classification and CLI."""

import json

import pytest

from agentspec.cli import main
from agentspec.diff import diff_modules, render_text
from agentspec.parser import parse_file, parse_source


@pytest.fixture(scope="module")
def minimal_src(fixtures_dir):
    return (fixtures_dir / "minimal_good.aspec.py").read_text()


@pytest.fixture(scope="module")
def minimal(minimal_src):
    return parse_source(minimal_src, "minimal_good.aspec.py")


def variant(src: str, *replacements: tuple[str, str]):
    for old, new in replacements:
        assert old in src, f"variant replacement not found: {old!r}"
        src = src.replace(old, new)
    return parse_source(src, "variant.aspec.py")


def changes_between(minimal, minimal_src, *replacements):
    return diff_modules(minimal, variant(minimal_src, *replacements))


def only(changes, code):
    matched = [c for c in changes if c.code == code]
    assert len(matched) == 1, f"expected exactly one {code}, got {[c.code for c in changes]}"
    return matched[0]


# ---------------- identity ----------------


def test_self_diff_is_empty(minimal):
    assert diff_modules(minimal, minimal) == []


def test_bookbank_self_diff_is_empty(fixtures_dir):
    bookbank = parse_file(fixtures_dir / "bookbank_routine.aspec.py")
    assert diff_modules(bookbank, bookbank) == []


# ---------------- widens ----------------


def test_tool_surface_widened(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        ('Tool("gh", ops=["issue create"])', 'Tool("gh", ops=["issue create", "issue edit"])'),
    )
    change = only(changes, "tool-surface-widened")
    assert change.direction == "widens"
    assert change.owner == "FileTicket"
    assert "issue edit" in change.detail


def test_tool_added_widens(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        (
            'tools = [Tool("gh", ops=["issue create"])]',
            'tools = [Tool("gh", ops=["issue create"]), Tool("bash", ops=["curl"])]',
        ),
    )
    change = only(changes, "tool-added")
    assert change.direction == "widens"


def test_must_rule_removed_widens(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        (
            "        Rule(\n"
            '            "mentions-are-not-intent",\n'
            '            "A message that merely mentions a product name is not automatically "\n'
            '            "actionable",\n'
            '            why="name-dropping is the most common false positive",\n'
            "        ),\n",
            "",
        ),
    )
    change = only(changes, "rule-removed")
    assert change.direction == "widens"
    assert change.owner == "TriageMessage"
    assert "must-rule 'mentions-are-not-intent'" in change.detail


def test_rule_weakened(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        (
            'why="name-dropping is the most common false positive",',
            'why="name-dropping is the most common false positive",\n'
            '            severity="should",',
        ),
    )
    change = only(changes, "rule-weakened")
    assert change.direction == "widens"
    assert change.old == "must" and change.new == "should"


def test_gate_removed_widens(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        (
            "    ticket = (\n"
            "        FileTicket(category=triage.category, summary=triage.summary)\n"
            "        if triage.actionable\n"
            "        else None\n"
            "    )",
            "    ticket = FileTicket(category=triage.category, summary=triage.summary)",
        ),
    )
    change = only(changes, "gate-removed")
    assert change.direction == "widens"
    assert "triage.actionable" in change.detail


def test_undo_removed_widens(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        ("    undo = \"Close the created ticket with a 'filed in error' comment\"\n", ""),
    )
    change = only(changes, "undo-removed")
    assert change.direction == "widens"


def test_enum_widened_and_bound_loosened(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        ('Enum["bug", "question", "noise"]', 'Enum["bug", "question", "noise", "spam"]'),
        ("summary: str = Field(max_length=200)", "summary: str = Field(max_length=400)"),
    )
    assert only(changes, "enum-widened").direction == "widens"
    loosened = only(changes, "bound-loosened")
    assert loosened.direction == "widens"
    assert loosened.old == 200 and loosened.new == 400


def test_abort_weakened_to_default_widens(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        (
            "    undo = \"Close the created ticket with a 'filed in error' comment\"\n"
            '    on_failure = "abort"',
            "    undo = \"Close the created ticket with a 'filed in error' comment\"\n"
            '    on_failure = {"filed": False, "tracker_id": ""}',
        ),
    )
    change = only(changes, "on-failure-kind-changed")
    assert change.direction == "widens"
    assert change.old == "abort"


# ---------------- narrows ----------------


def test_rule_added_narrows(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        (
            "    constraints = HOUSE_RULES\n    undo",
            "    constraints = HOUSE_RULES + "
            '[Rule("never-file-twice", "Never file twice", why="idempotency")]\n    undo',
        ),
    )
    change = only(changes, "rule-added")
    assert change.direction == "narrows"


def test_bound_tightened_narrows(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        ("summary: str = Field(max_length=200)", "summary: str = Field(max_length=100)"),
    )
    assert only(changes, "bound-tightened").direction == "narrows"


def test_strict_added_narrows(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        ('Tool("gh", ops=["issue create"])', 'Tool("gh", ops=["issue create"], strict=True)'),
    )
    assert only(changes, "strict-added").direction == "narrows"


# ---------------- reshapes / neutral ----------------


def test_docstring_change_reshapes(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        (
            '"""Classify one inbox message and summarize it in a sentence."""',
            '"""Classify one inbox message and rate its urgency."""',
        ),
    )
    change = only(changes, "task-docstring-changed")
    assert change.direction == "reshapes"


def test_step_rewired_reshapes(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        (
            "FileTicket(category=triage.category,",
            "FileTicket(category=triage.summary,",
        ),
    )
    change = only(changes, "step-rewired")
    assert change.direction == "reshapes"
    assert "triage.category -> triage.summary" in change.detail


def test_rule_text_changed_keeps_identity(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        (
            '"A message that merely mentions a product name is not automatically "',
            '"A message that merely mentions a competitor is not automatically "',
        ),
    )
    change = only(changes, "rule-text-changed")
    assert change.direction == "reshapes"
    assert "'mentions-are-not-intent'" in change.detail
    assert not [c for c in changes if c.code in ("rule-added", "rule-removed")]


def test_rule_since_change_is_neutral(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        (
            'why="name-dropping is the most common false positive",',
            'why="name-dropping is the most common false positive",\n            since="v1.1.0",',
        ),
    )
    change = only(changes, "rule-since-changed")
    assert change.direction == "neutral"


def test_rule_why_change_is_neutral(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        (
            'why="name-dropping is the most common false positive"',
            'why="mentions alone carry no intent"',
        ),
    )
    change = only(changes, "rule-why-changed")
    assert change.direction == "neutral"


def test_task_added_reshapes_and_its_tools_widen(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        (
            "class InboxRoutine(Task):",
            "class Archive(Task):\n"
            '    """Archive a message."""\n'
            "    message_text: str\n"
            "    returns: FileReport\n"
            '    tools = [Tool("bash", ops=["mv"])]\n'
            '    on_failure = "abort"\n\n'
            "class InboxRoutine(Task):",
        ),
    )
    assert only(changes, "task-added").direction == "reshapes"
    tool = only(changes, "tool-added")
    assert tool.direction == "widens" and "Archive" in tool.detail


# ---------------- rendering + CLI ----------------


def test_render_orders_widening_first(minimal, minimal_src):
    changes = changes_between(
        minimal,
        minimal_src,
        (
            'Tool("gh", ops=["issue create"])',
            'Tool("gh", ops=["issue create", "issue edit"], strict=True)',
        ),
    )
    text = render_text(changes, "a.aspec.py", "b.aspec.py")
    assert text.index("[widens]") < text.index("[narrows]")
    assert "widening change(s)" in text


def test_cli_exit_codes_and_json(fixtures_dir, tmp_path, capsys, minimal_src):
    fixture = str(fixtures_dir / "minimal_good.aspec.py")
    assert main(["diff", fixture, fixture]) == 0
    capsys.readouterr()

    changed = tmp_path / "changed.aspec.py"
    changed.write_text(
        minimal_src.replace(
            'Tool("gh", ops=["issue create"])', 'Tool("gh", ops=["issue create", "pr create"])'
        )
    )
    assert main(["diff", fixture, str(changed)]) == 1
    capsys.readouterr()

    assert main(["diff", "--json", fixture, str(changed)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["code"] == "tool-surface-widened"
    assert payload[0]["direction"] == "widens"

    broken = tmp_path / "broken.aspec.py"
    broken.write_text("import os\n")
    assert main(["diff", fixture, str(broken)]) == 2
