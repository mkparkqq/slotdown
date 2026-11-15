"""Build Pydantic models dynamically from field specs."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, create_model

from slotdown.schema import DocSchema, FieldSpec

# `number` keeps JSON integers as ints (so 3 renders as "3", not "3.0") and rejects NaN/Infinity.
_SCALARS: dict[str, Any] = {"string": str, "integer": int, "number": int | FiniteFloat, "boolean": bool}

# AI output must match the schema exactly: no type coercion, no unknown keys.
_CONFIG = ConfigDict(strict=True, extra="forbid", protected_namespaces=())


def build_fields_model(schema: DocSchema) -> type[BaseModel]:
    return build_model(_model_name(schema.name, "Fields"), schema.fields)


def build_inputs_model(schema: DocSchema) -> type[BaseModel]:
    return build_model(_model_name(schema.name, "Inputs"), schema.inputs)


def build_model(name: str, fields: dict[str, FieldSpec]) -> type[BaseModel]:
    definitions: dict[str, Any] = {}
    for field_name, spec in fields.items():
        annotation = _annotation(spec, f"{name}_{field_name}")
        kwargs: dict[str, Any] = {"alias": field_name, "title": field_name, **_constraints(spec)}
        if spec.optional:
            annotation = annotation | None
            kwargs["default"] = None
        # Internal attribute names are prefixed so user field names can never
        # shadow BaseModel attributes (`json`, `copy`, `model_*`, ...).
        definitions[f"f_{field_name}"] = (annotation, Field(**kwargs))
    return create_model(name, __config__=_CONFIG, **definitions)


def _annotation(spec: FieldSpec, model_name: str) -> Any:
    if spec.type in _SCALARS:
        return _SCALARS[spec.type]
    if spec.type == "enum":
        return Literal[tuple(spec.values or ())]
    if spec.type == "list":
        assert spec.items is not None
        item = _annotation(spec.items, f"{model_name}_item")
        if spec.items.optional:
            item = item | None
        if item_constraints := _constraints(spec.items):
            item = Annotated[item, Field(**item_constraints)]
        return list[item]
    if spec.type == "object":
        return build_model(model_name, spec.fields)
    raise ValueError(f"unsupported type {spec.type!r}")


def _constraints(spec: FieldSpec) -> dict[str, Any]:
    """Field() keyword arguments carrying the spec's description, examples and list bounds."""
    kwargs: dict[str, Any] = {}
    if spec.description:
        kwargs["description"] = spec.description
    if spec.examples:
        kwargs["examples"] = spec.examples
    if spec.type == "list":
        if spec.min_items is not None:
            kwargs["min_length"] = spec.min_items
        if spec.max_items is not None:
            kwargs["max_length"] = spec.max_items
    return kwargs


def _model_name(doc_name: str, suffix: str) -> str:
    base = "".join(part.capitalize() for part in doc_name.replace("-", "_").split("_") if part)
    return f"{base or 'Document'}{suffix}"
