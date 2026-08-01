"""Nonconformant: computes at module level (AS001/AS002 territory)."""

import os
from pydantic import BaseModel
from agentspec import Task

INBOX = os.environ.get("INBOX", "/tmp/inbox")   # arbitrary expression: forbidden

def helper():                                   # function def: forbidden
    return 42

class Result(BaseModel):
    ok: bool

class DoThing(Task):
    """Do the thing."""
    returns: Result
    on_failure = "abort"
