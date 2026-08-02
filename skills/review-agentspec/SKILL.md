---
name: review-agentspec
description: Review an AgentSpec routine or a change to one — lint findings, semantic diff with capability-widening detection, and visual review via studio. Use when the user asks to "review this spec", "diff these specs", "did this spec get riskier", "check this .aspec.py", or wants a spec change assessed before an unattended run.
---

# Reviewing AgentSpec routines

Toolchain invocation: `uv run --project "$CLAUDE_PLUGIN_ROOT" aspec <cmd>`
(or `uvx --from git+https://github.com/sunprema/AgentSpec aspec <cmd>`).

## Single-spec review

1. `aspec lint --strict <spec>` — errors are conformance/contract breaks;
   warnings are judgment flags. Pay special attention to:
   - AS038 (a declared fallback violates the output contract — the routine
     misbehaves at its worst moment)
   - AS041/AS042 (routing values outside an enum / enum values no path can
     ever produce — dead or corrupt outcomes)
   - AS030 (must-rule without a why), AS031 (>15 rules — decompose)
2. `aspec plan <spec>` — read the waves: is sequencing that matters
   expressed as a bind? Does a false gate skip more (or less) than
   intended? Does the final reporting/alerting step run on EVERY terminal
   path (its inputs must be `X | None`)?
3. `aspec studio <spec>` for interactive review with the human — or
   `aspec studio <spec> --export review.html` to hand them a
   self-contained page (canvas, inspector, gate simulator included).

## Reviewing a change (the important one)

```sh
aspec diff OLD.aspec.py NEW.aspec.py
```

Every semantic change is classified by **direction**:

- **widens** — more capability or less safety: tools/ops added, must-rules
  removed or weakened, gates/undo/on_uncertain dropped, `strict` dropped,
  bounds loosened, enums widened. **Review these first, loudly.** A spec
  that widened is a routine that may now do more than anyone approved.
- **narrows** — the reverse; usually safe tightening.
- **reshapes** — contracts or wiring changed: schema fields,
  inputs/returns, bind rewires, gate conditions, derivation rows,
  docstrings (the instruction!), failure-policy restructuring.
- **neutral** — whys, `since=`, meta.

Rules match by id and derivation rows by condition, so a reworded rule
reports as a change to a stable name — read `rule-text-changed` as "the
binding requirement is different now", not cosmetics.

Exit codes follow diff(1): 0 same, 1 different, 2 trouble. `--json` for CI.

## What to say in the review

Lead with widening changes and anything that alters failure semantics
(abort → literal fallback is the run *continuing past failure*). Quote rule
ids, not paraphrases. If the reduction/derivation changed, state which
outcomes became reachable or unreachable. Recommend the spec's own
convention for accepted risks: a new rule with a why and a `since=`.
