"""The guard: validate model output against the declared contract and feed
violations back verbatim for bounded repair (spec §11: run/orchestrate)."""

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from agentspec.eval import Adapter, EvalError, extract_json


class RunError(Exception):
    """The spec, dispatch, or harness setup is unusable (not a run failure)."""


class GuardOutcome(BaseModel):
    output: Any = None  # the validated output dict, or None if nonconforming
    attempts: int
    failures: list[str] = Field(default_factory=list)
    raw: str = ""


def guarded_call(
    adapter: Adapter, prompt: str, output_model, *, max_repairs: int = 2
) -> GuardOutcome:
    raw, error = _call(adapter, prompt)
    if raw is None:
        return GuardOutcome(attempts=1, failures=[error or "adapter failed"])
    attempts, history = 1, []
    while True:
        output, failures = _validate(raw, output_model)
        if output is not None:
            return GuardOutcome(output=output, attempts=attempts, failures=history, raw=raw)
        history.extend(failures)
        if attempts > max_repairs:
            return GuardOutcome(attempts=attempts, failures=history, raw=raw)
        raw, error = _call(adapter, _repair_prompt(raw, failures, output_model))
        attempts += 1
        if raw is None:
            history.append(error or "adapter failed")
            return GuardOutcome(attempts=attempts, failures=history)


def _call(adapter: Adapter, prompt: str) -> tuple[str | None, str | None]:
    try:
        return adapter(prompt), None
    except Exception as exc:
        return None, f"adapter: {exc}"


def _validate(raw: str, output_model) -> tuple[dict | None, list[str]]:
    try:
        payload = extract_json(raw)
    except EvalError as exc:
        return None, [str(exc)]
    try:
        return output_model.model_validate(payload).model_dump(), []
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        ]


def _repair_prompt(previous: str, failures: list[str], output_model) -> str:
    return "\n".join(
        [
            "Your previous reply did not conform to the declared output contract.",
            "Do not redo the work — correct the final JSON for the run you "
            "already performed. Real values only; never coerce or pad values "
            "to satisfy the shape.",
            "",
            "# Your previous reply",
            previous,
            "",
            "# Violations (verbatim)",
            *[f"- {failure}" for failure in failures],
            "",
            "# Output contract (JSON Schema)",
            json.dumps(output_model.model_json_schema(), indent=2),
            "",
            "Reply with ONLY the corrected JSON object.",
        ]
    )
