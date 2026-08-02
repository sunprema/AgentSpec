# AgentSpec Visual Tooling — Idea Outline

Captures the tooling ideas from the 2026-08-01 exploration session, inspired
by the Prompt Atlas skill (`~/.claude/skills/atlas`): a zero-dependency
localhost server + single-file UI + MCP endpoint, with a pin/note/edit loop
between a human in the browser and Claude in the terminal.

**Status: all items complete (2026-08-02).** This file is now the record
of what was built and why.

## The core insight

Atlas must ask a model to *heuristically* decompose a prompt into sections.
AgentSpec already has a deterministic parser producing a typed **SpecModel**
(schemas, rules + severities + whys, tool surfaces, failure declarations,
pipeline binds) plus derived artifacts (`plan` waves and gate-skip analysis,
`lint` ASxxx findings). An atlas-style UI for AgentSpec can therefore be
driven by real semantics with **no model in the loop for reads** — a file
watcher re-parses on save; refresh is mechanical.

The Mermaid graph's incompleteness is structural: the pipeline is the
smallest part of a spec. In BookBank, most of the file's meaning lives in
rules, tools, failure semantics, and the reduction mapping — all invisible
in a flowchart.

## Ground rules (inherited from toolchain.md)

- Static only for all read paths — SpecModel in, never execute a spec.
- Zero/minimal dependencies; server is stdlib, UI is one HTML file.
- Edits always route through `aspec fmt` so files stay canonical.
- Safety model borrowed from atlas: localhost only, Allow-edits toggle that
  never persists, verbatim single-occurrence edits, stale tracking.

---

## 1. `aspec studio` — live spec workbench (flagship)

Atlas's shape (stdlib server + one HTML + `/mcp`), SpecModel-driven.

- Pipeline canvas with toggleable semantic layers:
  - data flow — hover an edge → which fields flow, with types
  - gates — hover → what a false gate transitively skips (from `plan`)
  - failure — abort/retry/escalate, undo badges, the unwind path
  - tools — capability surface per task; strict/exclusive marked
  - waves — `aspec plan` generations as swimlanes, concurrency visible
- Task inspector on click: docstring, input/output contracts, rules as
  severity chips with whys, tools, failure declarations.
- Lint overlay: ASxxx findings as node badges + issues panel.
- Interaction loop kept from atlas: pins + notes (human → Claude), MCP tools
  (Claude → file), edits gated behind Allow-edits and applied via fmt.

**Status: S0–S3 done.** Phased tasks; the pin/MCP interaction
loop is deliberately last — visualization first, collaboration second.

Phase S0 — server + payload (no UI):

- [x] Payload builder (`src/agentspec/studio/payload.py`): pure function
      SpecModel → one JSON-able dict — schemas (fields, types, bounds),
      tasks (docstring, inputs, returns, tools + surfaces, rules, failure
      declarations), the root orchestrator's plan (steps, waves,
      gate_skips), derived data-flow edges labeled with the fields they
      carry, per-step simulator needs (kwarg roots + input optionality),
      lint diagnostics
- [x] Server (`src/agentspec/studio/server.py`): stdlib HTTP on 127.0.0.1 —
      `GET /` serves the packaged `ui.html`, `GET /api/spec` serves the
      payload; mtime watcher re-parses on change and bumps `version`;
      parse errors reported in the payload, last good graph kept
- [x] CLI: `aspec studio <spec> [--port] [--no-open]`; opens the browser
- [x] Tests: payload over the BookBank fixture (waves, edges, simulator
      needs, lint carried through); server smoke test on an ephemeral port

Phase S1 — canvas + inspector (the UI):

- [x] Single-file `ui.html` (no external deps): wave-layered SVG canvas —
      nodes are binds, solid data edges, dashed gate edges
- [x] Layer toggles: data flow, gates, failure (abort/retry/undo badges +
      unwind), tools (capability chips), waves (swimlane bands)
- [x] Edge hover: the fields that flow, with types; gate hover: what a
      false gate transitively skips (from gate_skips)
- [x] Task inspector panel: docstring, inputs/returns with resolved schema
      fields, rules as severity chips with whys, tools, failure decls
- [x] Lint issues panel + per-node badges; auto-refresh polling `version`

Phase S2 — gate simulator:

- [x] Gate toggle board (all true by default); client-side propagation in
      wave order: false gate skips its step, skips propagate through
      non-optional inputs only (spec §7 semantics, same as `aspec plan`)
- [x] Skipped nodes greyed on the canvas; run/skip counts in a banner;
      reset button

Phase S3 — done (2026-08-02):

- [x] Pins + notes on steps (UI + `/api/pin`, `/api/note`); session-scoped
      (studio restarts are cheap; the graph itself is derived, not state)
- [x] `/mcp` endpoint: `get_spec`, `get_pinned`, `highlight_node` — so a
      Claude session can read what the human marked and flash a node
- [x] Edits deliberately NOT mediated by the server (unlike atlas): the
      deterministic parser + file watcher make direct file edits safe —
      Claude edits the spec, studio re-parses on save

## 2. Gate simulator — static "what-if" reachability playground

The signature feature, inside studio. Flip any boolean or bounded field
(`plugin.usable = false`, `build.validator_errors = 3`) and watch the run
path light up: which steps skip, which reduction row fires, whether the
alerting step notifies or stays silent. Purely derived from binds, gates,
and the declared reduction mapping — findings like "unreachable outcome" or
"field no condition ever reads" become discoverable by clicking.

## 3. Run / eval trace overlay

- Replay an `aspec run` result on the canvas: completed / gate-skipped /
  aborted coloring, undo arrows in reverse completion order, tool
  substitutions flagged. `RunResult` already records everything needed.
- Same overlay for `aspec eval`: each case colors the path it exercised →
  **branch coverage for specs** — which gates, failure branches, and
  reduction rows no eval ever touches.

**Status: done (2026-08-02).** Tasks:

- [x] `aspec studio <spec> --trace result.json` (the `aspec run --json`
      output): step statuses color the canvas — ok / skipped /
      declared_fallback / failed / unwound; run status + mode in the
      header; the trace file is watched like the spec
- [x] Eval-path coverage deferred: evals are per-task fixtures today and
      exercise no pipeline path; revisit when evals drive pipelines

## 4. `aspec diff` — semantic spec review

Diff two SpecModels, not two texts. "Tool ops widened", "must-rule removed",
"gate condition changed" — capability widening and failure-semantic changes
flagged loudly. The `terraform plan` of spec review; small to build, ideal
as a CI comment on spec PRs. Shippable first, independent of studio.

**Status: done (2026-08-01).** Tasks:

- [x] Change model (`src/agentspec/diff/model.py`): one typed `Change` with
      a stable `code`, category (schema/task/rule/tool/failure/pipeline/...),
      kind (added/removed/changed), and a **direction** classification —
      `widens` (more capability or less safety: new tools/ops, must-rule
      removed or weakened, gate/undo/on_uncertain removed, strict dropped),
      `narrows` (the reverse), `reshapes` (contract or wiring changed:
      schema fields, inputs/returns, bind rewires, on_failure kind),
      `neutral` (docstrings, whys, meta)
- [x] Compare engine (`compare.py`): module level (tasks/schemas added or
      removed, root task changed) → per schema (fields; type, optionality,
      bounds loosened vs tightened, enum values) → per task (docstring,
      inputs, returns, tools incl. ops/scripts/paths/strict/exclusive,
      rules matched by text with severity transitions, undo, on_uncertain,
      on_failure incl. Retry/Escalate params, pipeline binds: steps, gates,
      gate conditions, kwargs wiring, fan-out and filters, meta)
- [x] Renderer (`render.py`): text output grouped by object, widening
      first and marked loudly; `--json` (list of `Change` dumps) for CI
- [x] CLI: `aspec diff OLD NEW`, diff(1) exit convention (0 same,
      1 differences, 2 parse/usage error); parse errors in either file
      reported like other subcommands
- [x] Tests (`tests/test_diff.py`): identical file → no changes; each
      direction class exercised via edited variants of `minimal_good`;
      BookBank self-diff clean; CLI exit codes
- [x] Docs: subcommand entry in toolchain.md decision log + README mention

## 5. Reduction-table lift

Parse `condition -> (outcome, stage)` prose mappings into a real decision
table: grid rendering, row-reachability check, consistency check between the
reducer and derived-value consumers. Doubles as lint. If real specs keep
needing it, this becomes the evidence for structured mapping *syntax* (per
the spec's two-failure rule for new features).

**Status: done (2026-08-02).** Tasks:

- [x] Extractor (`src/agentspec/reduction.py`): detect the mapping pattern
      inside an orchestrator's rules (`cond -> ('outcome', 'stage')`
      clauses + `otherwise`), producing a typed ReductionTable; identify
      the outcome enum field it targets
- [x] Lint: AS041 (warning) row value outside the outcome enum; AS042
      (warning) enum value neither produced by the mapping nor by
      on_uncertain — the studio 'unreachable uncertain' finding, automated
- [x] Studio: the table rendered as a grid in the orchestrator's inspector
- [x] Tests: BookBank extraction (7 rows + otherwise), both lint rules

## 6. Doc generation as static export

The studio view exported as one self-contained HTML file — no server,
shareable, checked into `docs/`. Subsumes the "documentation generation"
stretch item in toolchain.md once studio exists.

**Status: done (2026-08-02).** Tasks:

- [x] `aspec studio <spec> --export out.html`: payload embedded as
      `window.EMBEDDED_SPEC`, polling disabled, simulator still works —
      one file, no server
- [x] Tests: file written, payload embedded, marker present

## 7. `aspec lsp` — language server

Promoted from toolchain.md "Later / stretch" (2026-08-01). Generic Python
LSPs actively mislead on ghost Python — a domain server turns the existing
SpecModel + lint + SourceLocs into in-editor feedback. Decisions: stdlib
only (no pygls) — JSON-RPC over stdio is small if sync is full-document,
which is fine for spec-sized files; positions are published best-effort in
UTF-16 code units (identical for ASCII specs); Neovim/generic clients first,
VS Code shim deferred to L2.

**Status: L0–L2 done (2026-08-01).**

Phase L0 — stdio server + diagnostics:

- [x] JSON-RPC framing (`src/agentspec/lsp/protocol.py`): Content-Length
      framing over injectable binary streams, so tests run on BytesIO
- [x] Server (`server.py`): initialize/initialized/shutdown/exit lifecycle,
      didOpen / didChange (full sync) / didSave / didClose;
      publishDiagnostics from parser P0xx + lint AS0xx (1-based lines →
      0-based LSP positions; error→1, warning→2); diagnostics for imported
      sibling files never attach to the open document; didClose clears
- [x] CLI: `aspec lsp` on stdio — nothing but protocol on stdout, logs to
      stderr
- [x] Tests: framing round-trip; pure diagnostics derivation (clean spec →
      none, parse error → P0xx, rule-hygiene → AS0xx warning); a scripted
      end-to-end session over byte streams; CLI wiring

Phase L1 — done:

- [x] Position → construct resolution (`features.py`): the dotted chain
      under the cursor resolves contextually — class names anywhere; bind
      vars, result fields, and task inputs inside the enclosing task; rule
      tuples by their first line; `enclosing_class` from SourceLoc starts
- [x] Hover (markdown): task docstring + contract summary, schema field
      with bounds and its producing task, bind with gate/fan-out, rule
      with severity + why + source constant, rule-list constants
- [x] Go-to-definition: task/schema classes, bind vars, result fields
      (jumps into the schema), constants; sibling-file locs resolve to
      their own file URIs
- [x] Document symbols: hierarchical outline — constants (rule counts),
      schemas with field children, tasks with bind children
- [x] Tests: chain extraction, every hover/definition target on
      `minimal_good` (positions found by text search, so fixture edits
      don't break them), outline shape, subprocess smoke on BookBank

Phase L2 — done:

- [x] Completions: result-schema fields after `var.` (typed, with their
      producing task), the closed `env.` namespace, enum values inside
      `on_uncertain`/`on_failure` literal dicts and `== "` / `in ["`
      comparisons; the server keeps the last good SpecModel so completion
      answers on mid-keystroke text that no longer parses
- [x] Rule severity hints: completion offers `must`/`should`/`may` with
      their §5 semantics when the cursor is in the third string of a rule
      tuple — a scope-aware lex from the top of the document (a line regex
      cannot tell `("text", "why", "` apart from `ops=["a", "b", "`, and
      docstrings need real triple-quote handling); hover on a severity
      string explains what it means
- [x] Code action: AS030 quickfix — wrap a bare must-rule string into
      `("text", "TODO: state the why")`; single-line strings only, the
      edit is a precise TextEdit on the diagnostic's own literal
- [x] VS Code shim (`editors/vscode/`): package.json + extension.js
      launching `aspec lsp` for `**/*.aspec.py`, server command
      configurable; build via vsce, other editors need no shim
- [x] Tests: every completion context, the quickfix edit range, a full
      server session completing on unparseable text, severity positives
      (single- and multi-line rules) and negatives (ops lists, why
      position)

---

## Suggested order

1. `aspec diff` (4) — independent quick win, highest review value.
2. Studio (1) with the gate simulator (2) as its signature feature.
3. Trace/eval overlay (3) and static export (6) as layers on the canvas.
4. Reduction-table lift (5) once the table has a place to render.
