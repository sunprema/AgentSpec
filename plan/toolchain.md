# AgentSpec Static Toolchain — Implementation Plan

Tracks the reference toolchain promised in
[AgentSpec-specification.md §11](/docs/AgentSpec-specification.md) and the
tooling goals in [CLAUDE.md](/CLAUDE.md).

Ground rules for every phase:

- **Static only.** Specs are parsed with Python's `ast` module and are never
  imported or executed. No phase may `exec`/`import` a `.aspec.py` file.
- **Python tooling**, managed with `uv`. New dependencies enter via
  `uv add` and must be justified in this file next to the item that needs them.
- `docs/BookBank_routine.aspec.py` is the canonical fixture: every phase must
  produce correct output for it before its checkbox is ticked.
- Every feature lands with tests (parser / validation / regression), per the
  Testing section of CLAUDE.md.

CLI shape: a single `aspec` entry point with subcommands —
`aspec lint | plan | graph | fmt | eval | run`.

---

## Phase 0 — Project scaffolding

- [x] `uv init` — create `pyproject.toml` (package name `agentspec`, Python ≥ 3.11)
- [x] Package layout: `src/agentspec/` with subpackages `parser/`, `lint/`,
      `plan/`, `graph/`, `cli/`
- [x] `aspec` console script wired via `[project.scripts]`
- [x] Dev dependencies: `uv add --dev pytest ruff`
- [x] Runtime dependency: `uv add pydantic` (schema introspection and the
      toolchain's own internal models)
- [x] `tests/` layout with `tests/fixtures/` — copy of the BookBank routine
      plus small handwritten good/bad specs (`minimal_good`,
      `bad_module_statement`)
- [x] `ruff` configured (lint + format) for the toolchain's own code;
      `tests/fixtures/` excluded — fixtures are data under test, not code
- [x] `aspec --version` runs end to end via `uv run aspec --version`

## Phase 1 — Parser (the foundation everything else consumes)

Parse a `.aspec.py` file into a typed **SpecModel** — the single in-memory
representation all later tools consume. No tool other than the parser touches
raw AST.

- [x] Load file → `ast.parse`; collect module docstring, imports, module-level
      constants, class definitions
- [x] Classify classes: schema (`BaseModel` base) vs task (`Task` base);
      reject anything else at module level
- [x] Schema model: fields, types, optionality (`X | None`), `Field` bounds
      (`le/ge/max_length`), `Enum[...]`/`Literal[...]` closed sets
- [x] Task model: docstring, inputs (bare annotations), `returns`, reserved
      attributes (`tools`, `constraints`, `undo`, `on_uncertain`,
      `on_failure`, `on_item_failure`, `meta`)
- [x] Rule model: parse `("text", "why"[, severity])` tuples and bare strings;
      resolve module-level rule-list constants and `+` composition
- [x] Tool model: name, `ops`/`scripts`/`paths`, `strict`, `exclusive`
- [x] Failure model: `"abort"`, literal dict, `Retry(...)`, `Escalate(...)`
      with their fields (including nested `then`)
- [x] Pipeline model (orchestrator class bodies): steps, fan-outs
      (comprehensions), filters, gates (`X(...) if cond else None`), joins;
      record every referenced attribute path
- [x] Root-task resolution: the task no other task references
- [x] Cross-file imports: resolve `.aspec.py` → `.aspec.py` imports statically
      (sibling files, cycle detection, no star imports)
- [x] Structured parse errors with file/line/col (shared diagnostic type used
      by all later subcommands) — parser codes `P001`–`P013`
- [x] Parser test suite: BookBank fixture round-trips into a SpecModel with
      every construct accounted for (plus `fanout_routine` fixture for
      fan-out/filter/list-types, which BookBank does not exercise)

## Phase 2 — `aspec lint`

Each rule gets a stable ID (`ASxxx`), a test with a fixture that triggers it,
and an entry in the rule docs. `--strict` exits nonzero on warnings, for CI.

Conformance (structure) errors:

- [x] AS001 module-level statement that is not docstring/import/constant/class
      — covered by parser `P002` (and `P006` for forbidden imports)
- [x] AS002 function defs, loops, I/O, or arbitrary expressions anywhere
      — covered by parser `P002`/`P009`
- [x] AS003 unknown attribute on a non-orchestrator task
- [x] AS004 missing task docstring
- [x] AS005 anonymous `returns` (`str`, `dict`, …) instead of a named schema
- [x] AS006 filter richer than `path in [literals]` / `path == literal`
      (the comprehension cage)
- [x] AS007 undefined task reference in a pipeline bind
- [x] AS008 binding a name before it exists (the v2.0.4 `outcome=outcome` bug —
      regression fixture comes straight from that postmortem)
- [x] AS009 cycle in the derived dependency graph
- [x] AS010 `Retry`/`Escalate` without a terminating `then` (checks the whole
      nested chain)
- [x] AS011 gate condition that is not a boolean result field of a prior step

Type flow:

- [x] AS020 producing field vs consuming input mismatch on every bind
      (allows safe `Enum` → `str` widening; also checks missing/unknown
      inputs and literal-vs-closed-enum values)
- [x] AS021 fan-out result addressed as a scalar (or vice versa; also
      fanning out over a non-list)
- [x] AS022 attribute path that does not exist on the source schema
      (including the closed `env` namespace, spec §10)

Rule hygiene & safety (warnings unless noted):

- [x] AS030 must-rule without a *why*
- [x] AS031 task exceeding ~15 constraints (decompose instead)
- [x] AS032 duplicate task names (error) — covered by parser `P014`
      (duplicate definition of any class or constant)
- [x] AS033 unreachable / unused task (reachability from orchestrator roots;
      files with no orchestrator are treated as task libraries and skipped)
- [x] AS034 mutating task (exclusive tool) without `undo`
- [x] AS035 fan-out step without `on_item_failure`
- [ ] AS036 task whose declared tools cannot cover its own mandated procedure
      surface — **deferred**, see decision log (prose heuristics too noisy)
- [ ] AS037 tool declared but never plausibly needed — **deferred**, same
      rationale as AS036

CLI:

- [x] Human-readable output (`file:line:col: ASxxx message`) + `--json`
- [x] `--strict` flag; exit codes: 0 clean, 1 errors, 2 warnings-as-errors
- [x] BookBank fixture lints clean (or every finding is triaged and either
      fixed in the fixture or the rule is corrected) — triaged: 0 errors and
      exactly one true warning (AS031: GenerateBook carries 16 constraints),
      pinned in `tests/test_lint_integration.py`; the fix is a spec-author
      decision (decompose GenerateBook), not a lint bug

## Phase 3 — `aspec plan`

Derive execution order purely from the data-flow binds.

- [x] Build the dependency graph from referenced paths (steps, gates, joins)
      — `PipelineBind.referenced_roots()`, shared with lint's cycle check
- [x] Compute waves (topological generations): what runs concurrently, what
      waits, what fans out
- [x] Show gate consequences: what a false gate skips transitively — skips do
      NOT propagate through inputs declared `X | None` (that is how the plan
      proves PushAlert runs on every terminal path)
- [x] Serialize per-item for `exclusive=True` tools in fan-outs
      (`serialized` flag on the step plan, rendered as "serialized per item")
- [x] Flag false concurrency — implemented as: two steps in the same wave
      holding the same `exclusive` tool ("missing bind?" warning); ordering
      implied only by prose constraints stays out of scope with AS036
- [x] Text output (wave-by-wave) + `--json`
- [x] BookBank plan matches the hand-derived expectation (fixture assertion):
      7 waves, art ∥ notify in wave 5, alert joins all six in wave 6

## Phase 4 — `aspec graph`

- [x] Emit the pipeline as a Mermaid flowchart: steps (rectangles), fan-outs
      (`[[...]]` subroutine shape, "×N", source edge labeled
      "each of <path> [where <filter>]"), gates (dashed edges with condition
      labels), joins (multiple solid in-edges), dispatch-inputs node
- [x] Include failure edges as an optional layer (`--failures`): abort steps
      get a dashed edge into a shared saga-unwind node; Retry/Escalate/
      default and `undo declared` become node label lines
- [x] `--out file.md` and stdout modes
- [x] Rendered BookBank graph reviewed and checked into `docs/` as a
      reference artifact (`docs/BookBank_routine.graph.md`)

## Phase 5 — `aspec fmt` (formatter)

- [x] Canonical formatting for spec files: task bodies reorder to docstring →
      inputs → `returns` → pipeline binds (relative order preserved — it is
      semantic) → reserved attributes in spec §4 order; comments travel with
      the statement that follows them; layout by `ruff format --isolated`
      (now a runtime dependency), so output is independent of project config
- [x] `--check` mode for CI (exit 1 when files would change, nothing written)
- [x] Idempotence test: `fmt(fmt(x)) == fmt(x)` over all fixtures

## Phase 6 — `aspec eval` (fixture-based behavioral tests)

The first phase that talks to a model; everything before this stays fully
static. Gate spec changes like code changes.

- [x] Eval file format: TOML (`.eval.toml`, stdlib `tomllib`) — `spec`,
      optional `task` (default: root task), `[[case]]` tables with `inputs`
      and `expect`; case inputs are validated against the task's declared
      inputs before any adapter call
- [x] Schema validation of model output against declared contracts: real
      pydantic models are built from the spec's own schemas (`Field` bounds,
      closed enums, optionality, nested/list schemas, `extra="forbid"`)
- [x] Assertion language kept declarative: bare value = equality, plus a
      closed operator set `in`/`not_in`/`gte`/`lte`/`contains`, dotted paths
      for nested fields — anything richer belongs in the spec
- [x] Runner with per-case pass/fail report and `--json`; adapter errors and
      schema violations are case failures, not crashes
- [x] Provider-independent model adapter boundary: an adapter is any
      `Callable[[prompt], reply]`; the core ships only a subprocess bridge
      (`--adapter-cmd 'claude -p'` or any stdin→stdout command)
- [x] At least one eval for the BookBank routine's `SelectIssue` task —
      `docs/evals/SelectIssue.eval.toml` (3 cases); a repo test runs it with
      a canned conservative reply to prove the artifact stays well-formed

## Phase 7 — `aspec run` / `orchestrate` (guarded execution)

Last, and deliberately thin — AgentSpec must not become a runtime framework.

- [x] Guarded single-agent run: dispatch context → root task (freeform text
      flows into `freeform_context`, spec §9), outputs validated against
      declared schemas, violations fed back verbatim for bounded repair
      (`--max-repairs`, default 2)
- [x] Declared `on_failure` honored when conformance cannot be reached:
      literal → declared fallback, abort → aborted (with saga unwind in
      orchestrate mode), Retry → fresh guarded attempts then `then`,
      Escalate → recorded (this harness has no waiting channel) then `then`
- [x] `--orchestrate`: one guarded subagent per step — the harness derives
      the schedule from the binds, resolves data flow/gates/fan-out/filters
      mechanically (real `env` values, §10), each subagent receives only its
      pruned contract plus the orchestrator's inherited constraints (deduped,
      §5), `on_item_failure` honored per item, the orchestrator-as-reducer
      runs as a final guarded call, and abort unwinds completed steps via
      their declared `undo` in reverse order
- [x] Run report records tool substitutions, rule conflicts, and
      verified-vs-inferred claims: the single-agent prompt mandates a
      "## Run report" section (captured into `RunResult.report`); orchestrate
      mode records skips, dropped fan-out items, escalations, and unwind
      reports in `RunResult.notes`/`steps`

## Later / stretch (tracked, not scheduled)

- [ ] Language server (diagnostics from the linter, go-to-definition across
      binds, hover for rules/schemas)
- [ ] Documentation generation from a SpecModel (task tables, rule registry)
- [ ] Serialization: SpecModel → stable JSON for external tooling

---

## Decision log

Record here anything that changes the plan (new deps, dropped rules, semantic
questions the spec must answer). Semantic ambiguities discovered while
building the toolchain are spec bugs — fix the spec, then the tool.

- 2026-08-01 — Plan created.
- 2026-08-02 — **Language spec 2.2: derivation binds** (see
  [derivations.md](derivations.md)). The Elixir-`cond`-shaped dict —
  chosen over a `Mapping()`/`When()` DSL (string conditions) and a ternary
  chain (reads value-before-condition) after the user asked for a more
  Pythonic form; the never-executed nature of specs is what makes
  expression-keyed dicts legal surface. Decisions: (1) derivations always
  yield — skips never propagate into or through them (atoms over skipped
  steps are false, copied values null); this is what lets PushAlert consume
  `route.outcome` on every terminal path; (2) conditions reuse the
  gate/filter cage, `and`-only (P015 tells you to split `or` into rows);
  (3) `True:` catch-all mandatory and last (AS043), consistent row fields
  (AS044), AS041/AS042 retarget the construct natively; (4) orchestrate
  evaluates rows mechanically — routing left the model; (5) §7 "derived
  values" paragraph deleted; BookBank v2.2.0 replaced the prose table and
  the derive-outcome-from-mapping rule with the `route` derivation and a
  real `outcome` input on PushAlert.
- 2026-08-02 — Visual-tooling backlog completed (items 3, 5, 6, studio S3).
  (1) **Reduction-table lift**: `agentspec/reduction.py` lifts prose
  `cond -> ('outcome', 'stage')` mappings into typed tables; lint AS041/
  AS042 check row membership and enum reachability (on_uncertain counts as
  a producer). AS042's first run found a real bug in the reference spec —
  `stopped_at 'mark_started'` was unreachable; BookBank v2.1.1 adds the
  missing `NOT mark.marked` row. Two structured-syntax strikes are now on
  record for mapping syntax. (2) **Trace overlay**: `aspec studio --trace`
  colors the canvas from an `aspec run --json` result; the trace file is
  watched like the spec. Eval-path coverage deferred — evals are per-task
  and exercise no pipeline path. (3) **Static export**: `--export` embeds
  the payload into the single-file UI (simulator included, pins hidden);
  subsumes the doc-generation stretch item. (4) **Studio S3**: pins +
  notes (session-scoped) and an `/mcp` endpoint (get_spec, get_pinned,
  highlight_node); server-mediated editing deliberately dropped — the
  deterministic parser + file watcher make direct file edits safe, which
  atlas could not assume. Inspector defaults to the root orchestrator so
  the reduction table is reachable.
- 2026-08-02 — UX hardening (see [ux-hardening.md](ux-hardening.md)), from
  a construct-by-construct assessment with verified probes. (1) AS038:
  fallback literals (`on_uncertain`, literal `on_failure`, `then=`)
  validate **strictly** against the returns schema — they are
  author-written declarations, so `"yes"` is not a bool; lint now depends
  on eval's `build_output_model`. (2) AS039 fires on multiple
  *orchestrator* roots only — plain multi-root files are libraries
  (AS033's exemption); `aspec run` lists candidates when no unique root.
  (3) AS040: `on_item_failure` on a never-fanned task. (4) Teaching hovers
  put §5–§8 semantics at the trap sites (tool substitution, optional-input
  skip propagation, undo's abort-only trigger). (5) **Spec 2.1: negated
  gates** — `if not cond else None`; `PipelineBind.gate_negated` +
  `gate_condition()` render everywhere; orchestrate evaluates it; skip
  analysis unchanged (direction-agnostic). (6) `aspec new` (tenth
  subcommand) scaffolds a starter spec, piped through `format_source` at
  generation so the scaffold always meets the toolchain's own bar.
- 2026-08-02 — **Language spec 2.0: named rules** (see
  [rules-v2.md](rules-v2.md)). `Rule("id", "text", why=, severity=,
  since=)` replaces tuple and bare-string rules — hard cutover, all specs
  and fixtures migrated by hand. Ripples: parser (Rule calls only, P005
  guides migration), lint (AS036 duplicate id, AS037 kebab-case; AS030
  message now cites the id), diff (rules match by id — rewording is
  `rule-text-changed`, not removed+added), run/eval prompts render
  `[id]` and dedupe shared rules by id, studio shows ids as rule titles,
  LSP (severity= kwarg completion replaced the tuple lexer, rule-id hover
  and definition, rules in the outline), AS030 quickfix now inserts
  `why=` into the Rule call. Spec doc gained a version-history section;
  migrated specs are `aspec fmt`-canonical (one field per line).
- 2026-08-01 — `aspec lsp` phase L2 (completions, code actions, VS Code
  shim) — the language-server stretch item is now fully delivered.
  Decisions: (1) the server keeps the *last good* SpecModel per document —
  mid-keystroke text rarely parses, and completions/hover answer from the
  last good model while diagnostics always reflect the current text;
  (2) completion contexts are regex-on-line-prefix (after `var.`, after
  `env.`, inside `"field": "` literal dicts, after `== "` / `in ["`) —
  contextual and cheap, no second parser; (3) the AS030 quickfix edits only
  single-line bare-string rules, precisely at the diagnostic's literal —
  multi-line strings get no action rather than a wrong edit; (4) the
  VS Code shim lives in `editors/vscode/` outside the Python package and
  is transport-only — every feature stays in the server.
- 2026-08-01 — `aspec lsp` phase L1 (hover, go-to-definition, document
  symbols). Decisions: (1) position resolution is text-side — the dotted
  identifier chain under the cursor resolves against the SpecModel by
  context (enclosing class from SourceLoc starts), no second parse;
  (2) hover on a result field (`issue.number`) names its producing task —
  the cross-reference a text editor can't know; (3) rule hover matches the
  tuple's first line only (rules span lines; the loc is the start) —
  predictable beats clever; (4) definitions into imported sibling specs
  return that file's own URI, absolute paths only.
- 2026-08-01 — `aspec lsp` phase L0 added (ninth subcommand, from
  [visual-tooling.md](visual-tooling.md) item 7; graduates the "language
  server" stretch item). Decisions: (1) stdlib only, no pygls — JSON-RPC
  framing over injectable binary streams is ~50 lines and keeps the
  toolchain dependency-free; (2) full-document sync (`change: 1`) instead
  of incremental — spec files are small and it removes the whole position-
  patching class of bugs; (3) diagnostics republish parser P0xx + lint
  AS0xx on open/change/save, and diagnostics belonging to imported sibling
  specs are not attached to the open document; (4) positions are published
  as-is from the AST (UTF-16 vs byte offsets differ only on non-ASCII
  lines — accepted for L0).
- 2026-08-01 — `aspec studio` added (eighth subcommand, from
  [visual-tooling.md](visual-tooling.md) items 1–2): a stdlib-only localhost
  server + single-file UI — wave-layered pipeline canvas with gate/failure/
  tools/waves layers, task inspector, lint overlay, and a client-side gate
  simulator that reuses `plan`'s skip semantics (skips do not propagate
  through `X | None` inputs). Decisions: (1) the payload is one pure
  function over the SpecModel — no heuristics, no model in the loop, so a
  file save re-parses mechanically; (2) a parse with errors keeps the last
  good canvas and banners the errors instead of blanking the UI;
  (3) simulator toggles key on the gate *path*, not the gated step — two
  steps gated on `build.built` are one condition, not two; (4) TypeExpr
  gained a `render()` method, replacing per-tool copies of the same code.
- 2026-08-01 — `aspec diff` added (seventh subcommand, from
  [visual-tooling.md](visual-tooling.md) item 4): semantic comparison of two
  SpecModels, every change classified by direction — `widens` (more
  capability / less safety), `narrows`, `reshapes` (contract or wiring),
  `neutral` (prose/meta). Decisions: (1) rules match by exact text, so a
  reworded rule reports as removed + added — the honest reading, the old
  rule no longer binds; (2) a new task's tools are each reported as
  `widens`, so brand-new capability can never arrive silently inside a
  `reshapes` change; (3) exit codes follow diff(1): 0 same, 1 different,
  2 trouble; (4) old and new sides parse with separate caches so two
  checkouts of the same sibling files never resolve into each other.
- 2026-08-01 — Phase 7 complete (`aspec run` / `--orchestrate`); all six
  subcommands are now implemented. Semantics decisions: (1) `Escalate` in a
  CLI harness cannot deliver or wait — it is recorded in notes and its
  declared `then` applies immediately; a future harness with a channel can
  do better, but the spec's semantics are preserved (never ask, always
  terminate through declared behavior). (2) A step returning its declared
  literal fallback is NOT counted as "completed" for unwind purposes — only
  fully conforming steps are unwound, the conservative reading of §8.
  (3) `declared_fallback` exits 0: a declared fallback is a legitimate,
  reviewed outcome, not an error. (4) The orchestrate harness resolves all
  data flow itself (gates, filters, fan-out, env) so subagents never see the
  pipeline — each gets exactly its pruned contract plus inherited
  constraints, deduped by rule text.
- 2026-08-01 — Phase 6 complete (`aspec eval`). Format choices: TOML over
  YAML (stdlib, no new dependency) and over Python (eval files must stay
  data). The assertion operators are a deliberately closed set — richer
  logic is a sign the expectation belongs in the spec's rules, not the eval.
  The SelectIssue eval pins only judgment-reachable fields (freeform-context
  parsing); fields needing live `gh` queries stay unpinned, so a
  conservative on_uncertain reply legitimately passes the first two cases —
  doubt de-escalating is correct behavior, and the repo test asserts exactly
  that pattern. Real runs need an agent-capable adapter
  (`--adapter-cmd 'claude -p'`).
- 2026-08-01 — Phase 5 complete (`aspec fmt`). Design choices: (1) `ruff`
  moved from dev to runtime dependencies — it is the layout engine, invoked
  as `python -m ruff format --isolated` so canonical layout ignores any
  project ruff config. (2) Statement reordering is done by slicing source
  line ranges from the AST (comments/blanks attach to the statement that
  follows), avoiding a libcst dependency; bodies with two statements on one
  line are left untouched rather than risked. (3) Module level is never
  reordered — constants and class order are semantic
  (definition-before-reference). Fixtures on disk stay unformatted on
  purpose; idempotence is asserted in memory.
- 2026-08-01 — Phase 4 complete (`aspec graph`). The failure layer trades
  completeness for readability: only `abort` gets real edges (into one shared
  unwind node, since the saga unwind is the safety path worth seeing);
  Retry/Escalate/defaults render as node label lines to avoid edge spaghetti.
  Mermaid-reserved words used as step vars (e.g. `end`) are sanitized with a
  trailing underscore.
- 2026-08-01 — Phase 3 complete (`aspec plan`). Two semantic decisions worth
  recording: (1) gate-skip propagation stops at inputs declared `X | None` —
  the consumer tolerates a skipped producer, which is exactly the mechanism
  that lets BookBank's PushAlert run on every terminal path, and the plan
  now proves that statically. (2) "False concurrency" is detected via shared
  exclusive tools in the same wave, the one signal that is structural rather
  than prose; a plan-level warning, not a lint diagnostic, because it is
  derived from the schedule rather than the spec text.
- 2026-08-01 — Phase 2 complete (`aspec lint`). AS036/AS037 deferred: both
  require inferring tool usage from free-form rule prose, which produced
  unacceptable false positives against BookBank in design (e.g. VerifyPlugin's
  rules *mention* `validate_book.py` that GenerateBook, not VerifyPlugin,
  runs). Per CLAUDE.md ("prefer structured data over free-form prose"), the
  right fix is a future structured way for constraints to reference tool
  surfaces — revisit then. Duplicate definitions became parser `P014`
  (AS032's job) since dict-based namespaces lose duplicates before lint runs.
  Dogfooding result: lint found one true finding against the reference spec —
  GenerateBook exceeds the ~15-constraint ceiling (16) — kept as a pinned
  warning, since the remedy (decomposing the task) belongs to the spec author.
- 2026-08-01 — Phase 1 complete. Diagnostic code spaces split: the parser
  emits `P0xx` (structural: the file cannot be fully represented as a
  SpecModel) and lint owns `AS0xx` (semantic checks over a valid SpecModel).
  The parser is tolerant — it records diagnostics and keeps going, so lint
  can report everything in one pass. BookBank has no fan-out, so
  `tests/fixtures/fanout_routine.aspec.py` was added to cover fan-out,
  filters, `on_item_failure`, and `list[X]` fields.
- 2026-08-01 — Phase 0 complete. First fixture smoke test found that
  `docs/BookBank_routine.aspec.py` had lost its class-body indentation (paste
  artifact) and was not valid Python; repaired in place and re-copied to
  `tests/fixtures/bookbank_routine.aspec.py`. The fixtures-must-parse test
  stays as a permanent guard.
