# Showcases — Aha-first sample routines

Each showcase is engineered backwards from one Aha moment: pick the
language feature that makes people sit up, find the domain where it is
viscerally wanted, package it as a 60-second experience.

Packaging per showcase (`showcase/<name>/`):

- the spec (lint `--strict` clean, fmt-canonical, exercising current
  language features)
- `README.md` — the Aha called out, a 60-second demo script, expected
  output excerpts
- an `.eval.toml` pinning the judgment task's behavior
- a studio `--export` HTML (the no-install experience)
- where the Aha is governance: a risky variant for the `aspec diff` demo
- a repo test keeping all of it honest (lint clean, diff codes stable,
  eval artifact well-formed)

## Shortlist

- [x] **depbot** (2026-08-02) — nightly dependency updater that opens
      PRs and can never merge. Aha delivered: the risky variant diffs to
      exactly three `[widens]` lines on OpenPr (ops +pr merge, never-merge
      weakened, undo dropped), pinned by tests/test_showcase.py. Bonus
      finding while building it: complementary gates (`cond` / `not cond`)
      no longer false-positive the plan's exclusive-tool race warning.
- [x] **oncall-triage** (2026-08-02) — incident first responder. Aha
      delivered: mechanical severity routing with doubt-raises-severity
      (blindness pages sev2, proven by plan + simulator), declared
      Escalate chain, and an eval pinning prompt-injection resistance.
      Language additions earned while building it: gates on derivation
      boolean fields (AS011 extended); the diff demo surfaced that
      weakening shared doctrine reports on every bound task.
- [ ] **cost-janitor** — the deleter nobody trusts. Aha: reversibility
      and blast-radius bounds are statically visible (undo,
      snapshot-first ordering, caged allowlist, abort-unwind).
- [ ] Stretch: **release-notes** (drafting power ≠ publishing power),
      **pr-shepherd** (lintable review policy), **metrics-sentinel**
      (fan-out + on_item_failure resilience).
