"""Eval file format and result types.

An eval file is TOML — declarative, reviewable, no code:

    spec = "../BookBank_routine.aspec.py"   # relative to the eval file
    task = "SelectIssue"                    # default: the spec's root task

    [[case]]
    name = "empty queue yields a clean no-work stop"
    [case.inputs]
    freeform_context = "scheduled run"
    [case.expect]
    found = false                # bare value: equality
    number = { lte = 0 }         # operator table: bounds / membership
    tier = { in = ["a", "b"] }

Expectation operators (a closed set — anything richer belongs in the spec):
`in`, `not_in`, `gte`, `lte`, `contains`; a bare value means equality.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

EXPECT_OPERATORS = {"eq", "in", "not_in", "gte", "lte", "contains"}


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    expect: dict[str, Any] = Field(default_factory=dict)


class EvalFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: str
    task: str | None = None
    case: list[EvalCase] = Field(default_factory=list)


class CaseResult(BaseModel):
    name: str
    passed: bool
    failures: list[str] = Field(default_factory=list)
    output: Any = None  # the validated output, when validation succeeded


class EvalReport(BaseModel):
    file: str
    task: str
    results: list[CaseResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)
