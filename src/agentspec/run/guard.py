"""The guard: validate model output against the declared contract and feed
violations back verbatim for bounded repair (spec §11: run/orchestrate).

Dev mode: when an `ask` callback is supplied, a reply of exactly
{"question": "..."} is answered by the present human once per call and the
model re-prompted with the answer; without the callback (unattended
dispatch) the same reply is a violation fed back verbatim.

The run envelope (spec §9): a fenced block tagged `json envelope` before
the final JSON is the agent's channel for mandated recordings — tool
substitutions and conservatively resolved rule conflicts. It is stripped
before contract validation and parsed leniently: a malformed block is
noted and ignored, never a violation — the envelope can never fail a
conforming run.
"""

import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from agentspec.eval import Adapter, EvalError, extract_json
from agentspec.run.model import Clarification, RuleConflict, Substitution

Ask = Callable[[str], str | None]

_ENVELOPE_RE = re.compile(r"```json\s+envelope\s*\n(.*?)```", re.S)


class RunError(Exception):
    """The spec, dispatch, or harness setup is unusable (not a run failure)."""


class GuardOutcome(BaseModel):
    output: Any = None  # the validated output dict, or None if nonconforming
    attempts: int
    failures: list[str] = Field(default_factory=list)
    clarifications: list[Clarification] = Field(default_factory=list)
    substitutions: list[Substitution] = Field(default_factory=list)
    rule_conflicts: list[RuleConflict] = Field(default_factory=list)
    envelope_warning: str = ""  # malformed envelope block (telemetry only)
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
    raws: list[str] = [raw]

    def done(output: Any = None) -> GuardOutcome:
        # The latest reply carrying an envelope block wins: repair replies
        # are JSON-only, so the recordings usually ride an earlier attempt.
        substitutions: list[Substitution] = []
        rule_conflicts: list[RuleConflict] = []
        warning = ""
        for text in reversed(raws):
            parsed = _envelope_in(text)
            if parsed is not None:
                substitutions, rule_conflicts, warning = parsed
                break
        return GuardOutcome(
            output=output,
            attempts=attempts,
            failures=history,
            clarifications=clarifications,
            substitutions=substitutions,
            rule_conflicts=rule_conflicts,
            envelope_warning=warning,
            raw=raw or "",
        )

    while True:
        question = _question_in(_strip_envelope(raw))
        if question is not None and ask is not None and questions < max_questions:
            questions += 1
            answer = ask(question)
            clarifications.append(Clarification(question=question, answer=answer))
            raw, error = _call(adapter, _clarified_prompt(prompt, question, answer))
            attempts += 1
            if raw is None:
                history.append(error or "adapter failed")
                return done()
            raws.append(raw)
            continue
        if question is not None and ask is None:
            failures = [
                "clarifying questions are not permitted in unattended dispatch "
                "— proceed, using your declared on_uncertain output if you "
                "genuinely cannot decide"
            ]
        else:
            output, failures = _validate(_strip_envelope(raw), output_model)
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
        raws.append(raw)


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


class _Envelope(BaseModel):
    substitutions: list[Substitution] = Field(default_factory=list)
    rule_conflicts: list[RuleConflict] = Field(default_factory=list)


def _strip_envelope(raw: str | None) -> str:
    """Remove envelope blocks before contract validation: the envelope is
    telemetry, never part of the returns contract (spec §9)."""
    return _ENVELOPE_RE.sub("", raw or "")


def _envelope_in(raw: str) -> tuple[list[Substitution], list[RuleConflict], str] | None:
    """Parse the reply's `json envelope` block. None: no block present.
    A malformed block yields empty records plus a warning — noted and
    ignored, never a violation."""
    match = _ENVELOPE_RE.search(raw)
    if match is None:
        return None
    try:
        envelope = _Envelope.model_validate(json.loads(match.group(1)))
    except (json.JSONDecodeError, ValidationError) as exc:
        return [], [], f"malformed envelope block ignored: {str(exc).splitlines()[0]}"
    return envelope.substitutions, envelope.rule_conflicts, ""


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
