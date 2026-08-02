"""The studio server: stdlib HTTP on 127.0.0.1, one page, one API.

GET  /           the packaged single-file UI
GET  /api/spec   {"version", "file", "errors", "payload", "trace",
                  "pins", "highlight"}
POST /api/pin    {"var": "build", "pinned": true}
POST /api/note   {"var": "build", "note": "..."}
POST /mcp        JSON-RPC: get_spec, get_pinned, highlight_node — so a
                 Claude session can read what the human marked

A watcher thread polls the spec (and optional trace) file mtimes; on change
it re-parses and bumps `version`. A parse that produces errors keeps the
last good payload — the UI shows the errors in a banner instead of losing
the canvas. Pins are session-scoped: the graph is derived, not state, and
restarting studio is cheap.

Edits are deliberately NOT mediated by this server (unlike Prompt Atlas):
the deterministic parser plus the file watcher make direct file edits safe —
an agent edits the spec file, studio re-parses on save.
"""

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any

from agentspec.parser import parse_file
from agentspec.studio.payload import build_payload

WATCH_INTERVAL_S = 1.0

EMBED_MARKER = '<div id="tip"></div>'


class StudioState:
    def __init__(self, spec_path: str | Path, trace_path: str | Path | None = None):
        self.path = Path(spec_path).resolve()
        self.trace_path = Path(trace_path).resolve() if trace_path else None
        self.lock = threading.Lock()
        self.version = 0
        self.payload: dict[str, Any] | None = None
        self.errors: list[str] = []
        self.trace: dict[str, Any] | None = None
        self.pins: dict[str, str] = {}  # step var -> note ("" = pinned, no note)
        self.highlight: str | None = None
        self.mtimes: dict[Path, float] = {}
        self.refresh()

    def refresh(self) -> None:
        errors: list[str] = []
        payload = None
        try:
            self.mtimes[self.path] = self.path.stat().st_mtime
            module = parse_file(self.path, cache={})
            errors = [d.render() for d in module.errors]
            payload = None if errors else build_payload(module)
        except OSError as exc:
            errors = [str(exc)]
        trace = self._load_trace(errors)
        with self.lock:
            self.errors = errors
            if payload is not None:
                self.payload = payload
            self.trace = trace
            self.version += 1

    def _load_trace(self, errors: list[str]) -> dict[str, Any] | None:
        if self.trace_path is None:
            return None
        try:
            self.mtimes[self.trace_path] = self.trace_path.stat().st_mtime
            return json.loads(self.trace_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"trace: {exc}")
            return None

    def changed_on_disk(self) -> bool:
        for path in filter(None, (self.path, self.trace_path)):
            try:
                if path.stat().st_mtime != self.mtimes.get(path):
                    return True
            except OSError:
                continue
        return False

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "version": self.version,
                "file": str(self.path),
                "errors": self.errors,
                "payload": self.payload,
                "trace": self.trace,
                "pins": dict(self.pins),
                "highlight": self.highlight,
            }

    def bump(self) -> None:
        with self.lock:
            self.version += 1


def _watch(state: StudioState, stop: threading.Event) -> None:
    while not stop.wait(WATCH_INTERVAL_S):
        if state.changed_on_disk():
            state.refresh()


def _ui_html() -> bytes:
    return resources.files("agentspec.studio").joinpath("ui.html").read_bytes()


def export_html(spec_path: str | Path) -> str:
    """The studio view as one self-contained file: the payload embedded,
    polling disabled by its presence, simulator still fully client-side."""
    module = parse_file(spec_path, cache={})
    if module.errors:
        rendered = "; ".join(d.render() for d in module.errors)
        raise ValueError(f"spec has parse errors: {rendered}")
    payload = build_payload(module)
    embed = f"<script>window.EMBEDDED_SPEC = {json.dumps(payload)};</script>"
    html = _ui_html().decode()
    if EMBED_MARKER not in html:
        raise ValueError("ui.html embed marker missing")
    return html.replace(EMBED_MARKER, EMBED_MARKER + "\n" + embed, 1)


# ---------------- MCP (read + highlight only) ----------------

_MCP_TOOLS = {
    "get_spec": "The parsed spec payload studio is showing (tasks, rules, pipeline, lint)",
    "get_pinned": "Steps the human pinned in the studio UI, with their notes",
    "highlight_node": 'Flash a step on the human\'s canvas (params: {"var": ...})',
}


def _mcp_handle(state: StudioState, message: dict[str, Any]) -> dict[str, Any] | None:
    mid = message.get("id")
    method = message.get("method", "")
    params = message.get("params") or {}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": params.get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aspec-studio", "version": "0.1.0"},
            },
        }
    if method.startswith("notifications/"):
        return None
    if method == "tools/list":
        tools = [
            {
                "name": name,
                "description": description,
                "inputSchema": {"type": "object", "properties": {}},
            }
            for name, description in _MCP_TOOLS.items()
        ]
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "get_spec":
            result: Any = state.snapshot()["payload"]
        elif name == "get_pinned":
            snap = state.snapshot()
            steps = (
                {s["var"]: s for s in (snap["payload"] or {}).get("pipeline", {}).get("steps", [])}
                if snap["payload"] and snap["payload"].get("pipeline")
                else {}
            )
            result = [
                {"var": var, "note": note, "task": steps.get(var, {}).get("task")}
                for var, note in snap["pins"].items()
            ]
        elif name == "highlight_node":
            state.highlight = args.get("var")
            state.bump()
            result = {"ok": True, "highlighted": args.get("var")}
        else:
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32602, "message": f"unknown tool {name}"},
            }
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]},
        }
    if mid is not None:
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return None


def _make_handler(state: StudioState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            pass  # keep the terminal quiet; the UI is the output

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: Any, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            if self.path == "/" or self.path.startswith("/index"):
                self._send(200, _ui_html(), "text/html; charset=utf-8")
            elif self.path.startswith("/api/spec"):
                self._json(state.snapshot())
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            body = self._body()
            if self.path == "/api/pin":
                var = body.get("var")
                if var:
                    if body.get("pinned", True):
                        state.pins.setdefault(var, "")
                    else:
                        state.pins.pop(var, None)
                    state.bump()
                self._json({"ok": True, "pins": dict(state.pins)})
            elif self.path == "/api/note":
                var = body.get("var")
                if var:
                    state.pins[var] = body.get("note", "")
                    state.bump()
                self._json({"ok": True})
            elif self.path == "/api/highlight_done":
                state.highlight = None
                self._json({"ok": True})
            elif self.path == "/mcp":
                reply = _mcp_handle(state, body)
                if reply is None:
                    self._send(202, b"", "application/json")
                else:
                    self._json(reply)
            else:
                self._json({"error": "not found"}, 404)

    return Handler


def create_server(
    spec_path: str | Path, port: int = 7788, trace_path: str | Path | None = None
) -> tuple[ThreadingHTTPServer, StudioState, threading.Event]:
    """Build the server without blocking — the CLI and the tests share this."""
    state = StudioState(spec_path, trace_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(state))
    stop = threading.Event()
    threading.Thread(target=_watch, args=(state, stop), daemon=True).start()
    return httpd, state, stop


def serve(
    spec_path: str | Path,
    port: int = 7788,
    open_browser: bool = True,
    trace_path: str | Path | None = None,
) -> int:
    try:
        httpd, state, stop = create_server(spec_path, port, trace_path)
    except OSError as exc:
        print(f"aspec studio: {exc}")
        return 1
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    banner = f"aspec studio — {state.path.name} — {url}"
    if state.trace_path is not None:
        banner += f"  (trace: {state.trace_path.name})"
    if state.errors:
        banner += f"  ({len(state.errors)} parse error(s); showing them in the UI)"
    print(banner)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
    return 0
