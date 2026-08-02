"""The guard: validate model output against the declared contract and feed
violations back verbatim for bounded repair (spec §11: run/orchestrate).

Dev mode: when an `ask` callback is supplied, a reply of exactly
{"question": "..."} is answered by the present human once per call and the
model re-prompted with the answer; without the callback (unattended
dispatch) the same reply is a violation fed back verbatim.
"""

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from agentspec.eval import Adapter, EvalError, extract_json
from agentspec.run.model import Clarification

Ask = Callable[[str], str | None]


class RunError(Exception):
    """The spec, dispatch, or harness setup is unusable (not a run failure)."""


class GuardOutcome(BaseModel):
    output: Any = None  # the validated output dict, or None if nonconforming
    attempts: int
    failures: list[str] = Field(default_factory=list)
    clarifications: list[Clarification] = Field(default_factory=list)
    raw: str = ""


def guarded_call(
    adapter: Adapter,
    prompt: str,
    output_model,
    *,
    max_repairs: int = 2,
    ask: Ask | None = None,
    max_questions: int = 1,
) -> GuardOutcome:
    raw, error = _call(adapter, prompt)
    if raw is None:
        return GuardOutcome(attempts=1, failures=[error or "adapter failed"])
    attempts, repairs, questions = 1, 0, 0
    history: list[str] = []
    clarifications: list[Clarification] = []

    def done(output: Any = None) -> GuardOutcome:
        return GuardOutcome(
            output=output,
            attempts=attempts,
            failures=history,
            clarifications=clarifications,
            raw=raw or "",
        )

    while True:
        question = _question_in(raw)
        if question is not None and ask is not None and questions < max_questions:
            questions += 1
            answer = ask(question)
            clarifications.append(Clarification(question=question, answer=answer))
            raw, error = _call(adapter, _clarified_prompt(prompt, question, answer))
            attempts += 1
            if raw is None:
                history.append(error or "adapter failed")
                return done()
            continue
        if question is not None and ask is None:
            failures = [
                "clarifying questions are not permitted in unattended dispatch "
                "— proceed, using your declared on_uncertain output if you "
                "genuinely cannot decide"
            ]
        else:
            output, failures = _validate(raw, output_model)
            if output is not None:
                return done(output)
        history.extend(failures)
        if repairs >= max_repairs:
            return done()
        raw, error = _call(adapter, _repair_prompt(raw, failures, output_model))
        attempts += 1
        repairs += 1
        if raw is None:
            history.append(error or "adapter failed")
            return done()


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


def _question_in(raw: str | None) -> str | None:
    """A reply of exactly {"question": "..."} is a clarification request."""
    if raw is None:
        return None
    try:
        payload = extract_json(raw)
    except EvalError:
        return None
    if (
        isinstance(payload, dict)
        and set(payload) == {"question"}
        and isinstance(payload["question"], str)
        and payload["question"].strip()
    ):
        return payload["question"].strip()
    return None


def _clarified_prompt(prompt: str, question: str, answer: str | None) -> str:
    if answer is not None:
        resolution = f"Answer from the developer: {answer}"
    else:
        resolution = (
            "The developer declined to answer. Proceed without it; if you "
            "genuinely cannot decide, return your declared on_uncertain "
            "output. This gap should become a rule in the spec."
        )
    return "\n".join(
        [
            prompt,
            "",
            "# Clarification",
            f"You asked: {question}",
            resolution,
            "",
            "No further questions are available for this task. Complete the "
            "work and end with the JSON output.",
        ]
    )
