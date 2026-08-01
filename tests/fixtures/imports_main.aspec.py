"""Routine that composes rules imported from a sibling spec file."""

from pydantic import BaseModel
from agentspec import Task, Tool
from shared_rules import SAFETY

class Result(BaseModel):
    done: bool

class DoWork(Task):
    """Do the work, safely."""
    returns: Result

    tools = [Tool("bash", ops=["ls"])]
    constraints = SAFETY + [("Log before acting", "auditability")]
    on_uncertain = {"done": False}
    on_failure = "abort"
