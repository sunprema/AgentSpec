"""`aspec studio`: payload derivation and the localhost server."""

import json
import shutil
import urllib.request

import pytest

from agentspec.cli import main
from agentspec.parser import parse_file, parse_source
from agentspec.studio import build_payload, create_server


@pytest.fixture(scope="module")
def bookbank_payload(fixtures_dir):
    return build_payload(parse_file(fixtures_dir / "bookbank_routine.aspec.py"))


# ---------------- payload ----------------


def test_payload_root_and_tasks(bookbank_payload):
    p = bookbank_payload
    assert p["root"] == "BookbankRun"
    assert set(p["tasks"]) >= {"ResolveWorkspace", "GenerateBook", "PushAlert", "BookbankRun"}
    assert p["tasks"]["BookbankRun"]["is_orchestrator"]

    generate = p["tasks"]["GenerateBook"]
    assert {t["name"] for t in generate["tools"]} >= {"bookbank-plugin", "git"}
    plugin_tool = next(t for t in generate["tools"] if t["name"] == "bookbank-plugin")
    assert plugin_tool["strict"]
    git_tool = next(t for t in generate["tools"] if t["name"] == "git")
    assert git_tool["exclusive"]
    assert any(r["severity"] == "must" for r in generate["rules"])
    assert generate["undo"]
    assert generate["on_failure"] == "abort"

    publish = p["tasks"]["PublishBook"]
    assert {t["name"] for t in publish["tools"]} == {"git", "gh"}


def test_payload_schema_fields_and_bounds(bookbank_payload):
    report = bookbank_payload["schemas"]["RunReport"]
    summary = next(f for f in report["fields"] if f["name"] == "summary")
    assert summary["bounds"] == "max_length=500"
    outcome = next(f for f in report["fields"] if f["name"] == "outcome")
    assert outcome["type"].startswith("Enum[")


def test_payload_pipeline_waves(bookbank_payload):
    pipe = bookbank_payload["pipeline"]
    assert pipe["orchestrator"] == "BookbankRun"
    assert len(pipe["waves"]) == 8
    assert sorted(pipe["waves"][6]) == ["art", "notify", "outcomes"]
    assert pipe["waves"][7] == ["alert"]
    outcomes = next(s for s in pipe["steps"] if s["var"] == "outcomes")
    assert outcomes["task"] == "<derived>" and outcomes["needs"] == []
    assert pipe["inputs"] == ["freeform_context"]


def test_payload_edges_carry_fields(bookbank_payload):
    edges = bookbank_payload["edges"]

    def edge(src, dst, kind):
        return next(e for e in edges if e["src"] == src and e["dst"] == dst and e["kind"] == kind)

    assert "issue.number → issue_number" in edge("issue", "build", "data")["labels"]
    assert edge("issue", "mark", "gate")["labels"] == ["issue.proceed"]
    assert any(e["src"] == "_inputs" and e["dst"] == "issue" for e in edges)


def test_payload_simulator_needs(bookbank_payload):
    steps = {s["var"]: s for s in bookbank_payload["pipeline"]["steps"]}
    # PushAlert's inputs are all `X | None`: skips must not propagate into it
    alert_needs = {n["root"]: n["optional"] for n in steps["alert"]["needs"]}
    assert alert_needs["build"] is True and alert_needs["workspace"] is True
    # GenerateBook consumes required fields plus its gate on mark
    build_needs = {(n["root"], n["input"]): n["optional"] for n in steps["build"]["needs"]}
    assert build_needs[("issue", "issue_number")] is False
    assert build_needs[("mark", "<gate>")] is False
    assert steps["build"]["gate"] == "mark.marked"
    assert steps["build"]["gate_root"] == "mark"


def test_payload_gate_skips_carried(bookbank_payload):
    skips = bookbank_payload["pipeline"]["gate_skips"]
    # a false mark gate transitively skips the build and its dependents,
    # but never the alert join (optional inputs)
    assert "build" in skips["mark"]
    assert "alert" not in skips["mark"]


def test_payload_lint_owner_mapping(fixtures_dir):
    src = (fixtures_dir / "minimal_good.aspec.py").read_text()
    # a bare-string rule lints as a must without a why, owned by FileTicket
    src = src.replace(
        "    constraints = HOUSE_RULES\n    undo",
        '    constraints = HOUSE_RULES + [Rule("never-file-twice", "Never file twice")]\n    undo',
    )
    payload = build_payload(parse_source(src, "minimal_good.aspec.py"))
    owned = [i for i in payload["lint"] if i["owner"] == "FileTicket"]
    assert owned, payload["lint"]


def test_payload_without_orchestrator(fixtures_dir):
    src = '''"""One lone task."""
from pydantic import BaseModel
from agentspec import Task

class Out(BaseModel):
    ok: bool

class Solo(Task):
    """Do the one thing."""
    returns: Out
    on_failure = {"ok": False}
'''
    payload = build_payload(parse_source(src, "solo.aspec.py"))
    assert payload["pipeline"] is None
    assert payload["edges"] == []


# ---------------- server ----------------


def _get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
            return r.status, r.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def test_server_serves_ui_and_payload(fixtures_dir):
    httpd, state, stop = create_server(fixtures_dir / "bookbank_routine.aspec.py", port=0)
    port = httpd.server_address[1]
    import threading

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(port, "/")
        assert status == 200 and b"aspec studio" in body
        status, body = _get(port, "/api/spec")
        snap = json.loads(body)
        assert snap["version"] == 1
        assert snap["errors"] == []
        assert snap["payload"]["root"] == "BookbankRun"
        # the source pane's text and each step's sync anchor
        assert snap["source"].startswith('"""BookBank headless book generation')
        steps = {s["var"]: s for s in snap["payload"]["pipeline"]["steps"]}
        src_lines = snap["source"].split("\n")
        assert "workspace = ResolveWorkspace()" in src_lines[steps["workspace"]["line"] - 1]
        assert "outcomes = [" in src_lines[steps["outcomes"]["line"] - 1]
        status, _ = _get(port, "/nope")
        assert status == 404
    finally:
        stop.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_prompt_endpoint(fixtures_dir):
    httpd, state, stop = create_server(fixtures_dir / "bookbank_routine.aspec.py", port=0)
    port = httpd.server_address[1]
    import threading

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(port, "/api/prompt?task=SelectIssue")
        payload = json.loads(body)
        assert status == 200
        assert payload["used_trace"] is False
        assert "# Task: SelectIssue" in payload["prompt"]
        assert "verify-freeform-claim" in payload["prompt"]  # SelectIssue's own rule
        assert "gates-are-clean-stops" in payload["prompt"]  # inherited orchestrator rule

        # the orchestrator itself: no reducer-prompt preview (yet)
        status, body = _get(port, "/api/prompt?task=BookbankRun")
        assert status == 400
        assert "orchestrator" in json.loads(body)["error"]

        status, body = _get(port, "/api/prompt?task=NoSuchTask")
        assert status == 404

        status, body = _get(port, "/api/prompt")
        assert status == 400

        # dev mode only adds the clarifying-question line for a task that
        # actually declares doubt (on_uncertain / Escalate)
        status, body = _get(port, "/api/prompt?task=ResolveWorkspace&dev=1")
        assert "clarifying question" in json.loads(body)["prompt"]
        status, body = _get(port, "/api/prompt?task=ResolveWorkspace&dev=0")
        assert "clarifying question" not in json.loads(body)["prompt"]
        status, body = _get(port, "/api/prompt?task=GenerateBook&dev=1")
        assert "clarifying question" not in json.loads(body)["prompt"]  # no doubt declared
    finally:
        stop.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_prompt_uses_trace_output_when_available(fixtures_dir):
    from agentspec.parser import parse_file
    from agentspec.studio.prompt import render_task_prompt

    module = parse_file(fixtures_dir / "bookbank_routine.aspec.py")
    trace = {
        "steps": [
            {"var": "issue", "task": "SelectIssue", "status": "ok", "output": {"number": 122}},
        ]
    }
    prompt, used_trace = render_task_prompt(module, "GenerateBook", trace=trace)
    assert used_trace is True
    assert '"issue_number": 122' in prompt


def test_prompt_rejects_task_with_no_named_returns_schema():
    from agentspec.parser import parse_source
    from agentspec.studio.prompt import render_task_prompt

    src = '''"""Doc."""
from agentspec import Task

class Solo(Task):
    """Do the one thing."""
    returns: str
    on_failure = {"ok": False}
'''
    module = parse_source(src, "solo.aspec.py")
    with pytest.raises(ValueError, match="no named returns schema"):
        render_task_prompt(module, "Solo")


def test_state_refresh_on_change_and_error_keeps_last_good(fixtures_dir, tmp_path):
    spec = tmp_path / "spec.aspec.py"
    shutil.copy(fixtures_dir / "minimal_good.aspec.py", spec)
    from agentspec.studio import StudioState

    state = StudioState(spec)
    assert state.version == 1 and state.payload["root"] == "InboxRoutine"
    assert not state.changed_on_disk()

    # a broken edit: version bumps, errors surface, last good payload kept
    spec.write_text("import os\n")
    assert state.changed_on_disk()
    state.refresh()
    assert state.version == 2
    assert state.errors
    assert state.payload["root"] == "InboxRoutine"

    # fixed again: errors clear, payload rebuilt
    shutil.copy(fixtures_dir / "minimal_good.aspec.py", spec)
    state.refresh()
    assert state.version == 3
    assert state.errors == []


def test_cli_studio_missing_file(capsys):
    assert main(["studio", "/nonexistent/spec.aspec.py"]) == 1
    assert "no such file" in capsys.readouterr().err


def test_payload_reduction_table(bookbank_payload):
    table = bookbank_payload["pipeline"]["reduction"]
    assert table["source"] == "derivation"
    assert table["name"] == "outcomes"
    assert table["fields"] == ["alert", "outcome", "stopped_at", "validator_errors"]
    assert len(table["rows"]) == 9
    assert table["rows"][-1]["condition"] == "otherwise"
    assert table["rows"][0]["values"]["outcome"] == "workspace_failed"
    assert table["rows"][-2]["values"]["validator_errors"] == "build.validator_errors"


def test_export_self_contained(fixtures_dir, tmp_path, capsys):
    out = tmp_path / "bookbank.html"
    assert (
        main(["studio", str(fixtures_dir / "bookbank_routine.aspec.py"), "--export", str(out)]) == 0
    )
    assert "wrote" in capsys.readouterr().out
    html = out.read_text()
    assert "window.EMBEDDED_SPEC" in html
    assert "window.EMBEDDED_SOURCE" in html  # the source pane works offline too
    assert '"root": "BookbankRun"' in html
    assert "aspec studio" in html
    assert html.count("window.EMBEDDED_SPEC = {") == 1  # injected exactly once


def test_export_refuses_broken_spec(tmp_path, capsys):
    spec = tmp_path / "broken.aspec.py"
    spec.write_text("import os\n")
    assert main(["studio", str(spec), "--export", str(tmp_path / "x.html")]) == 1
    assert "parse errors" in capsys.readouterr().err


def _post(port, path, obj):
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(obj).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def test_trace_pins_and_mcp(fixtures_dir, tmp_path):
    import threading

    trace = tmp_path / "trace.json"
    trace.write_text(
        json.dumps(
            {
                "task": "BookbankRun",
                "mode": "orchestrate",
                "status": "conforming",
                "steps": [
                    {"var": "workspace", "task": "ResolveWorkspace", "status": "ok"},
                    {"var": "plugin", "task": "VerifyPlugin", "status": "skipped"},
                ],
            }
        )
    )
    httpd, state, stop = create_server(
        fixtures_dir / "bookbank_routine.aspec.py", port=0, trace_path=trace
    )
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        _, body = _get(port, "/api/spec")
        snap = json.loads(body)
        assert snap["trace"]["status"] == "conforming"
        assert snap["pins"] == {}

        # pins + notes round-trip, and bump the version
        v1 = snap["version"]
        _post(port, "/api/pin", {"var": "build"})
        _post(port, "/api/note", {"var": "build", "note": "tighten the branch rule"})
        _, body = _get(port, "/api/spec")
        snap = json.loads(body)
        assert snap["pins"] == {"build": "tighten the branch rule"}
        assert snap["version"] > v1

        # MCP: the agent reads the pins and flashes a node
        reply = _post(
            port,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_pinned", "arguments": {}},
            },
        )
        pinned = json.loads(reply["result"]["content"][0]["text"])
        assert pinned == [
            {"var": "build", "note": "tighten the branch rule", "task": "GenerateBook"}
        ]

        _post(
            port,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "highlight_node", "arguments": {"var": "build"}},
            },
        )
        _, body = _get(port, "/api/spec")
        assert json.loads(body)["highlight"] == "build"
        _post(port, "/api/highlight_done", {})
        _, body = _get(port, "/api/spec")
        assert json.loads(body)["highlight"] is None

        # unpin clears
        _post(port, "/api/pin", {"var": "build", "pinned": False})
        _, body = _get(port, "/api/spec")
        assert json.loads(body)["pins"] == {}

        # the trace file is watched: an edit shows as changed_on_disk
        assert not state.changed_on_disk()
        trace.write_text(
            json.dumps({"task": "x", "mode": "single", "status": "aborted", "steps": []})
        )
        assert state.changed_on_disk()
        state.refresh()
        assert state.snapshot()["trace"]["status"] == "aborted"
    finally:
        stop.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
