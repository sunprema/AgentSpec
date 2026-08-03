"""Derivation-bind checks (spec 2.2): AS043 missing catch-all, AS044
inconsistent row keys, AS008 unknown condition roots, and the AS041/AS042
enum checks applied natively to the construct."""

from collections.abc import Iterator

from agentspec.diagnostics import Diagnostic
from agentspec.lint.catalog import mk
from agentspec.parser import DerivationBind, SpecModule, TaskDef


def check_derivations(module: SpecModule) -> Iterator[Diagnostic]:
    for task in module.tasks.values():
        for derivation in task.derivations:
            yield from _check_shape(task, derivation)
            yield from _check_roots(task, derivation)
            yield from _check_enums(module, task, derivation)


def _check_shape(task: TaskDef, derivation: DerivationBind) -> Iterator[Diagnostic]:
    if not derivation.has_catch_all:
        yield mk(
            "AS043",
            f"derivation '{derivation.var}' has no True: catch-all row — "
            "without one its value is undefined when no condition matches",
            derivation.loc,
        )
    else:
        last = derivation.rows[-1]
        if not last.is_catch_all:
            yield mk(
                "AS043",
                f"derivation '{derivation.var}': the True: catch-all must be "
                "the last row — rows after it can never match",
                derivation.loc,
            )
    key_sets = {frozenset(row.output) for row in derivation.rows}
    if len(key_sets) > 1:
        fields = " vs ".join(sorted("{" + ", ".join(sorted(keys)) + "}" for keys in key_sets))
        yield mk(
            "AS044",
            f"derivation '{derivation.var}': rows declare different field "
            f"sets ({fields}) — consumers cannot rely on the value's shape",
            derivation.loc,
        )


def _check_roots(task: TaskDef, derivation: DerivationBind) -> Iterator[Diagnostic]:
    always = {i.name for i in task.inputs} | {"env"}
    line_of = {b.var: b.loc.line for b in task.pipeline} | {
        d.var: d.loc.line for d in task.derivations
    }
    for root in sorted(derivation.referenced_roots() - always):
        if root not in line_of:
            yield mk(
                "AS008",
                f"derivation '{derivation.var}' references '{root}', which is "
                "not a prior step, derivation, or input",
                derivation.loc,
            )
        elif root == derivation.var:
            yield mk(
                "AS008",
                f"derivation '{derivation.var}' references itself",
                derivation.loc,
            )
        elif line_of[root] >= derivation.loc.line:
            yield mk(
                "AS008",
                f"derivation '{derivation.var}' references '{root}' before it "
                "is bound — a name must exist before it is referenced",
                derivation.loc,
            )


def _check_enums(
    module: SpecModule, task: TaskDef, derivation: DerivationBind
) -> Iterator[Diagnostic]:
    """When an output field name matches an enum field of the orchestrator's
    returns schema, row literals must be members (AS041), and every enum
    value must be producible by the derivation or on_uncertain (AS042)."""
    returns = task.returns
    if returns is None or returns.kind != "name" or returns.name not in module.schemas:
        return
    schema = module.schemas[returns.name]
    fallback_values = {str(v) for v in (task.on_uncertain or {}).values()}
    for field in schema.fields:
        if field.type.kind != "enum":
            continue
        produced: set[str] = set()
        covers_field = False
        for row in derivation.rows:
            ref = row.output.get(field.name)
            if ref is None:
                continue
            covers_field = True
            if ref.path is not None:
                return  # copied values are opaque; skip both checks for this field
            produced.add(str(ref.value))
            declared = {str(v) for v in field.type.values}
            if str(ref.value) not in declared:
                yield mk(
                    "AS041",
                    f"derivation '{derivation.var}' produces "
                    f"{field.name}='{ref.value}', which is not a member of "
                    f"{schema.name}.{field.name}",
                    row.loc,
                )
        if not covers_field:
            continue
        declared = {str(v) for v in field.type.values}
        for value in sorted(declared - produced - fallback_values):
            yield mk(
                "AS042",
                f"{schema.name}.{field.name} value '{value}' is neither "
                f"produced by derivation '{derivation.var}' nor by "
                "on_uncertain — no path can ever return it",
                derivation.loc,
            )
