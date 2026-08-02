"""`aspec lsp` phase L2: completions and code actions.

Completions run against possibly-unparseable text plus the last good
SpecModel — exactly how the server calls them mid-keystroke.
"""

import pytest

from agentspec.lsp.features import code_actions, completion, hover
from agentspec.lsp.server import compute_diagnostics
from agentspec.parser import parse_source

FILE = "/tmp/minimal.aspec.py"
URI = "file://" + FILE


@pytest.fixture(scope="module")
def text(fixtures_dir):
    return (fixtures_dir / "minimal_good.aspec.py").read_text()


@pytest.fixture(scope="module")
def module(text):
    return parse_source(text, filename=FILE)


def complete_at(module, text, needle, suffix):
    """Completions with the cursor right after `needle + suffix` on the
    needle's line — the text is edited in place, mid-keystroke style."""
    lines = text.split("\n")
    row = next(i for i, r in enumerate(lines) if needle in r)
    col = lines[row].index(needle) + len(needle)
    lines[row] = lines[row][:col] + suffix + lines[row][col + 1000 :]
    return completion(module, FILE, "\n".join(lines), row, col + len(suffix))


# ---------------- completions ----------------


def test_complete_bind_result_fields(module, text):
    items = complete_at(module, text, "if triage", ".")
    labels = {i["label"] for i in items}
    assert labels == {"actionable", "category", "summary"}
    category = next(i for i in items if i["label"] == "category")
    assert category["detail"].startswith("Enum[")
    assert "produced by TriageMessage" in category["documentation"]


def test_complete_partial_word_after_dot(module, text):
    items = complete_at(module, text, "if triage", ".act")
    assert {i["label"] for i in items} == {"actionable", "category", "summary"}


def test_complete_env_namespace(module, text):
    items = complete_at(module, text, "summary=", "env.")
    assert {i["label"] for i in items} == {"now", "cwd", "user", "platform", "run_id"}


def test_complete_enum_value_in_literal_dict(module, text):
    # on_uncertain = {..., "category": "|
    items = complete_at(module, text, '"category": ', '"')
    assert {i["label"] for i in items} == {"bug", "question", "noise"}
    assert all(i["kind"] == 20 for i in items)


def test_complete_enum_value_in_comparison(module, text):
    extended = text + "    extra = FileTicket(category=triage.category,\n"
    lines = extended.split("\n")
    row = len(lines) - 2
    lines[row] = '    extra = FileTicket(category=triage.category) if triage.category == "'
    edited = "\n".join(lines)
    items = completion(module, FILE, edited, row, len(lines[row]))
    assert {i["label"] for i in items} == {"bug", "question", "noise"}


def test_complete_unknown_root_is_empty(module, text):
    assert complete_at(module, text, "if triage", ".") != []
    assert complete_at(module, text, "summary=", "nonsense.") == []


# ---------------- rule severity ----------------


def test_complete_severity_single_line(module, text):
    lines = text.split("\n")
    row = next(i for i, r in enumerate(lines) if "constraints = HOUSE_RULES" in r)
    lines[row] = (
        '    constraints = HOUSE_RULES + [Rule("never-file-twice", "Never file twice", severity="'
    )
    items = completion(module, FILE, "\n".join(lines), row, len(lines[row]))
    assert {i["label"] for i in items} == {"must", "should", "may"}
    must = next(i for i in items if i["label"] == "must")
    assert "Inviolable" in must["documentation"]


def test_complete_severity_on_continuation_line(module, text):
    # rules span lines; severity= is typed on its own continuation line
    lines = text.split("\n")
    row = next(i for i, r in enumerate(lines) if "writes belong to other routines" in r)
    lines[row] = '         severity="'
    items = completion(module, FILE, "\n".join(lines), row, len(lines[row]))
    assert {i["label"] for i in items} == {"must", "should", "may"}


def test_no_severity_outside_kwarg(module, text):
    # a plain string position must NOT offer severities
    lines = text.split("\n")
    row = next(i for i, r in enumerate(lines) if 'Tool("gh", ops=["issue create"])' in r)
    lines[row] = '    tools = [Tool("gh", ops=["issue create", "issue edit", "'
    items = completion(module, FILE, "\n".join(lines), row, len(lines[row]))
    assert items == []


def test_no_severity_in_why_kwarg(module, text):
    lines = text.split("\n")
    row = next(i for i, r in enumerate(lines) if "constraints = HOUSE_RULES" in r)
    lines[row] = (
        '    constraints = HOUSE_RULES + [Rule("never-file-twice", "Never file twice", why="'
    )
    items = completion(module, FILE, "\n".join(lines), row, len(lines[row]))
    assert items == []


def test_hover_severity_string(module, text):
    src = text.replace(
        'why="the routine is a reader; writes belong to other routines",',
        'why="the routine is a reader; writes belong to other routines",\n'
        '        severity="should",',
    )
    lines = src.split("\n")
    row = next(i for i, r in enumerate(lines) if '"should"' in r)
    char = lines[row].index('"should"') + 3
    module_v = parse_source(src, filename=FILE)
    result = hover(module_v, FILE, src, row, char)
    md = result["contents"]["value"]
    assert "**should** — rule severity" in md
    assert "the must wins" in md


# ---------------- code actions ----------------


def test_add_why_quickfix(text):
    src = text.replace(
        "    constraints = HOUSE_RULES\n    undo",
        '    constraints = HOUSE_RULES + [Rule("never-file-twice", "Never file twice")]\n    undo',
    )
    diags = compute_diagnostics(URI, src)
    as030 = [d for d in diags if d["code"] == "AS030"]
    assert len(as030) == 1

    module = parse_source(src, filename=FILE)
    actions = code_actions(module, URI, src, as030)
    assert len(actions) == 1
    action = actions[0]
    assert action["kind"] == "quickfix"
    assert action["title"] == "Add a why to this must-rule"
    edit = action["edit"]["changes"][URI][0]
    assert edit["newText"] == ', why="TODO: state the why"'
    row = src.split("\n")[edit["range"]["start"]["line"]]
    insert_at = edit["range"]["start"]["character"]
    assert edit["range"]["start"] == edit["range"]["end"]  # pure insertion
    assert row[insert_at] == ")"  # lands before the Rule call's closing paren
    assert row[:insert_at].endswith('"Never file twice"')


def test_server_completes_on_unparseable_text(text):
    """Mid-keystroke: `triage.` breaks the parse, but the server answers
    from the last good module."""
    from test_lsp import run_session

    lines = text.split("\n")
    row = next(i for i, r in enumerate(lines) if "if triage.actionable" in r)
    broken = lines[:]
    broken[row] = "                        summary=triage.summary) if triage."
    broken_text = "\n".join(broken)
    code, out = run_session(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": URI,
                        "languageId": "python",
                        "version": 1,
                        "text": text,
                    }
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didChange",
                "params": {
                    "textDocument": {"uri": URI, "version": 2},
                    "contentChanges": [{"text": broken_text}],
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "textDocument/completion",
                "params": {
                    "textDocument": {"uri": URI},
                    "position": {"line": row, "character": len(broken[row])},
                },
            },
            {"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}},
            {"jsonrpc": "2.0", "method": "exit"},
        ]
    )
    assert code == 0
    published = [m for m in out if m.get("method") == "textDocument/publishDiagnostics"]
    assert published[1]["params"]["diagnostics"], "broken text must still diagnose"
    items = next(m for m in out if m.get("id") == 2)["result"]
    assert {i["label"] for i in items} == {"actionable", "category", "summary"}


def test_no_action_for_other_diagnostics(module, text):
    fake = {
        "code": "AS031",
        "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
    }
    assert code_actions(module, URI, text, [fake]) == []
