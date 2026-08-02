"""Typed changes produced by `aspec diff` (semantic spec review).

Every semantic difference between two SpecModels becomes one `Change`,
classified by its *direction* — the review question a text diff cannot
answer:

- ``widens``  — the new spec grants more capability or removes safety: a
  tool or op added, a must-rule removed or weakened, a gate, undo, or
  on_uncertain dropped, `strict`/`exclusive` dropped, a bound loosened.
- ``narrows`` — the reverse: capability removed, a rule added or
  strengthened, a bound tightened, a gate or undo declared.
- ``reshapes`` — a contract or wiring change that is neither: schema
  fields, task inputs/returns, docstrings (the instruction), pipeline
  rewiring, failure-policy restructuring.
- ``neutral`` — prose and metadata: whys, meta, module docstring.
"""

from typing import Any, Literal

from pydantic import BaseModel

Direction = Literal["widens", "narrows", "reshapes", "neutral"]

Category = Literal["module", "schema", "task", "rule", "tool", "failure", "pipeline"]


class Change(BaseModel):
    code: str  # stable id, e.g. "tool-surface-widened"
    category: Category
    kind: Literal["added", "removed", "changed"]
    direction: Direction
    owner: str  # "<module>", or the schema/task name the change belongs to
    detail: str  # one human-readable sentence
    old: Any = None
    new: Any = None
