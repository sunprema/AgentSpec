"""JSON-RPC framing for the Language Server Protocol, over any binary
streams — stdio in production, BytesIO in tests.

LSP messages are JSON bodies behind MIME-style headers:

    Content-Length: 123\r\n
    \r\n
    {"jsonrpc": "2.0", ...}
"""

import json
from typing import Any, BinaryIO


class ProtocolError(Exception):
    pass


class Connection:
    def __init__(self, reader: BinaryIO, writer: BinaryIO):
        self.reader = reader
        self.writer = writer

    def read_message(self) -> dict[str, Any] | None:
        """One message, or None on a clean EOF at a message boundary."""
        length: int | None = None
        while True:
            line = self.reader.readline()
            if not line:
                if length is None:
                    return None
                raise ProtocolError("stream ended inside message headers")
            line = line.strip()
            if not line:
                break  # blank line: headers done
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                try:
                    length = int(value.strip())
                except ValueError as exc:
                    raise ProtocolError(f"bad Content-Length: {value!r}") from exc
        if length is None:
            raise ProtocolError("message without Content-Length")
        body = self.reader.read(length)
        if len(body) != length:
            raise ProtocolError("stream ended inside message body")
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"body is not JSON: {exc}") from exc

    def write_message(self, message: dict[str, Any]) -> None:
        body = json.dumps(message).encode()
        self.writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode())
        self.writer.write(body)
        self.writer.flush()
