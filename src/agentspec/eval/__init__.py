"""Fixture-based behavioral tests: known inputs, declared-contract
validation, declarative assertions — gate spec changes like code changes."""

from agentspec.eval.adapters import subprocess_adapter
from agentspec.eval.model import CaseResult, EvalCase, EvalFile, EvalReport
from agentspec.eval.runner import (
    Adapter,
    EvalError,
    check_expectations,
    extract_json,
    render_prompt,
    render_report,
    run_eval,
)
from agentspec.eval.schema import build_output_model

__all__ = [
    "Adapter",
    "CaseResult",
    "EvalCase",
    "EvalError",
    "EvalFile",
    "EvalReport",
    "build_output_model",
    "check_expectations",
    "extract_json",
    "render_prompt",
    "render_report",
    "run_eval",
    "subprocess_adapter",
]
