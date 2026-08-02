# Rules v2 — named `Rule(...)` declarations

Decided 2026-08-01. Motivation: in real specs (BookBank) most of the file's
prose lives in rules blocks, and the tuple form fails the language's own
"understandable during code review" bar — rule text and why have identical
visual weight, the boundary between them is invisible across implicit
string concatenation, and the trailing severity string is easy to miss.
Named rules also give every rule a stable id: the scannable "rule-scape"
for readers, and the anchor for diff, lint, coverage, and run reports.

**Decision** (user, 2026-08-01): `Rule()` with a required id; hard cutover —
tuples and bare strings no longer parse; BookBank and all fixtures migrate
by hand in the same change.

## The form

```python
from agentspec import Task, Tool, Rule

UNATTENDED = [
    Rule("no-questions",
         "Never ask questions; if anything is ambiguous, choose the "
         "conservative branch and record the ambiguity in the run report",
         why="there is no human on the other end of this run"),
]

class SelectIssue(Task):
    constraints = UNATTENDED + [
        Rule("verify-freeform-claim",
             "Freeform context naming an issue wins over queue order, but "
             "ONLY after verifying by command that the named issue is OPEN "
             "and carries the 'book-request' label...",
             why="a webhook fires on issues it was never meant to aim this "
                 "routine at",
             severity="must",
             since="v2.0.3(6)"),
    ]
```

- `id` (positional): required, kebab-case, unique within a task's composed
  constraints. The reviewer-facing name of the rule.
- `text` (positional): the rule.
- `why=`: the rationale. Optional at parse; AS030 still warns on must-rules
  without one — the enforcement moves from tuple shape to lint.
- `severity=`: `"must"` (default) | `"should"` | `"may"`.
- `since=`: free-form provenance (spec revision / incident), replacing the
  `"v2.0.3(6): ..."` prefix convention inside why-prose.

**Status: complete (2026-08-02).** All phases done; 206 tests green.

## Phases

R0 — language spec:

- [x] Rewrite §5 of `docs/AgentSpec-specification.md` for `Rule(...)`;
      update the §2 import line and every tuple-rule example in the doc
- [x] Add a version-history section; bump the spec to Version 2.0
      (breaking: tuple and bare-string rules removed)

R1 — parser:

- [x] `Rule` model: add `id`, `since`; drop `bare`
- [x] Parse `Rule("id", "text", why=, severity=, since=)` calls in
      constraints lists and module rule constants; tuples and bare strings
      produce a P005 that names the migration (`use Rule("id", "text",
      why=...)`)
- [x] Validate severity values (existing set) and non-empty string id

R2 — migrate the reference specs:

- [x] `docs/BookBank_routine.aspec.py` and the fixture copy: every rule
      gets an authored id; `v2.0.x(n)` prefixes move from why into `since=`
- [x] `minimal_good`, `fanout_routine`, `shared_rules`, `imports_main`
      fixtures

R3 — toolchain:

- [x] lint: AS036 duplicate rule id in a task's composed constraints;
      AS037 id not kebab-case; AS030/AS031 unchanged
- [x] diff: rules match by id — reworded text becomes `rule-text-changed`
      (reshapes) instead of removed+added; severity transitions as before;
      why/since changes neutral
- [x] run/eval prompt: rules render as `- (severity) [id] text — why: ...`;
      dedupe of inherited shared rules by id
- [x] studio: payload carries id/since; inspector shows the id as the rule
      chip title
- [x] fmt: no special casing expected (Rule() is an expression); prove
      idempotence over migrated fixtures

R4 — LSP:

- [x] Severity completion becomes a `severity="` kwarg context; the
      scope-aware tuple lexer is deleted
- [x] Hover on a rule id string resolves the rule (severity, text, why,
      since); severity-string hover unchanged
- [x] Document symbols: rules appear as children (by id) under tasks and
      rule constants — the outline becomes the rule-scape

R5 — sweep:

- [x] Update every test that authors tuple rules; full suite green
- [x] README examples if any; toolchain.md decision log entry
