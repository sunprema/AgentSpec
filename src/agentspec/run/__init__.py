"""Guarded execution: outputs validated against declared contracts,
violations fed back verbatim for bounded repair, declared failure
semantics honored — deliberately thin, never a runtime framework."""

from agentspec.run.guard import GuardOutcome, RunError, guarded_call
from agentspec.run.model import RuleConflict, RunResult, StepRecord, Substitution
from agentspec.run.orchestrate import orchestrate
from agentspec.run.policy import resolve_with_policy
from agentspec.run.single import run_routine

__all__ = [
    "GuardOutcome",
    "RuleConflict",
    "RunError",
    "RunResult",
    "StepRecord",
    "Substitution",
    "guarded_call",
    "orchestrate",
    "resolve_with_policy",
    "run_routine",
]
