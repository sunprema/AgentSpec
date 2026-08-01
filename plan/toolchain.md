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

- [ ] Canonical formatting for spec files (deterministic ordering of reserved
      attributes, rule-tuple layout, line wrapping) built on `ruff format` plus
      spec-specific ordering rules
- [ ] `--check` mode for CI
- [ ] Idempotence test: `fmt(fmt(x)) == fmt(x)` over all fixtures

## Phase 6 — `aspec eval` (fixture-based behavioral tests)

The first phase that talks to a model; everything before this stays fully
static. Gate spec changes like code changes.

- [ ] Eval file format: known inputs, expected-output assertions, referencing
      the spec's own declared schemas for validation
- [ ] Schema validation of model output against declared contracts
      (Pydantic, including `Field` bounds and closed enums)
- [ ] Assertion language kept declarative (equality, membership, bounds — no
      arbitrary code)
- [ ] Runner with per-case pass/fail report and `--json`
- [ ] Provider-independent model adapter boundary (one interface; concrete
      adapters live outside the core)
- [ ] At least one eval for the BookBank routine's `SelectIssue` task

## Phase 7 — `aspec run` / `orchestrate` (guarded execution)

Last, and deliberately thin — AgentSpec must not become a runtime framework.

- [ ] Guarded single-agent run: dispatch context → root task, outputs
      validated against declared schemas, violations fed back verbatim for
      bounded repair
- [ ] Declared `on_failure` honored when conformance cannot be reached
- [ ] `orchestrate`: optional one-subagent-per-step mode, each receiving only
      its own pruned contract plus inherited constraints
- [ ] Run report records tool substitutions, rule conflicts, and
      verified-vs-inferred claims (§9 non-negotiables)

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
