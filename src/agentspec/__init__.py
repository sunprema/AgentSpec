"""Static toolchain for AgentSpec.

AgentSpec is a declarative specification language for autonomous AI routines
(see docs/AgentSpec-specification.md). This package provides the reference
toolchain: parser, linter, execution planner, graph generator, and — later —
eval and guarded-run frontends.

Specs are parsed as AST and never executed. Nothing in this package may
import or exec a `.aspec.py` file.
"""

__version__ = "0.1.0"
