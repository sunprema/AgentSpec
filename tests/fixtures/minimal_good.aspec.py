"""Minimal conformant routine: one worker task, one orchestrator.

Exercises every core construct once — schema, task, rules, tools, gate,
failure declarations — while staying small enough to hand-verify.
"""

from pydantic import BaseModel, Field
from agentspec import Task, Tool, Enum, Rule

HOUSE_RULES = [
    Rule(
        "inbox-read-only",
        "Never modify anything outside the inbox directory",
        why="the routine is a reader; writes belong to other routines",
    ),
]


class Triage(BaseModel):
    actionable: bool
    category: Enum["bug", "question", "noise"]
    summary: str = Field(max_length=200)


class FileReport(BaseModel):
    filed: bool
    tracker_id: str


class TriageMessage(Task):
    """Classify one inbox message and summarize it in a sentence."""

    message_text: str
    returns: Triage

    tools = [Tool("read", paths=["<inbox>/**"])]
    constraints = HOUSE_RULES + [
        Rule(
            "mentions-are-not-intent",
            "A message that merely mentions a product name is not automatically "
            "actionable",
            why="name-dropping is the most common false positive",
        ),
    ]
    on_uncertain = {"actionable": False, "category": "noise", "summary": "unclear"}
    on_failure = {"actionable": False, "category": "noise", "summary": "triage failed"}


class FileTicket(Task):
    """File a tracker ticket for an actionable message."""

    category: str
    summary: str
    returns: FileReport

    tools = [Tool("gh", ops=["issue create"])]
    constraints = HOUSE_RULES
    undo = "Close the created ticket with a 'filed in error' comment"
    on_failure = "abort"


class InboxRoutine(Task):
    """Triage one message and file a ticket if it is actionable."""

    message_text: str
    returns: FileReport

    triage = TriageMessage(message_text=message_text)
    ticket = (
        FileTicket(category=triage.category, summary=triage.summary)
        if triage.actionable
        else None
    )

    constraints = HOUSE_RULES
    on_uncertain = {"filed": False, "tracker_id": ""}
    on_failure = "abort"
    meta = {"version": "1.0.0"}
