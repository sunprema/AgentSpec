"""Conformance, type-flow, and rule-hygiene checks over a SpecModel (ASxxx rules)."""

from agentspec.diagnostics import Diagnostic
from agentspec.lint.catalog import CATALOG
from agentspec.lint.derivations import check_derivations
from agentspec.lint.hygiene import check_hygiene
from agentspec.lint.pipeline import check_pipelines
from agentspec.lint.reduction import check_reduction
from agentspec.lint.structure import check_structure
from agentspec.parser import SpecModule

PASSES = [check_structure, check_pipelines, check_hygiene, check_reduction, check_derivations]

__all__ = ["CATALOG", "dedupe", "lint_module"]


def dedupe(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    """Sort by location and drop exact duplicates (shared rules and imported
    modules are seen once per consumer)."""
    seen: set[tuple] = set()
    unique = []
    for diag in sorted(diagnostics, key=lambda d: (d.file, d.line, d.col, d.code, d.message)):
        key = (diag.file, diag.line, diag.col, diag.code, diag.message)
        if key not in seen:
            seen.add(key)
            unique.append(diag)
    return unique


def lint_module(module: SpecModule) -> list[Diagnostic]:
    """Parser diagnostics (P0xx) plus every lint pass (AS0xx), deduplicated."""
    diagnostics = list(module.diagnostics)
    for check in PASSES:
        diagnostics.extend(check(module))
    return dedupe(diagnostics)
