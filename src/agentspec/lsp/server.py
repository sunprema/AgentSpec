"""The AgentSpec language server: lifecycle + diagnostics (L0), hover,
go-to-definition, and document symbols (L1).

Single-threaded, full-document sync (spec files are small), stdlib only.
Diagnostics are the parser's P0xx plus the linter's AS0xx, republished on
every open/change/save. Positions are best-effort UTF-16 (identical to the
AST's offsets for ASCII specs).
"""

import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from agentspec.lint import lint_module
from agentspec.lsp import features
from agentspec.lsp.protocol import Connection, ProtocolError
from agentspec.parser import SpecModule, parse_source

TEXT_SYNC_FULL = 1
SEVERITY = {"error": 1, "warning": 2}

PARSE_ERROR = -32700
METHOD_NOT_FOUND = -32601
INVALID_REQUEST = -32600


def uri_to_path(uri: str) -> str | None:
    parts = urlsplit(uri)
    if parts.scheme != "file":
        return None
    return unquote(parts.path)


def doc_filename(uri: str) -> str:
    return uri_to_path(uri) or uri


def parse_document(uri: str, text: str) -> SpecModule:
    path = uri_to_path(uri)
    base_dir = Path(path).parent if path else None
    return parse_source(text, filename=doc_filename(uri), base_dir=base_dir)


def compute_diagnostics(uri: str, text: str) -> list[dict[str, Any]]:
    """Parse + lint one document; only diagnostics located in that document
    are published (imported sibling specs report in their own files)."""
    return diagnostics_from(parse_document(uri, text), doc_filename(uri))


def diagnostics_from(module: SpecModule, filename: str) -> list[dict[str, Any]]:
    out = []
    for diag in lint_module(module):
        if diag.file != filename:
            continue
        line = max(diag.line - 1, 0)
        col = max(diag.col, 0)
        out.append(
            {
                "range": {
                    "start": {"line": line, "character": col},
                    "end": {"line": line, "character": col},
                },
                "severity": SEVERITY.get(diag.severity, 2),
                "code": diag.code,
                "source": "aspec",
                "message": diag.message,
            }
        )
    return out


class LspServer:
    def __init__(self, connection: Connection):
        self.conn = connection
        self.documents: dict[str, str] = {}  # uri -> current text
        self.modules: dict[str, SpecModule] = {}  # uri -> parse of current text
        # uri -> last parse without errors: mid-keystroke text rarely parses,
        # and completions/hover should keep answering from the last good model
        self.good_modules: dict[str, SpecModule] = {}
        self.shutdown_requested = False
        self.exit_code: int | None = None

    # ---------------- plumbing ----------------

    def serve_forever(self) -> int:
        while self.exit_code is None:
            try:
                message = self.conn.read_message()
            except ProtocolError as exc:
                print(f"aspec lsp: protocol error: {exc}", file=sys.stderr)
                return 1
            if message is None:  # client hung up
                return 0 if self.shutdown_requested else 1
            self.handle(message)
        return self.exit_code

    def handle(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}
        if method is None:
            if msg_id is not None:  # a response — L0 sends no requests
                return
            return
        handler = getattr(self, "on_" + method.replace("/", "_").replace("$", "dollar"), None)
        if handler is None:
            if msg_id is not None:
                self._error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}")
            return  # unknown notifications are ignored per spec
        result = handler(params)
        if msg_id is not None:
            self._respond(msg_id, result)

    def _respond(self, msg_id: Any, result: Any) -> None:
        self.conn.write_message({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _error(self, msg_id: Any, code: int, message: str) -> None:
        self.conn.write_message(
            {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
        )

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self.conn.write_message({"jsonrpc": "2.0", "method": method, "params": params})

    # ---------------- lifecycle ----------------

    def on_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "capabilities": {
                "textDocumentSync": {
                    "openClose": True,
                    "change": TEXT_SYNC_FULL,
                    "save": True,
                },
                "hoverProvider": True,
                "definitionProvider": True,
                "documentSymbolProvider": True,
                "completionProvider": {"triggerCharacters": [".", '"']},
                "codeActionProvider": {"codeActionKinds": ["quickfix"]},
            },
            "serverInfo": {"name": "aspec-lsp", "version": "0.1.0"},
        }

    def on_initialized(self, params: dict[str, Any]) -> None:
        return None

    def on_shutdown(self, params: dict[str, Any]) -> None:
        self.shutdown_requested = True
        return None

    def on_exit(self, params: dict[str, Any]) -> None:
        self.exit_code = 0 if self.shutdown_requested else 1
        return None

    # ---------------- documents ----------------

    def _publish(self, uri: str) -> None:
        text = self.documents.get(uri, "")
        module = parse_document(uri, text)
        self.modules[uri] = module
        if module.ok:
            self.good_modules[uri] = module
        self._notify(
            "textDocument/publishDiagnostics",
            {"uri": uri, "diagnostics": diagnostics_from(module, doc_filename(uri))},
        )

    def _doc(self, params: dict[str, Any]) -> tuple[str, SpecModule, str] | None:
        uri = (params.get("textDocument") or {}).get("uri")
        if uri is None or uri not in self.documents:
            return None
        module = self.good_modules.get(uri) or self.modules[uri]
        return uri, module, self.documents[uri]

    def on_textDocument_didOpen(self, params: dict[str, Any]) -> None:  # noqa: N802 (LSP names)
        doc = params["textDocument"]
        self.documents[doc["uri"]] = doc["text"]
        self._publish(doc["uri"])

    def on_textDocument_didChange(self, params: dict[str, Any]) -> None:  # noqa: N802
        uri = params["textDocument"]["uri"]
        changes = params.get("contentChanges") or []
        if changes:  # full sync: the last change carries the whole document
            self.documents[uri] = changes[-1].get("text", "")
        self._publish(uri)

    def on_textDocument_didSave(self, params: dict[str, Any]) -> None:  # noqa: N802
        uri = params["textDocument"]["uri"]
        if "text" in params:
            self.documents[uri] = params["text"]
        self._publish(uri)

    def on_textDocument_didClose(self, params: dict[str, Any]) -> None:  # noqa: N802
        uri = params["textDocument"]["uri"]
        self.documents.pop(uri, None)
        self.modules.pop(uri, None)
        self.good_modules.pop(uri, None)
        self._notify("textDocument/publishDiagnostics", {"uri": uri, "diagnostics": []})

    # ---------------- L1: hover / definition / symbols ----------------

    def on_textDocument_hover(self, params: dict[str, Any]) -> Any:  # noqa: N802
        doc = self._doc(params)
        pos = params.get("position") or {}
        if doc is None or "line" not in pos:
            return None
        uri, module, text = doc
        return features.hover(module, doc_filename(uri), text, pos["line"], pos.get("character", 0))

    def on_textDocument_definition(self, params: dict[str, Any]) -> Any:  # noqa: N802
        doc = self._doc(params)
        pos = params.get("position") or {}
        if doc is None or "line" not in pos:
            return None
        uri, module, text = doc
        return features.definition(
            module, uri, doc_filename(uri), text, pos["line"], pos.get("character", 0)
        )

    def on_textDocument_documentSymbol(self, params: dict[str, Any]) -> Any:  # noqa: N802
        doc = self._doc(params)
        if doc is None:
            return []
        uri, module, text = doc
        return features.document_symbols(module, doc_filename(uri), text)

    def on_textDocument_completion(self, params: dict[str, Any]) -> Any:  # noqa: N802
        doc = self._doc(params)
        pos = params.get("position") or {}
        if doc is None or "line" not in pos:
            return []
        uri, module, text = doc
        return features.completion(
            module, doc_filename(uri), text, pos["line"], pos.get("character", 0)
        )

    def on_textDocument_codeAction(self, params: dict[str, Any]) -> Any:  # noqa: N802
        doc = self._doc(params)
        if doc is None:
            return []
        uri, module, text = doc
        diagnostics = (params.get("context") or {}).get("diagnostics") or []
        return features.code_actions(module, uri, text, diagnostics)


def serve_stdio() -> int:
    """Run the server on stdin/stdout. Nothing but protocol goes to stdout."""
    connection = Connection(sys.stdin.buffer, sys.stdout.buffer)
    return LspServer(connection).serve_forever()
