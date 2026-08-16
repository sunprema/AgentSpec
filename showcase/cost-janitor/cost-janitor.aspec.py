"""cost-janitor — the weekly cloud-cost reaper. The automation nobody
trusts: it DELETES things. Which is exactly why it is specified — every
bound on its destructiveness is a declaration you can read: what kinds of
resources are even eligible (a caged filter), how many may go per run (a
schema bound), what must happen before any delete (snapshot, verified),
and how an abort unwinds (restore from those snapshots, in reverse).

Doubt de-escalates to KEEPING things. A janitor that guesses is a janitor
that deletes someone's Tuesday.

(Illustrative revision history, showing how a spec accumulates lessons:)
v1.0.0 — initial routine.
v1.1.0 — a run reaped 61 volumes in one sweep; one of them was a
quarter-end reporting disk that was 'idle' for 29 days a month. The
per-run cap became a schema bound and the age floor doubled.
v1.2.0 — a delete failed halfway and the run died with it; the batch now
skips-and-reports per item, and every delete is preceded by a verified
snapshot so the unwind is always possible.
"""

from pydantic import BaseModel, Field
from agentspec import Task, Tool, Enum, Rule, Cond

DOCTRINE = [
    Rule(
        "doubt-keeps",
        "When it is unclear whether a resource is in use, it stays — "
        "uncertainty is a reason to keep, never a reason to reap",
        why="a janitor that guesses deletes someone's quarter-end disk",
        severity="must",
        since="v1.1.0",
    ),
    Rule(
        "reversible-or-nothing",
        "No resource is deleted without a verified snapshot to restore it from",
        why="the undo below is only honest if the snapshot exists first",
        severity="must",
        since="v1.2.0",
    ),
    Rule(
        "no-undeclared-software",
        "Never install, fetch, or enable software that no task declares",
        why="a janitor with a growing toolbox is a growing incident",
        severity="must",
    ),
]


class Resource(BaseModel):
    id: str
    kind: Enum["unattached-volume", "orphan-snapshot", "detached-ip"]
    age_days: int = Field(ge=0)
    monthly_usd: int = Field(ge=0)
    tags: list[str]


class IdleScan(BaseModel):
    ok: bool
    found: bool
    count: int = Field(ge=0)
    resources: list[Resource]


class ReapPlan(BaseModel):
    proceed: bool
    selected: list[Resource] = Field(max_length=5)
    deferred: int = Field(ge=0)
    rationale: str = Field(max_length=300)


class Reaped(BaseModel):
    reaped: bool
    resource_id: str
    snapshot_ref: str
    saved_usd: int = Field(ge=0)


class Report(BaseModel):
    sent: bool


class RunReport(BaseModel):
    outcome: Enum["reaped", "nothing_to_reap", "scan_failed", "uncertain"]
    reaped_count: int = Field(ge=0, le=5)
    saved_usd: int = Field(ge=0)
    summary: str = Field(max_length=400)
    operator_action: str = Field(max_length=200)


class ScanResources(Task):
    """Inventory idle resources, read-only. An accurate list with ages,
    costs, and tags — no judgment about what deserves to live."""

    returns: IdleScan

    tools = [
        Tool("cloud", ops=["volume list", "snapshot list", "address list"]),
    ]
    constraints = DOCTRINE + [
        Rule(
            "tags-travel",
            "Every resource is recorded with ALL of its tags",
            why="the keep-tag check downstream is only as good as this list",
            severity="must",
        ),
    ]
    on_uncertain = {"ok": True, "found": False, "count": 0, "resources": []}
    on_failure = {"ok": False, "found": False, "count": 0, "resources": []}


class SelectReaps(Task):
    """Choose what actually goes this week. This is the routine's judgment
    call — pinned by evals, capped by the schema."""

    resources: list[Resource]
    returns: ReapPlan

    constraints = DOCTRINE + [
        Rule(
            "cap-five",
            "Select at most five resources per run; defer the rest and say "
            "how many wait",
            why="the v1.1.0 sweep reaped 61 at once; a bounded batch keeps "
            "any mistake small and the report reviewable",
            severity="must",
            since="v1.1.0",
        ),
        Rule(
            "sixty-day-floor",
            "Only resources idle for 60 days or more are eligible",
            why="'idle for 29 days a month' described a quarter-end reporting disk",
            severity="must",
            since="v1.1.0",
        ),
        Rule(
            "keep-tags-are-sacred",
            "A resource carrying a 'keep' or 'prod' tag is never selected, "
            "whatever its age or cost",
            why="a tag is a human saying 'I know about this one'",
            severity="must",
        ),
        Rule(
            "cheapest-truth",
            "rationale states the total monthly saving and what was "
            "deferred — real numbers, no estimates presented as facts",
            why="the report is how humans audit the janitor",
        ),
    ]
    on_uncertain = {
        "proceed": False,
        "selected": [],
        "deferred": 0,
        "rationale": "could not establish idleness confidently; keeping everything",
    }
    on_failure = {
        "proceed": False,
        "selected": [],
        "deferred": 0,
        "rationale": "selection failed; nothing will be deleted",
    }


class ReapResource(Task):
    """Reap ONE resource: snapshot it, VERIFY the snapshot is restorable,
    then — and only then — delete. The ordering is what keeps the undo
    below honest at every moment of the run."""

    resource: Resource
    returns: Reaped

    tools = [
        Tool(
            "cloud",
            ops=["snapshot create", "snapshot verify", "resource delete"],
            exclusive=True,
        ),
    ]
    constraints = DOCTRINE + [
        Rule(
            "snapshot-verify-delete",
            "Strictly: snapshot, verify the snapshot readable, delete. A "
            "failed verification means the resource stays and the item is "
            "reported",
            why="an unverified snapshot is a hope, not an undo",
            severity="must",
            since="v1.2.0",
        ),
        Rule(
            "recheck-tags-at-the-brink",
            "Re-read the resource's tags immediately before deleting; a "
            "keep/prod tag added since the scan aborts this item",
            why="a human may have claimed the resource while the run was underway",
            severity="must",
        ),
    ]
    undo = "Restore the resource from snapshot_ref (the snapshot outlives the run)"
    on_item_failure = "skip_and_report"
    on_failure = "abort"


class SendReport(Task):
    """One report to the operator: what was reaped, what was saved, what
    was deferred, what failed — every run, including empty ones."""

    outcome: str
    reaps: list[Reaped] | None
    scan: IdleScan | None
    returns: Report

    tools = [Tool("push-notification")]
    constraints = DOCTRINE + [
        Rule(
            "empty-runs-report-too",
            "A run that reaped nothing still reports — silence is how "
            "janitors get forgotten until the bill arrives",
            why="the weekly cadence is the audit trail",
            severity="must",
        ),
    ]
    on_failure = {"sent": False}


class JanitorRun(Task):
    """Weekly: scan idle resources, select a bounded batch, snapshot-verify-
    delete each, report. Doubt keeps; aborts restore."""

    returns: RunReport

    scan = ScanResources()
    plan = SelectReaps(resources=scan.resources) if scan.found else None
    reaps = (
        [
            ReapResource(resource=r)
            for r in plan.selected
            if r.kind in ["unattached-volume", "orphan-snapshot", "detached-ip"]
        ]
        if plan.proceed
        else None
    )
    route = Cond(
        (not scan.ok, {"outcome": "scan_failed"}),
        (not scan.found, {"outcome": "nothing_to_reap"}),
        (not plan.proceed, {"outcome": "nothing_to_reap"}),
        (True, {"outcome": "reaped"}),
    )
    report = SendReport(outcome=route.outcome, reaps=reaps, scan=scan)

    constraints = DOCTRINE + [
        Rule(
            "the-cage-is-the-license",
            "Only the resource kinds named in the reaps filter are ever "
            "deletable; anything else the scan surfaces is report-only",
            why="the eligible set must be visible in the file, not decided at runtime",
            severity="must",
        ),
        Rule(
            "reduce-from-route",
            "Copy route.outcome verbatim into RunReport; reaped_count and "
            "saved_usd are sums over the successful reaps; then author "
            "summary and operator_action",
            why="routing is declared; arithmetic is arithmetic; only the "
            "prose is judgment",
            severity="must",
        ),
        Rule(
            "report-on-every-path",
            "SendReport is the last action on every terminal path, "
            "including after an abort's unwind",
            why="the operator hears about every run or the janitor is dead already",
            severity="must",
        ),
    ]
    on_uncertain = {
        "outcome": "uncertain",
        "reaped_count": 0,
        "saved_usd": 0,
        "summary": "Could not proceed confidently; nothing was deleted.",
        "operator_action": "Review the scan output and re-run when resolved.",
    }
    on_failure = "abort"
    meta = {"version": "1.2.0", "dispatch": "cron weekly"}
