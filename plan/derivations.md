# Spec 2.2 — Derivation binds (cond-dict mappings)

Decided 2026-08-02. The reduction mapping's prose form has two recorded
failures (v2.0.4 derived-value duplication; v2.1.1 AS042 unreachable
`mark_started`), earning a construct under the two-failure rule.

**Decision** (user): Elixir-`cond`-shaped dict syntax with named dict rows.
The file is never executed, so expression-keyed dicts are legal surface —
valid Python for `ast`/ruff/editors, with declared (not inherited)
semantics: rows match **first-match-wins, top to bottom**; `True:` is the
catch-all.

```python
route = {
    not workspace.resolved: {"outcome": "workspace_failed", "stopped_at": "resolve_workspace"},
    not plugin.usable: {"outcome": "plugin_failed", "stopped_at": "verify_plugin"},
    ...
    build.validator_errors > 0: {"outcome": "published_with_errors",
                                 "stopped_at": "complete",
                                 "validator_errors": build.validator_errors},
    True: {"outcome": "published_draft", "stopped_at": "complete"},
}
alert = PushAlert(..., outcome=route.outcome)
```

Semantics:

- A **derivation bind** is an orchestrator class-body assignment whose value
  is a dict with non-string-constant keys. Conditions use the same caged
  grammar as gates/filters: a boolean path, `not path`, comparisons
  (`== != > >= < <= in [literals]`), joined by `and` only.
- Row outputs are dicts of `field: literal-or-path`. All rows must declare
  the same key set.
- A derivation **always yields a value** — the catch-all guarantees it.
  Skips do NOT propagate into or through a derivation: an atom over a
  skipped step's field evaluates false (conservative), a copied path from a
  skipped step yields null. This is what lets PushAlert consume
  `route.outcome` and still run on every terminal path.
- Consumers reference derivation fields like any step result. The reducer
  receives the derivation's value with the other collected results and must
  copy it verbatim — the routing decision is mechanical, only prose fields
  (summary, operator_action) remain model-authored.

**Status: complete (2026-08-02).** 244 tests green.

Phases:

D0 — parser + model:

- [x] `DerivationBind` / `DerivationRow` / `DerivationAtom` models;
      `TaskDef.derivations`; P015 diagnostics for malformed conditions,
      non-dict rows, non-literal/path values
- [x] Recognized only in class bodies alongside task binds; a dict with all
      string-constant keys stays an unknown attr (AS003), not a derivation

D1 — lint:

- [x] AS043 (error): derivation without a `True:` catch-all row
- [x] AS044 (error): rows with inconsistent key sets
- [x] Condition/type flow: atom paths must resolve to prior-step fields
      (existing context machinery); AS041/AS042 re-target derivations —
      enum membership per row value, enum reachability including
      on_uncertain (the prose lift stays for narrated mappings)

D2 — execution + plan:

- [x] plan: derivations are steps (waves from their references) but never
      skip and never propagate skips
- [x] orchestrate: mechanical first-match evaluation; skipped references →
      atom false / copied value null; result flows to consumers and the
      reducer
- [x] graph: derivation node (hexagon) with data edges

D3 — surfaces:

- [x] studio: derivation rendered with the reduction grid (payload prefers
      the construct over the prose lift); simulator unaffected
- [x] LSP: hover on the derivation var shows the table; outline includes it
- [x] diff: rows matched by condition text — row added/removed/output
      changed

D4 — spec + migration:

- [x] Spec doc: §7 "Derived values" paragraph replaced by a Derivations
      section; version history 2.2
- [x] BookBank v2.2.0: `route` derivation replaces the prose table;
      PushAlert gains the `outcome` input; `derive-outcome-from-mapping`
      rule deleted; fixture sync; all dependent tests updated (waves grow
      by one)
