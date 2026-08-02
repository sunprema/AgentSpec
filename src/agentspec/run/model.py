"""Run results: what happened, under which declared semantics."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class Clarification(BaseModel):
    """One dev-mode question and its answer — by definition, a spec gap."""

    task: str = ""
    question: str
    answer: str | None = None  # None: the human declined; the declared
    # fallback applied


class StepRecord(BaseModel):
    var: str
    task: str
    status: Literal["ok", "skipped", "declared_fallback", "failed", "unwound"]
    attempts: int = 0
    failures: list[str] = Field(default_factory=list)
    output: Any = None


class RunResult(BaseModel):
    task: str
    mode: Literal["single", "orchestrate"]
    # "conforming": the contract was met; "declared_fallback": a declared
    # on_failure output was returned (a legitimate declared outcome);
    # "aborted": the declared abort path ran (undo unwind where declared).
    status: Literal["conforming", "declared_fallback", "aborted"]
    output: Any = None
    report: str = ""  # the agent's run-report prose (single mode)
    steps: list[StepRecord] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    clarifications: list[Clarification] = Field(default_factory=list)
    adapter_calls: int = 0
