"""Routine that composes rules imported from a sibling spec file."""

from pydantic import BaseModel
from agentspec import Task, Tool, Rule
from shared_rules import SAFETY


class Result(BaseModel):
    done: bool


class DoWork(Task):
    """Do the work, safely."""

    returns: Result

    tools = [Tool("bash", ops=["ls"])]
    constraints = SAFETY + [
        Rule("log-before-acting", "Log before acting", why="auditability")
    ]
    on_uncertain = {"done": False}
    on_failure = "abort"
