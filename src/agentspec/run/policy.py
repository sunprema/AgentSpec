"""Apply a declared FailurePolicy exactly as written (spec §8) — the harness
never substitutes its own recovery strategy."""

from collections.abc import Callable
from typing import Any

from agentspec.parser import FailurePolicy

Attempt = Callable[[], dict | None]  # one fresh guarded attempt


def resolve_with_policy(
    policy: FailurePolicy | None, attempt: Attempt, notes: list[str]
) -> tuple[str, Any]:
    """Returns (status, output): 'conforming' | 'declared_fallback' | 'aborted'."""
    if policy is None:
        notes.append("no on_failure declared; the conservative reading is abort")
        return "aborted", None
    if policy.kind == "literal":
        return "declared_fallback", policy.literal
    if policy.kind == "abort":
        return "aborted", None
    if policy.kind == "retry":
        for _ in range(policy.max or 1):
            output = attempt()
            if output is not None:
                return "conforming", output
        return resolve_with_policy(policy.then, attempt, notes)
    # escalate: the only legitimate way to "ask" in an unattended run — but
    # this harness has no waiting channel, so record it and honor `then`.
    notes.append(
        f"Escalate(channel='{policy.channel}') declared; this harness cannot "
        "deliver it or wait — applying the declared then"
    )
    return resolve_with_policy(policy.then, attempt, notes)
