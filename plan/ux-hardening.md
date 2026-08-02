# UX hardening — findings from the 2026-08-02 construct assessment

Every construct was walked as a new spec author meets it; suspected gaps
were verified against the toolchain (probe specs, not guesses). Fixes in
priority order — **all complete (2026-08-02)**. The reduction-table lift stays tracked as
[visual-tooling.md](visual-tooling.md) item 5.

## 1. Lint: fallback literals validated against the contract

Verified gap: `on_uncertain = {"okk": False}` lints clean — a typo'd
fallback ships and violates the output contract during a failing
unattended run, the worst possible moment.

- [x] AS038 (error): `on_uncertain`, literal `on_failure`, and literal
      `then=` inside Retry/Escalate must validate against the task's
      returns schema — reuse eval's `build_output_model` (bounds, closed
      enums, extra fields forbidden)
- [x] Tests: wrong field name, wrong type, bound violation, non-dict
      literal, nested then-literal; clean fixtures stay clean

## 2. Lint: dispatch-time surprises caught statically

- [x] AS039 (warning): more than one *orchestrator* root — "the dispatch context
      must name one"; refined during implementation: plain multi-root files are libraries (AS033's exemption) and stay clean — the warning targets multiple orchestrator roots, and `aspec run` now lists the candidates when no unique root exists
- [x] AS040 (warning): `on_item_failure` on a task nothing fans out over
      (verified: silently meaningless today)
- [x] Tests for both

## 3. LSP: teaching hovers at the trap sites

The three semantics users cannot discover from the file itself:

- [x] Hover on an `X | None` task input notes: optional — upstream skips
      do not propagate through this input (spec §7)
- [x] Hover on a tool name inside `tools = [...]` shows its surface and
      teaches the substitution rule (capability, not binary; `strict=`
      reverses; `exclusive=` serializes) — BookBank v2.0.1 exists because
      this was misread in the field
- [x] Hover on reserved attribute names (`undo`, `on_uncertain`,
      `on_failure`, `on_item_failure`, `constraints`, `tools`) gives a
      one-line semantics summary; `undo`'s states it runs only during
      abort-unwind, never after a literal fallback
- [x] Tests per hover

## 4. Language: negated gates

Verified gap: `if not a.ok` is rejected (AS011); running a step on
falsity requires polluting the producer's schema with an inverse field.
`not` on a boolean path is deterministic, statically analyzable, and
lintable — it passes the decision checklist.

- [x] Parser: `X(...) if not path else None` → `gate_negated` on the bind
- [x] AS011 unchanged semantics (path must be a boolean field of a prior
      step); plan/graph/studio/LSP/diff render `not x.y`; orchestrate
      evaluates the negation; simulator toggles the whole condition
- [x] Spec doc §7 gates sentence + version history entry (2.1, additive)
- [x] Tests: parse, lint, plan text, orchestrate skip behavior, diff

## 5. `aspec new` — starter spec scaffold

- [x] `aspec new <name>` writes `<name>.aspec.py`: one worker task, one
      orchestrator, a rule with id/why, a gate, declared fallbacks —
      commented, lint-clean, fmt-canonical; refuses to overwrite
- [x] Tests: file created, lints clean, fmt-idempotent, overwrite refused
