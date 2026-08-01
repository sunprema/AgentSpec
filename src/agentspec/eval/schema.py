"""Build real pydantic models from a spec's declared schemas, so model
output is validated against the actual contract: closed enums, Field
bounds, optionality — with extra fields forbidden."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from agentspec.parser import SpecModule, TypeExpr

_BUILTINS: dict[str, type] = {"str": str, "int": int, "float": float, "bool": bool}


def build_output_model(
    module: SpecModule,
    schema_name: str,
    _cache: dict[str, type[BaseModel] | None] | None = None,
) -> type[BaseModel]:
    cache = _cache if _cache is not None else {}
    cached = cache.get(schema_name)
    if cached is not None:
        return cached
    schema = module.schemas[schema_name]
    cache[schema_name] = None  # in-progress marker; self-recursion falls back to Any
    fields: dict[str, Any] = {}
    for field in schema.fields:
        annotation = _python_type(module, field.type, cache)
        fields[field.name] = (
            annotation,
            Field(
                default=field.default if field.has_default else ...,
                ge=field.ge,
                le=field.le,
                max_length=field.max_length,
            ),
        )
    model = create_model(schema_name, __config__=ConfigDict(extra="forbid"), **fields)
    cache[schema_name] = model
    return model


def _python_type(
    module: SpecModule, type_expr: TypeExpr, cache: dict[str, type[BaseModel] | None]
) -> Any:
    if type_expr.kind == "enum" and type_expr.values:
        annotation: Any = Literal[tuple(type_expr.values)]
    elif type_expr.kind == "list":
        item = _python_type(module, type_expr.item, cache) if type_expr.item is not None else Any
        annotation = list[item]
    elif type_expr.kind == "name" and type_expr.name in _BUILTINS:
        annotation = _BUILTINS[type_expr.name]
    elif type_expr.kind == "name" and type_expr.name in module.schemas:
        if type_expr.name in cache and cache[type_expr.name] is None:
            annotation = Any  # self-recursive schema: do not loop
        else:
            annotation = build_output_model(module, type_expr.name, cache)
    else:
        annotation = Any
    if type_expr.optional:
        annotation = annotation | None
    return annotation
