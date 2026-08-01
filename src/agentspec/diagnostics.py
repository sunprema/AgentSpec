"""The diagnostic type shared by every aspec subcommand.

Parser diagnostics use `P0xx` codes (structural: the file cannot be fully
represented as a SpecModel). Lint diagnostics use `AS0xx` codes (semantic:
the SpecModel violates the language's rules). One output format serves both.
"""

from typing import Literal

from pydantic import BaseModel


class Diagnostic(BaseModel):
    code: str
    message: str
    file: str
    line: int = 0
    col: int = 0
    severity: Literal["error", "warning"] = "error"

    def render(self) -> str:
        return f"{self.file}:{self.line}:{self.col}: {self.code} {self.message}"
