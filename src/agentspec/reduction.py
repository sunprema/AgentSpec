"""Lift a prose reduction mapping into a typed decision table.

Spec §7 tells orchestrators to declare outcome classification "as an
ordered first-match-wins mapping in a rule". This module recognizes that
pattern — `condition -> ('outcome', 'stage')` clauses ending in an
`otherwise` row — and lifts it into data, so tooling can render it as a
grid and check its reachability instead of re-reading prose. If real
specs keep needing this, the table is the evidence for structured mapping
syntax (the two-failure rule).
"""

import re

from pydantic import BaseModel, Field

from agentspec.parser import SchemaDef, SpecModule, TaskDef

_CLAUSE = re.compile(r"^(.*?)\s*->\s*\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)", re.DOTALL)


class ReductionRow(BaseModel):
    condition: str  # "NOT workspace.resolved", …, or "otherwise"
    outcome: str
    stage: str


class ReductionTable(BaseModel):
    task: str  # the orchestrator
    rule_id: str
    rows: list[ReductionRow] = Field(default_factory=list)
    outcome_field: str | None = None  # enum field the outcomes target
    stage_field: str | None = None  # enum field the stages target

    @property
    def has_otherwise(self) -> bool:
        return any(row.condition == "otherwise" for row in self.rows)


def extract_tables(module: SpecModule) -> list[ReductionTable]:
    tables = []
    for task in module.tasks.values():
        if not task.is_orchestrator:
            continue
        for rule in task.constraints:
            table = _parse_rule(task, rule.id, rule.text)
            if table is not None:
                _resolve_target_fields(module, task, table)
                tables.append(table)
    return tables


def _parse_rule(task: TaskDef, rule_id: str, text: str) -> ReductionTable | None:
    if "->" not in text or "otherwise" not in text:
        return None
    rows = []
    for segment in text.split(";"):
        match = _CLAUSE.match(segment.strip())
        if match is None:
            continue
        condition = match.group(1).strip()
        # the first clause carries the rule's preamble prose; the condition
        # is whatever follows the final colon
        if ":" in condition:
            condition = condition.rsplit(":", 1)[1].strip()
        condition = " ".join(condition.split())
        rows.append(ReductionRow(condition=condition, outcome=match.group(2), stage=match.group(3)))
    if len(rows) < 2 or not any(r.condition == "otherwise" for r in rows):
        return None
    return ReductionTable(task=task.name, rule_id=rule_id, rows=rows)


def _resolve_target_fields(module: SpecModule, task: TaskDef, table: ReductionTable) -> None:
    """The enum fields of the returns schema with the largest value overlap
    (≥2) become the table's outcome/stage targets."""
    if task.returns is None or task.returns.kind != "name":
        return
    schema = module.schemas.get(task.returns.name or "")
    if schema is None:
        return
    table.outcome_field = _best_enum_field(schema, {row.outcome for row in table.rows})
    table.stage_field = _best_enum_field(schema, {row.stage for row in table.rows})


def _best_enum_field(schema: SchemaDef, values: set[str]) -> str | None:
    scored = []
    for field in schema.fields:
        if field.type.kind != "enum":
            continue
        overlap = len(values & {str(v) for v in field.type.values})
        # at least half the produced values must be members — tolerant of a
        # corrupted row (which AS041 then flags) without matching noise
        if overlap >= 1 and overlap * 2 >= len(values):
            scored.append((overlap, field.name))
    return max(scored)[1] if scored else None
