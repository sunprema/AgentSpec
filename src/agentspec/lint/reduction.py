"""Reduction-mapping checks over lifted decision tables: AS041 row values
outside the target enum, AS042 enum values no path can ever produce."""

from collections.abc import Iterator

from agentspec.diagnostics import Diagnostic
from agentspec.lint.catalog import mk
from agentspec.parser import SpecModule
from agentspec.reduction import ReductionTable, extract_tables


def check_reduction(module: SpecModule) -> Iterator[Diagnostic]:
    for table in extract_tables(module):
        task = module.tasks[table.task]
        yield from _check_axis(module, table, table.outcome_field, "outcome")
        yield from _check_axis(module, table, table.stage_field, "stage")
        if not table.has_otherwise:  # unreachable today (extractor requires it)
            yield mk(
                "AS041",
                f"reduction mapping '{table.rule_id}' has no otherwise row",
                task.loc,
            )


def _check_axis(
    module: SpecModule, table: ReductionTable, field_name: str | None, axis: str
) -> Iterator[Diagnostic]:
    task = module.tasks[table.task]
    if field_name is None or task.returns is None or task.returns.name is None:
        return
    schema = module.schemas[task.returns.name]
    field = schema.field(field_name)
    if field is None:
        return
    declared = {str(v) for v in field.type.values}
    produced = {getattr(row, axis) for row in table.rows}

    for value in sorted(produced - declared):
        yield mk(
            "AS041",
            f"reduction mapping '{table.rule_id}' produces {axis} '{value}', "
            f"which is not a member of {schema.name}.{field_name}",
            task.loc,
        )

    # values the mapping can never yield are unreachable — unless the
    # declared doubt path (on_uncertain) produces them
    fallback_values = {str(v) for v in (task.on_uncertain or {}).values()}
    for value in sorted(declared - produced - fallback_values):
        yield mk(
            "AS042",
            f"{schema.name}.{field_name} value '{value}' is neither produced "
            f"by reduction mapping '{table.rule_id}' nor by on_uncertain — "
            "no path can ever return it",
            task.loc,
        )
