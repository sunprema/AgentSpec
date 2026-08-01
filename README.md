# AgentSpec

A declarative specification language for autonomous AI routines — and its
static reference toolchain.

An AgentSpec file (`.aspec.py`) is valid Python that is **never executed as
code**. It declares intent, constraints, data contracts, tool boundaries, and
failure behavior; a model reads the file and executes it directly. See
[the specification](docs/AgentSpec-specification.md) for the language, and
[the BookBank routine](docs/BookBank_routine.aspec.py) for a real spec.

## Toolchain

This repository hosts the `aspec` CLI (Python, managed with [uv](https://docs.astral.sh/uv/)):

```
aspec lint   # conformance, type-flow, and rule-hygiene checks
aspec plan   # derived execution waves: concurrency, gates, fan-out
aspec graph  # the pipeline as a Mermaid flowchart
aspec fmt    # canonical formatting
aspec eval   # fixture-based behavioral tests
aspec run    # guarded execution
```

All analysis is static: specs are parsed as AST and never imported or
executed. Implementation status is tracked in [plan/toolchain.md](plan/toolchain.md).

## Development

```sh
uv sync            # create the environment
uv run aspec       # run the CLI
uv run pytest      # tests
uv run ruff check src tests && uv run ruff format --check src tests
```
