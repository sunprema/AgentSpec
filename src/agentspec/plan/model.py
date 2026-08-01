"""The ExecutionPlan: what runs concurrently, what waits, what fans out,
and what a false gate skips — all derived from the data-flow binds."""

from pydantic import BaseModel, Field


class StepPlan(BaseModel):
    var: str
    task: str
    wave: int
    depends_on: list[str] = Field(default_factory=list)  # prior vars, sorted
    gate: str | None = None  # dotted gate path (or raw text if malformed)
    fanout_over: str | None = None  # dotted source path for fan-outs
    filter: str | None = None  # raw filter text
    serialized: bool = False  # fan-out over an exclusive tool: one item at a time
    exclusive_tools: list[str] = Field(default_factory=list)


class ExecutionPlan(BaseModel):
    orchestrator: str
    file: str
    steps: list[StepPlan] = Field(default_factory=list)
    waves: list[list[str]] = Field(default_factory=list)
    # gated var -> everything a false gate transitively skips (beyond itself).
    # Skips do not propagate through inputs declared `X | None`: a consumer
    # that tolerates a missing value still runs (spec §7).
    gate_skips: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    def step(self, var: str) -> StepPlan | None:
        return next((s for s in self.steps if s.var == var), None)
