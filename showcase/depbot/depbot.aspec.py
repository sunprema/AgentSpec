"""depbot — nightly dependency updater. Runs unattended in a repo checkout:
picks ONE outdated dependency, updates it on a branch, runs the tests, and
opens a draft PR. It can propose; it can never merge — that is the point,
and it is visible in the tool declarations below.

(Illustrative revision history, showing how a spec accumulates lessons:)
v1.0.0 — initial routine.
v1.1.0 — the run of 2026-07-12 updated 14 dependencies in one PR; the diff
was unreviewable and shipped a breaking minor. One update per run, always.
v1.2.0 — a flaky test failed a clean update; the branch was left behind and
confused the next run. Tests get one declared retry; a failed run now
cleans its own branch.
v1.3.0 — routing moved into the `route` derivation (AgentSpec 2.2); the
notify step consumes route.outcome and fires on every terminal path.
"""

from pydantic import BaseModel, Field
from agentspec import Task, Tool, Enum, Retry, Rule

DOCTRINE = [
    Rule(
        "one-update-per-run",
        "Update exactly ONE dependency per run, even when many are outdated",
        why="the 2026-07-12 run bundled 14 bumps into one unreviewable PR; "
        "one small diff per night keeps review honest and blast radius tiny",
        severity="must",
        since="v1.1.0",
    ),
    Rule(
        "no-undeclared-software",
        "Never install, fetch, or enable software that no task declares",
        why="a dependency bot that widens its own toolbox is the incident",
        severity="must",
    ),
    Rule(
        "conservative-when-unsure",
        "If anything is ambiguous, choose the conservative branch and say so "
        "in the run summary",
        why="there is no human on the other end of a nightly run",
    ),
]


class Candidate(BaseModel):
    name: str
    current: str
    target: str
    kind: Enum["patch", "minor", "major"]


class DepScan(BaseModel):
    ok: bool
    found: bool
    count: int = Field(ge=0)
    candidates: list[Candidate]


class Selection(BaseModel):
    selected: bool
    name: str
    target: str
    kind: Enum["patch", "minor", "major"]
    rationale: str = Field(max_length=200)


class Applied(BaseModel):
    applied: bool
    branch: str
    lockfile_changed: bool


class TestRun(BaseModel):
    passed: bool
    failures: int = Field(ge=0)
    log_excerpt: str = Field(max_length=500)


class PrResult(BaseModel):
    opened: bool
    pr_url: str


class Cleanup(BaseModel):
    cleaned: bool


class Note(BaseModel):
    sent: bool


class RunReport(BaseModel):
    outcome: Enum[
        "pr_opened",
        "no_updates",
        "tests_failed",
        "update_failed",
        "scan_failed",
        "uncertain",
    ]
    stopped_at: Enum["scan", "select", "apply", "test", "publish", "complete"]
    summary: str = Field(max_length=400)
    operator_action: str = Field(max_length=200)


class ScanDeps(Task):
    """List outdated dependencies from the manifest, mechanically — no
    judgment here, just an accurate inventory."""

    returns: DepScan

    tools = [Tool("bash", ops=["npm outdated --json", "cat package.json"])]
    constraints = DOCTRINE + [
        Rule(
            "report-what-is-there",
            "Record every outdated dependency with its current and target "
            "versions and bump kind; do not filter here",
            why="selection is a separate, evaluable judgment — the scan "
            "must not pre-empt it",
        ),
    ]
    on_uncertain = {"ok": True, "found": False, "count": 0, "candidates": []}
    on_failure = {"ok": False, "found": False, "count": 0, "candidates": []}


class SelectUpdate(Task):
    """Pick the single safest update from the candidates. This is the
    routine's one judgment call — it is pinned by evals."""

    candidates: list[Candidate]
    returns: Selection

    constraints = DOCTRINE + [
        Rule(
            "safest-first",
            "Prefer patch over minor over major; among equals, prefer the "
            "most widely depended-upon package",
            why="the smallest semantic step has the smallest review cost",
            severity="must",
        ),
        Rule(
            "majors-only-alone",
            "Select a major bump ONLY when no patch or minor exists, and say "
            "so in the rationale",
            why="a major is a migration, not an update; it deserves the "
            "night's whole attention",
            severity="must",
        ),
        Rule(
            "empty-is-clean",
            "No candidates means selected is false — that is a finished run, "
            "not a failure",
            why="an up-to-date repo is the goal state",
        ),
    ]
    on_uncertain = {
        "selected": False,
        "name": "",
        "target": "",
        "kind": "patch",
        "rationale": "could not choose safely; skipping tonight",
    }
    on_failure = {
        "selected": False,
        "name": "",
        "target": "",
        "kind": "patch",
        "rationale": "selection failed",
    }


class ApplyUpdate(Task):
    """Create the update branch and apply the single bump."""

    name: str
    target: str
    returns: Applied

    tools = [
        Tool("git", ops=["checkout -b", "add", "commit"], exclusive=True),
        Tool("bash", ops=["npm install <name>@<target>", "npm ci"]),
    ]
    constraints = DOCTRINE + [
        Rule(
            "branch-name-convention",
            "Branch name: depbot/<name>-<target>",
            why="predictable names are what cleanup and dedupe key on",
            severity="must",
        ),
        Rule(
            "lockfile-tells-the-truth",
            "Commit manifest and lockfile together, nothing else",
            why="a mixed commit hides what the update actually changed",
            severity="must",
        ),
    ]
    undo = "Delete the depbot/* branch and restore the pristine lockfile"
    on_failure = "abort"


class RunTests(Task):
    """Run the project's test suite on the update branch."""

    branch: str
    returns: TestRun

    tools = [Tool("bash", ops=["npm test"])]
    constraints = DOCTRINE + [
        Rule(
            "tests-decide",
            "passed reflects the suite's exit status, never a judgment about "
            "whether failures 'look related'",
            why="the whole authority of the PR rests on this bit being honest",
            severity="must",
        ),
    ]
    on_failure = Retry(
        max=2,
        backoff_s=30,
        then={
            "passed": False,
            "failures": 0,
            "log_excerpt": "test harness failed to run",
        },
    )


class OpenPr(Task):
    """Open the draft PR. Proposal is the END of this routine's authority:
    the gh surface below has no merge op, and never will."""

    name: str
    target: str
    branch: str
    log_excerpt: str
    returns: PrResult

    tools = [
        Tool("git", ops=["push"], exclusive=True),
        Tool("gh", ops=["pr create"]),
    ]
    constraints = DOCTRINE + [
        Rule(
            "never-merge",
            "Never merge, approve, or enable auto-merge on any PR — this "
            "routine's authority ends at the proposal",
            why="merge is the human's act; a bot that can merge its own "
            "proposals is a supply-chain incident waiting for a flaky test",
            severity="must",
        ),
        Rule(
            "draft-with-evidence",
            "Open as a draft; the body carries the version delta and the "
            "test log excerpt",
            why="the reviewer should see the evidence without leaving the PR",
            severity="must",
        ),
    ]
    undo = "Close the PR and delete the depbot/* branch"
    on_failure = "abort"


class CleanupBranch(Task):
    """Remove the update branch after a failed test run, so tomorrow's run
    starts clean."""

    branch: str
    returns: Cleanup

    tools = [Tool("git", ops=["branch -D"], exclusive=True)]
    constraints = DOCTRINE + [
        Rule(
            "leave-no-residue",
            "A failed update leaves no branch behind",
            why="the 2026-07-19 leftover branch made the next run misread "
            "the repo state",
            severity="must",
            since="v1.2.0",
        ),
    ]
    undo = "Restore the deleted branch from the reflog (git branch <name> <sha>)"
    on_failure = {"cleaned": False}


class Notify(Task):
    """One line to the operator with the night's outcome and the PR link
    when there is one."""

    outcome: str
    pr: PrResult | None
    tests: TestRun | None
    returns: Note

    tools = [Tool("push-notification")]
    constraints = DOCTRINE + [
        Rule(
            "lead-with-the-outcome",
            "First words: the outcome; then the PR link or the failure excerpt",
            why="the first sentence is the phone banner",
        ),
    ]
    on_failure = {"sent": False}


class DepbotRun(Task):
    """Nightly: scan, pick one update, branch, test, propose. Unattended;
    proposes but never merges."""

    returns: RunReport

    scan = ScanDeps()
    pick = SelectUpdate(candidates=scan.candidates) if scan.found else None
    apply = ApplyUpdate(name=pick.name, target=pick.target) if pick.selected else None
    tests = RunTests(branch=apply.branch) if apply.applied else None
    pr = (
        OpenPr(
            name=pick.name,
            target=pick.target,
            branch=apply.branch,
            log_excerpt=tests.log_excerpt,
        )
        if tests.passed
        else None
    )
    cleanup = CleanupBranch(branch=apply.branch) if not tests.passed else None
    route = {
        not scan.ok: {"outcome": "scan_failed", "stopped_at": "scan"},
        not scan.found: {"outcome": "no_updates", "stopped_at": "complete"},
        not pick.selected: {"outcome": "no_updates", "stopped_at": "select"},
        not apply.applied: {"outcome": "update_failed", "stopped_at": "apply"},
        not tests.passed: {"outcome": "tests_failed", "stopped_at": "test"},
        not pr.opened: {"outcome": "update_failed", "stopped_at": "publish"},
        True: {"outcome": "pr_opened", "stopped_at": "complete"},
    }
    notify = Notify(outcome=route.outcome, pr=pr, tests=tests)

    constraints = DOCTRINE + [
        Rule(
            "gates-are-clean-stops",
            "Every step is gated on the one before it reporting success; a "
            "false gate is a clean stop, never an error",
            why="an up-to-date repo and a failed scan must end differently "
            "in the report, but neither is a crash",
            severity="must",
        ),
        Rule(
            "reduce-from-route",
            "Copy route.outcome and route.stopped_at verbatim into "
            "RunReport, then author summary and operator_action",
            why="routing is declared and mechanical; only the prose is judgment",
            severity="must",
        ),
        Rule(
            "notify-on-every-path",
            "Notify is the last action on every terminal path, including "
            "after an abort's unwind",
            why="a bot that fails silently gets un-installed the hard way",
            severity="must",
        ),
    ]
    on_uncertain = {
        "outcome": "uncertain",
        "stopped_at": "scan",
        "summary": "Genuinely could not decide; nothing was changed.",
        "operator_action": "Inspect the run transcript before the next nightly.",
    }
    on_failure = "abort"
    meta = {"version": "1.3.0", "dispatch": "cron nightly"}
