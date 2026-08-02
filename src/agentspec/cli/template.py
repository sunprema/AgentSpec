"""The `aspec new` starter spec: one worker, one gated orchestrator, a
shared rule list, declared fallbacks — every core construct once, commented
for a first-time author. Generated files are piped through the formatter,
so the template itself only has to be semantically canonical."""

TEMPLATE = '''"""__TITLE__ — describe what this routine does and when it runs.

The module docstring is also where operational history accumulates: after
every run that surprises, add the lesson here and as a rule below (spec 12).
"""

from pydantic import BaseModel, Field
from agentspec import Task, Tool, Enum, Rule

# Rule lists are shared by composing them with `+` (the only operator).
# Every rule has a kebab-case id, the rule text, and a why.
HOUSE_RULES = [
    Rule("stay-in-scope",
         "Only use what a task declares; never widen capability to finish a step",
         why="an unattended run has no human to catch an overreach"),
]


class WorkItem(BaseModel):
    found: bool
    label: Enum["routine", "urgent"]
    summary: str = Field(max_length=200)


class WorkResult(BaseModel):
    done: bool
    note: str


class FindWork(Task):
    """Find the next work item. The docstring is the task's instruction."""

    returns: WorkItem

    tools = [Tool("bash", ops=["ls"])]
    constraints = HOUSE_RULES
    # Fallbacks are validated against the returns schema (lint AS038).
    # on_uncertain is what the model returns when it genuinely cannot decide.
    on_uncertain = {"found": False, "label": "routine", "summary": "could not decide"}
    on_failure = {"found": False, "label": "routine", "summary": "finding work failed"}


class DoWork(Task):
    """Do one work item and report what happened."""

    summary: str
    returns: WorkResult

    tools = [Tool("bash", ops=["ls"])]
    constraints = HOUSE_RULES + [
        Rule("verified-versus-inferred",
             "State in the note what was verified by command and what is inferred",
             why="evidence over inference; the report is audited, not the transcript"),
    ]
    # undo runs only during an abort's saga unwind (hover it in your editor).
    undo = "Describe how to reverse this task's completed effects"
    on_failure = "abort"


class Routine(Task):
    """Find work and do it. The orchestrator: class-body assignments ARE the
    pipeline — data flow, gates, and concurrency all derive from them."""

    freeform_context: str
    returns: WorkResult

    item = FindWork()
    # A gate: DoWork runs only when the condition is true (`if not ...` for
    # falsity). A false gate is a clean stop, never an error.
    work = DoWork(summary=item.summary) if item.found else None

    constraints = HOUSE_RULES
    on_uncertain = {"done": False, "note": "genuinely could not decide"}
    on_failure = "abort"
    meta = {"version": "0.1.0"}
'''


def render_template(name: str) -> str:
    title = name.replace("_", " ").replace("-", " ").strip().title() or "New Routine"
    return TEMPLATE.replace("__TITLE__", title)
