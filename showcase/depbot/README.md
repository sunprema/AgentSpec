# depbot — the dependency bot that can never merge

A nightly routine: pick **one** outdated dependency, update it on a branch,
run the tests, open a **draft PR**. Its authority ends at the proposal —
and that boundary is not a hope in a prompt, it is a declaration you can
lint, diff, and review:

```python
tools = [Tool("gh", ops=["pr create"])]        # no merge op. Ever.

Rule("never-merge",
     "Never merge, approve, or enable auto-merge on any PR — this "
     "routine's authority ends at the proposal",
     why="merge is the human's act; a bot that can merge its own "
         "proposals is a supply-chain incident waiting for a flaky test",
     severity="must")
```

## The Aha: a bot's permissions are a reviewable artifact

`depbot-risky.aspec.py` is the pull request you should be afraid of: it
adds `pr merge` to the tool surface, weakens `never-merge` to a `should`,
and quietly drops the PR-cleanup `undo`. A text diff shows string churn.
The semantic diff shows what actually changed:

```
$ aspec diff depbot.aspec.py depbot-risky.aspec.py

OpenPr:
  [widens]   tool 'gh' ops widened: +['pr merge', 'pr review --approve']
  [widens]   rule 'never-merge' weakened must -> should
  [widens]   undo removed — the saga unwind can no longer reverse this task

!! 3 widening change(s) — more capability or less safety; review these first
```

Your bot just asked for merge rights. Now you know — in CI, before it runs.
(Lint independently flags the dropped undo: AS034, an exclusive tool whose
effects an abort can no longer unwind.)

## 60-second tour

```sh
alias aspec='uvx --from git+https://github.com/sunprema/AgentSpec aspec'

aspec lint --strict depbot.aspec.py     # clean — fallbacks, rules, contracts all check
aspec plan depbot.aspec.py              # the derived schedule: gates, waves, skips
aspec diff depbot.aspec.py depbot-risky.aspec.py    # the Aha
aspec studio depbot.aspec.py            # interactive canvas + gate simulator
```

No install at all: open `depbot.html` — the studio view as one static file,
gate simulator included. Flip `tests.passed` off and watch the PR step skip
while `cleanup` (gated `if not tests.passed`) runs instead, and `notify`
still fires — the operator hears about every outcome by construction.

## What the spec demonstrates

- **One judgment, pinned by evals** — selection is its own task;
  `SelectUpdate.eval.toml` fixes "patch beats major", "a major alone is
  allowed but flagged", and "empty queue is a clean finish".
- **Negated gates** — `cleanup` runs `if not tests.passed`; the plan
  proves `pr` and `cleanup` are mutually exclusive, not racing.
- **A routing derivation** — `route` maps run state to the report outcome
  mechanically; `notify` consumes `route.outcome` on every terminal path.
- **Declared failure semantics** — the flaky-test lesson (`v1.2.0` in the
  docstring history) lives as `Retry(max=2, ...)` plus a branch-cleanup
  step, not as tribal knowledge.

The revision history in the module docstring is illustrative — written to
show how a spec accumulates postmortem lessons as rules with `since=`.
