# Dev mode — clarifications during development, silence in production

Decided 2026-08-02. Reframed by the user from "interactive mode": the
routine's semantics ARE its unattended semantics; dev mode is the
development harness where gaps surface cheaply. No language change — dev
mode is a runtime substitution on the doubt constructs, parallel to tool
substitution.

**Status: complete (2026-08-02).**

Design rules:

1. **Askability derives from declared doubt.** A question may only arise
   where the spec declared a doubt point (`on_uncertain` or `Escalate`).
   Tasks without one asserted "I never doubt here" — no permission is
   offered. On decline, the declared fallback applies verbatim.
2. **Clarification is not authorization.** Answers resolve questions; they
   never override must-rules, widen tools, or skip gates. Different
   behavior = edit the spec, rerun. A spec's own no-questions rule binds
   even in dev mode (rules outrank dispatch).
3. **Answers are late-bound inputs, recorded.** Every Q&A lands in
   `RunResult.clarifications` — auditable, and each one is a spec gap.
   The gap report is the point: dev runs end with "N clarifications —
   each is a candidate rule"; the count trending to zero is the maturity
   signal for unattended dispatch.

Tasks:

- [x] `Clarification` model; `RunResult.clarifications`
- [x] guard: `guarded_call(..., ask=)` — detect a `{"question": "..."}`
      reply, ask once per call, re-prompt with the answer (or the decline
      text); in unattended runs a question-shaped reply is a violation
      fed back verbatim
- [x] prompts: a dev-mode clarification section, added ONLY for tasks
      with declared doubt; the unattended prompt keeps "never ask"
- [x] single + orchestrate: thread `ask` through; collect clarifications
      with task names; reducer included
- [x] CLI: `aspec run --dev` (terminal asker); the gap report after the
      run — questions listed as candidate rules
- [x] Spec doc §9: "Development dispatch" subsection stating the three
      rules and the identity claim (unattended is the definition)
- [x] Skill (run-agentspec) + README mention
- [x] Tests: ask-then-conform, decline→declared fallback, unattended
      question = violation, doubt-gating (no permission without
      on_uncertain/Escalate), orchestrate e2e with task names, CLI flag
