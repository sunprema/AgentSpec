"""Fan-out routine: scan for findings, triage each one, page for the urgent.

Exercises the pipeline constructs the other fixtures lack: fan-out over a
list result, iterating a prior fan-out, a caged filter, on_item_failure,
and a list-typed schema field.
"""

from pydantic import BaseModel, Field
from agentspec import Task, Tool, Enum

class Finding(BaseModel):
    id: str
    severity: Enum["low", "high"]

class ScanResult(BaseModel):
    found: bool
    findings: list[Finding]
    count: int = Field(ge=0)

class TriagePlan(BaseModel):
    id: str
    action: Enum["ignore", "page"]

class PageResult(BaseModel):
    paged: bool

class Digest(BaseModel):
    total: int
    paged_count: int

class Scan(Task):
    """Scan the system for findings."""
    returns: ScanResult

    tools = [Tool("bash", ops=["ls"])]
    on_uncertain = {"found": False, "findings": [], "count": 0}
    on_failure = "abort"

class TriageFinding(Task):
    """Decide what to do about one finding."""
    finding: Finding
    returns: TriagePlan

    on_item_failure = "skip_and_report"
    on_uncertain = {"id": "", "action": "ignore"}
    on_failure = {"id": "", "action": "ignore"}

class PageOncall(Task):
    """Page the on-call for one urgent plan."""
    plan_id: str
    returns: PageResult

    tools = [Tool("push-notification")]
    on_item_failure = "abort"
    on_failure = "abort"

class ScanRoutine(Task):
    """Scan, triage every finding, page for the urgent ones, and digest."""
    returns: Digest

    scan  = Scan()
    plans = [TriageFinding(finding=f) for f in scan.findings]
    pages = [PageOncall(plan_id=p.id) for p in plans if p.action in ["page"]]

    on_uncertain = {"total": 0, "paged_count": 0}
    on_failure = "abort"
    meta = {"version": "1.0.0"}
