# cost-janitor — the deleter you can trust, because you can read why

Weekly, unattended, it **deletes cloud resources** — the automation nobody
lets near production. The reason you could: every bound on its
destructiveness is a *declaration*, visible in the file before any run:

```python
reaps = [
    ReapResource(resource=r)
    for r in plan.selected
    if r.kind in ["unattached-volume", "orphan-snapshot", "detached-ip"]
]                                            # the cage IS the license:
                                             # nothing else is deletable

selected: list[Resource] = Field(max_length=5)   # blast radius as a
                                                 # schema bound

undo = "Restore the resource from snapshot_ref"  # abort-unwind restores,
                                                 # in reverse order
```

And the ordering that makes the undo *honest* is a rule, not a habit:
`snapshot-verify-delete` — an unverified snapshot is a hope, not an undo.

**Doubt keeps.** Where oncall-triage escalates on uncertainty, the janitor
inverts it: `SelectReaps.on_uncertain` is `proceed: false, selected: []` —
"could not establish idleness confidently; keeping everything." Doubt
de-escalates to the conservative action *for this domain*, and the domain
decides what conservative means.

## The Aha: reversibility and blast radius are statically visible

You don't have to trust the janitor's judgment to trust its limits. Read
the file: what kinds are eligible (caged filter), how many per run (Field
bound), what precedes every delete (verified snapshot), what an abort does
(restore from those snapshots). `aspec plan` shows the whole safety story
in one line:

```
reaps = ReapResource  [fan-out over plan.selected if r.kind in
        ['unattached-volume', 'orphan-snapshot', 'detached-ip'];
        serialized per item]  [gate: plan.proceed]
```

Serialized per item because the cloud tool is `exclusive`; one failed
deletion `skip_and_report`s instead of killing the batch.

## The diff demo: five ways to widen a janitor

`cost-janitor-risky.aspec.py` is v1.3.0's tempting proposal — "the cap is
slowing us down." The semantic diff catches every distinct kind of
loosening at once:

```
$ aspec diff cost-janitor.aspec.py cost-janitor-risky.aspec.py

JanitorRun:
  [widens]   step 'reaps': fan-out filter removed — every item now runs
ReapPlan:
  [widens]   ReapPlan.selected: bound max_length loosened: 5 -> 100
ReapResource:
  [widens]   must-rule 'recheck-tags-at-the-brink' removed: "Re-read the …"
  [widens]   undo removed — the saga unwind can no longer reverse this task
SelectReaps:
  [widens]   rule 'sixty-day-floor' weakened must -> should

!! 5 widening change(s) — more capability or less safety; review these first
```

The cage came off, the cap went 5 → 100, a last-second safety check
vanished, and deletes became irreversible — one screen, before it runs.
(Lint independently flags the dropped undo: AS034.)

## 60-second tour

```sh
alias aspec='uvx --from git+https://github.com/sunprema/AgentSpec aspec'

aspec lint --strict cost-janitor.aspec.py
aspec plan cost-janitor.aspec.py
aspec diff cost-janitor.aspec.py cost-janitor-risky.aspec.py
aspec studio cost-janitor.aspec.py
```

Zero-install: open `cost-janitor.html` — flip `scan.found` off in the
simulator and watch `report` still fire (`empty-runs-report-too`: silence
is how janitors get forgotten until the bill arrives).

## What the spec demonstrates

- **The comprehension cage as an allowlist** — eligibility is syntax, not
  runtime judgment (`the-cage-is-the-license`).
- **Schema bounds as blast-radius caps** — `max_length=5` on the selected
  list, `le=5` on the reported count; AS038 validates the fallbacks
  against them.
- **undo + ordering rules** — the §8 pattern: the rule that puts the
  snapshot *before* the delete is what keeps the unwind always possible.
- **`on_item_failure="skip_and_report"` + `exclusive`** — a resilient,
  serialized batch in two declarations.
- **Evals pin the judgment**: keep-tags sacred, the cap holds with
  deferrals counted, an all-protected week proceeds with nothing.

The revision history in the module docstring is illustrative — written to
show how a spec accumulates postmortem lessons as rules with `since=`.
