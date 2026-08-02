"""Shared operational doctrine, imported by other specs in this directory."""

from agentspec import Rule

SAFETY = [
    Rule(
        "prefer-reversible",
        "Prefer the reversible action when two actions would both satisfy a rule",
        why="unwind must stay possible at every step",
    ),
    Rule(
        "record-substitutions",
        "Record every substitution in the run report",
        why="reviewers audit the report, not the transcript",
        severity="must",
    ),
]
