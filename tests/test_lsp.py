"""`aspec lsp` phase L0: framing, diagnostics derivation, and a full session."""

import io

import pytest

from agentspec.cli import SUBCOMMANDS
from agentspec.lsp import Connection, LspServer, ProtocolError, compute_diagnostics

# ---------------- framing ----------------


def test_framing_round_trip():
    out = io.BytesIO()
    Connection(io.BytesIO(), out).write_message({"jsonrpc": "2.0", "id": 1, "result": None})
    raw = out.getvalue()
    assert raw.startswith(b"Content-Length: ")
    conn = Connection(io.BytesIO(raw), io.BytesIO())
    assert conn.read_message() == {"jsonrpc": "2.0", "id": 1, "result": None}
    assert conn.read_message() is None  # clean EOF


def test_framing_errors():
    with pytest.raises(ProtocolError, match="Content-Length"):
        Connection(io.BytesIO(b"\r\n"), io.BytesIO()).read_message()
    with pytest.raises(ProtocolError, match="body"):
        Connection(io.BytesIO(b"Content-Length: 99\r\n\r\n{}"), io.BytesIO()).read_message()


# ---------------- diagnostics ----------------

URI = "file:///tmp/spec.aspec.py"


def test_diagnostics_clean_spec(fixtures_dir):
    text = (fixtures_dir / "minimal_good.aspec.py").read_text()
    assert compute_diagnostics(URI, text) == []


def test_diagnostics_parse_error_zero_based():
    diags = compute_diagnostics(URI, '"""Doc."""\nimport os\n')
    assert diags, "expected a parser diagnostic"
    diag = diags[0]
    assert diag["code"].startswith("P0")
    assert diag["severity"] == 1
    assert diag["source"] == "aspec"
    assert diag["range"]["start"]["line"] == 1  # import os is 1-based line 2


def test_diagnostics_lint_warning(fixtures_dir):
    text = (fixtures_dir / "minimal_good.aspec.py").read_text()
    text = text.replace(
        "    constraints = HOUSE_RULES\n    undo",
        '    constraints = HOUSE_RULES + [Rule("never-file-twice", "Never file twice")]\n    undo',
    )
    diags = compute_diagnostics(URI, text)
    assert any(d["code"].startswith("AS") and d["severity"] == 2 for d in diags)


# ---------------- a scripted session ----------------


def frame(messages):
    buf = io.BytesIO()
    conn = Connection(io.BytesIO(), buf)
    for m in messages:
        conn.write_message(m)
    return io.BytesIO(buf.getvalue())


def read_all(raw: bytes):
    conn = Connection(io.BytesIO(raw), io.BytesIO())
    out = []
    while (m := conn.read_message()) is not None:
        out.append(m)
    return out


def run_session(messages):
    out = io.BytesIO()
    server = LspServer(Connection(frame(messages), out))
    code = server.serve_forever()
    return code, read_all(out.getvalue())


def test_full_session(fixtures_dir):
    good = (fixtures_dir / "minimal_good.aspec.py").read_text()
    code, out = run_session(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "initialized", "params": {}},
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": URI,
                        "languageId": "python",
                        "version": 1,
                        "text": good,
                    }
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didChange",
                "params": {
                    "textDocument": {"uri": URI, "version": 2},
                    "contentChanges": [{"text": good + "\nimport os\n"}],
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didClose",
                "params": {"textDocument": {"uri": URI}},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "textDocument/rename", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}},
            {"jsonrpc": "2.0", "method": "exit"},
        ]
    )
    assert code == 0

    init = next(m for m in out if m.get("id") == 1)
    caps = init["result"]["capabilities"]
    sync = caps["textDocumentSync"]
    assert sync["change"] == 1 and sync["openClose"] is True
    assert caps["hoverProvider"] and caps["definitionProvider"] and caps["documentSymbolProvider"]

    published = [m for m in out if m.get("method") == "textDocument/publishDiagnostics"]
    assert len(published) == 3  # open, change, close
    assert published[0]["params"]["diagnostics"] == []  # clean spec
    assert any(d["code"].startswith("P0") for d in published[1]["params"]["diagnostics"])
    assert published[2]["params"]["diagnostics"] == []  # close clears

    rename = next(m for m in out if m.get("id") == 2)
    assert rename["error"]["code"] == -32601  # rename is not implemented

    shutdown = next(m for m in out if m.get("id") == 3)
    assert shutdown["result"] is None


def test_exit_without_shutdown_is_nonzero():
    code, _ = run_session([{"jsonrpc": "2.0", "method": "exit"}])
    assert code == 1


def test_client_hangup_after_shutdown_is_clean():
    code, _ = run_session([{"jsonrpc": "2.0", "id": 1, "method": "shutdown", "params": {}}])
    assert code == 0


def test_cli_advertises_lsp():
    assert "lsp" in SUBCOMMANDS
