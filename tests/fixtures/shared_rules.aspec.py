"""Shared operational doctrine, imported by other specs in this directory."""

SAFETY = [
    ("Prefer the reversible action when two actions would both satisfy a rule",
     "unwind must stay possible at every step"),
    ("Record every substitution in the run report",
     "reviewers audit the report, not the transcript", "must"),
]
