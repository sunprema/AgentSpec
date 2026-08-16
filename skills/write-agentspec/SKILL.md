---
name: write-agentspec
description: Author an AgentSpec routine (.aspec.py) — the declarative specification language for autonomous, unattended AI agents. Use when the user wants to "write a spec", "create an AgentSpec", "turn this prompt/runbook/routine into a spec", "specify an agent routine", or mentions .aspec.py files. Covers the full authoring loop with the aspec toolchain (new, lint, plan, graph, fmt, studio).
---

# Writing an AgentSpec routine

An `.aspec.py` file is valid Python that is **never executed**: it declares
intent, contracts, tool boundaries, and failure behavior; a model reads the
file and executes it directly. The full language reference ships with this
plugin at `docs/AgentSpec-specification.md` under `$CLAUDE_PLUGIN_ROOT` —
read it before authoring anything non-trivial. `docs/BookBank_routine.aspec.py`
is the canonical real-world example.

## Running the toolchain

Inside this plugin's root (it is a uv project):

```sh
uv run --project "$CLAUDE_PLUGIN_ROOT" aspec <cmd> ...
```

Anywhere else: `uvx --from git+https://github.com/sunprema/AgentSpec aspec <cmd>`.

## The authoring loop

1. `aspec new <name>` — scaffold a commented starter spec (already
   lint-clean and fmt-canonical). Start here, not from a blank file.
2. Shape the **schemas** first: every task output is a named Pydantic
   model. Closed sets are `Enum["a", "b"]`; bounds (`Field(ge=, le=,
   max_length=)`) are binding at runtime. `X | None` means optional — and
   changes scheduling (see gotchas).
3. Write the **tasks**: docstring = the instruction; bare annotations =
   inputs; `returns:` = the contract. Declare `tools`, `constraints`,
   `undo`, `on_uncertain`, `on_failure`.
4. Wire the **orchestrator**: class-body assignments are the pipeline.
   Data flow, gates, fan-out, and concurrency all derive from them.
5. Verify after every change:
   - `aspec lint --strict` — must be clean before anything ships
   - `aspec plan` — check the derived waves and what false gates skip
   - `aspec graph` / `aspec studio` — review the structure visually
   - `aspec fmt` — canonicalize before committing

## The constructs, compressed

```python
Rule("kebab-case-id",                     # every rule has a reviewer-facing id
     "The rule text",
     why="the cost of breaking it",       # must-rules without a why lint AS030
     severity="must",                     # must (default) | should | may
     since="v1.2.0")                      # incident/revision provenance

tools = [Tool("gh", ops=["issue list"]),  # a CAPABILITY named by its preferred
         Tool("x", strict=True),          # mechanism — substitution allowed
         Tool("git", ops=[...], exclusive=True)]  # unless strict; exclusive
                                          # serializes fan-out per item

step  = DoThing(param=prior.field)                    # step: kwargs are paths
each  = [Work(item=i) for i in scan.items             # fan-out + caged filter
         if i.kind in ["a", "b"]]                     # (paths vs literals only)
maybe = DoThing(...) if prior.ok else None            # gate (or `if not prior.ok`)

route = Cond(                                         # derivation: mechanical
    (not check.ok, {"outcome": "failed", "n": 0}),    # routing, first match
    (check.n > 3, {"outcome": "busy", "n": check.n}), # wins top to bottom;
    (True, {"outcome": "fine", "n": check.n}),        # (True, ...) catch-all
)                                                     # is mandatory and last
alert = Notify(outcome=route.outcome)                 # consumers reference it

outcomes = [                                          # declared endings: name,
    Outcome("failed", when=not check.ok, alert=True), # condition, report
    Outcome("fine", when=True, alert=False),          # fields, and alerting in
]                                                     # one construct; when=True
page = Notify(...) if outcomes.alert else None        # catch-all is mandatory

on_uncertain = {...literal matching returns...}   # the declared doubt path
on_failure = "abort"                              # or a literal, Retry(...,
                                                  # then=...), Escalate(...)
undo = "How to reverse completed effects"         # runs ONLY on abort-unwind
```

## Gotchas that lint cannot always save you from

- **Optional inputs control scheduling.** A false gate skips a step and
  everything depending on it — but skips do NOT propagate through inputs
  declared `X | None`. Declare a final join's inputs optional so it runs on
  every terminal path.
- **Derivations never skip.** Conditions over skipped steps are false;
  copied values are null. Use a derivation for outcome routing and give the
  alerting/reporting step the derived value as an input.
- **Tools are capabilities, not binaries.** Declare the full surface the
  procedure needs plus a degradation branch for anything that may be
  missing; never rely on the agent installing something undeclared.
- **Fallback literals are contracts.** `on_uncertain` / literal
  `on_failure` must validate against the returns schema (lint AS038,
  strict typing).
- **Keep tasks under ~15 rules** (AS031) — decompose instead.
- **The docstring is where operational history accumulates**: after every
  surprising run, add the lesson as a rule (with `since=`) and a docstring
  history line.

Before finishing, run `aspec lint --strict`, `aspec plan`, and `aspec fmt`
one final time and show the user the plan output so gates and concurrency
are visible.
