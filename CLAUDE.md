# AgentSpec Project Guidelines

## Purpose

AgentSpec is a **declarative specification language** for autonomous AI routines.

It is **not**:

- a prompt engineering framework
- an orchestration framework
- an agent runtime
- a wrapper around a specific LLM

AgentSpec specifies **what** an autonomous routine must do.

A runtime determines **how** to execute it.

The language must remain:

- deterministic
- statically analyzable
- provider independent
- human readable
- machine verifiable

Every design decision should move the language closer to these goals.

---

# Design Principles

## 1. Specification over implementation

The spec describes behavior.

It never describes implementation.

Good:

```python
Task(
    ...
)
```

Bad:

```python
Call Claude.
Think step by step.
```

Execution details belong to runtimes.

---

## 2. Deterministic semantics

Every language feature must have well-defined execution semantics.

There should never be ambiguity such as:

- "probably"
- "usually"
- "the runtime may decide"

Execution should be reproducible from the specification.

---

## 3. Declarative over imperative

Prefer:

```python
GenerateSummary(
    source=documents
)
```

over

```python
for doc in documents:
    ...
```

Specifications describe desired outcomes.

They do not describe algorithms.

---

## 4. Static analysis first

Every new language feature should be evaluated by asking:

Can this be linted?

If the answer is "no", reconsider the design.

Examples of desirable static analysis:

- undefined task references
- cycles
- unreachable tasks
- missing reducers
- undeclared tool use
- invalid schemas
- conflicting constraints
- impossible execution paths

---

## 5. Human review matters

AgentSpec files should be understandable during code review.

Avoid clever syntax.

Explicit is preferred over concise.

---

# Python Usage

Python is currently the host language.

Treat Python as syntax, not as execution.

AgentSpec files should not contain:

- network requests
- filesystem mutations
- business logic
- arbitrary computation
- side effects

The Python file should be declarative.

---

# Tooling Goals

The repository should support:

- parser
- formatter
- linter
- validator
- execution graph generation
- language server
- documentation generation
- test runner

Language changes should consider all tooling.

---

# Backwards Compatibility

The language should evolve conservatively.

Breaking changes require:

- motivation
- migration strategy
- documentation updates
- version history entry

Avoid unnecessary syntax churn.

---

# Constraints

Constraints are part of the specification.

They are not comments.

Future tooling should be able to:

- inspect them
- visualize them
- validate them
- report violations

When introducing new constraint types, prefer structured data over free-form prose.

---

# Tasks

Tasks are the primary abstraction.

A task should:

- have a single responsibility
- have explicit inputs
- have explicit outputs
- declare required tools
- declare failure behavior
- declare uncertainty behavior

Tasks should remain composable.

---

# Tools

Tools represent capabilities.

Tools do not represent implementations.

A runtime may substitute an implementation only when:

- capability is equivalent
- declared scope is preserved
- substitution is recorded

Never widen capabilities implicitly.

---

# Safety

Safety is part of the language.

It is never optional.

Examples include:

- reversible operations
- bounded execution
- uncertainty handling
- human escalation
- auditability

Safety should be statically visible whenever possible.

---

# Lint Philosophy

Prefer reporting potential problems instead of silently accepting them.

Good lint rules prevent production incidents.

Examples:

- undeclared outputs
- unreachable tasks
- unused tasks
- missing failure handlers
- duplicate task names
- ambiguous reducers
- capability escalation
- schema mismatches

---

# Documentation

The specification is the source of truth.

Documentation explains the specification.

Documentation should never redefine language semantics.

---

# Testing

Every language feature should include:

- parser tests
- validation tests
- execution tests
- serialization tests (if applicable)

Regression tests are preferred over ad hoc examples.

---

# What Not To Build

Do not add features merely because another framework has them.

AgentSpec should not become:

- LangGraph
- Airflow
- Temporal
- BPMN
- an SDK
- an orchestration runtime

Maintain focus on specification.

---

# Decision Checklist

Before introducing a new language feature, ask:

1. Does it make specifications easier to understand?
2. Can it be statically analyzed?
3. Can it be visualized?
4. Can it be linted?
5. Does it preserve deterministic semantics?
6. Is it provider independent?
7. Would this still make sense five years from now?

If the answer to multiple questions is "no", rethink the design.

---

# Long-Term Vision

AgentSpec should become for autonomous AI routines what Terraform is for infrastructure:

- declarative
- reviewable
- version controlled
- testable
- lintable
- visualizable
- provider independent

The language should outlive individual LLMs, APIs, and runtimes.

## Reference

[AgentSpec Specification](/docs/AgentSpec-specification.md)
[BookBank Referral implemntation of Spec](/docs/BookBank_routine.aspec.py)
