"""Document schema definitions: parse the YAML front matter of a `.sd` file."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SCALAR_TYPES = frozenset({"string", "integer", "number", "boolean"})
ALL_TYPES = SCALAR_TYPES | {"enum", "list", "object"}

_COMMON_KEYS = {"type", "description", "optional", "examples"}
_TYPE_KEYS: dict[str, set[str]] = {
    "string": set(),
    "integer": set(),
    "number": set(),
    "boolean": set(),
    "enum": {"values"},
    "list": {"items", "min_items", "max_items"},
    "object": {"fields"},
}
_DOC_KEYS = {"name", "description", "instructions", "inputs", "fields"}

_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
# Templates receive plain dicts, and Jinja resolves `obj.name` via getattr first,
# so a field called e.g. `items` would resolve to dict.items instead of the value.
RESERVED_NAMES = frozenset(n for n in dir(dict) if not n.startswith("_"))
# Top-level fields become template variables, so they must not be Jinja keywords,
# constants or names Jinja binds itself (`{{ self }}` is the template reference).
JINJA_RESERVED_NAMES = frozenset(
    {"self", "loop", "caller", "varargs", "kwargs", "true", "false", "none", "True", "False", "None"}
    | {"and", "or", "not", "in", "is", "if", "else", "elif", "for", "endfor", "endif", "recursive"}
    | {"block", "extends", "import", "include", "from", "as", "with", "without", "context", "set"}
    | {"macro", "call", "filter", "raw", "ignore", "scoped", "required"}
)


class SchemaError(Exception):
    """Raised when a document schema is malformed. Carries every problem found."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass
class FieldSpec:
    name: str
    type: str
    description: str | None = None
    optional: bool = False
    examples: list[Any] | None = None
    values: list[Any] | None = None  # enum
    items: FieldSpec | None = None  # list
    min_items: int | None = None  # list
    max_items: int | None = None  # list
    fields: dict[str, FieldSpec] = field(default_factory=dict)  # object


@dataclass
class DocSchema:
    name: str
    description: str | None = None
    instructions: str | None = None
    inputs: dict[str, FieldSpec] = field(default_factory=dict)
    fields: dict[str, FieldSpec] = field(default_factory=dict)


def parse_schema(raw: Any) -> DocSchema:
    """Parse the front matter mapping into a DocSchema, collecting all problems."""
    problems: list[str] = []
    if not isinstance(raw, dict):
        raise SchemaError(["front matter must be a YAML mapping"])

    for key in raw.keys() - _DOC_KEYS:
        problems.append(f"unknown top-level key '{key}'")

    name = raw.get("name")
    if not isinstance(name, str) or not name:
        problems.append("'name' is required and must be a non-empty string")
        name = ""
    for key in ("description", "instructions"):
        if raw.get(key) is not None and not isinstance(raw[key], str):
            problems.append(f"'{key}' must be a string")

    inputs = _parse_field_map(raw.get("inputs"), "inputs", problems)
    fields = _parse_field_map(raw.get("fields"), "fields", problems)
    if not fields and "fields" not in raw:
        problems.append("'fields' is required")
    if "inputs" in fields:
        problems.append("fields.inputs: 'inputs' is reserved for the template's input namespace")

    if problems:
        raise SchemaError(problems)
    return DocSchema(
        name=name,
        description=raw.get("description"),
        instructions=raw.get("instructions"),
        inputs=inputs,
        fields=fields,
    )


def _parse_field_map(raw: Any, path: str, problems: list[str]) -> dict[str, FieldSpec]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        problems.append(f"{path}: must be a mapping of field name to definition")
        return {}
    result: dict[str, FieldSpec] = {}
    for name, definition in raw.items():
        sub_path = f"{path}.{name}"
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            problems.append(
                f"{sub_path}: invalid field name (use letters, digits and '_', starting with a letter)"
            )
            continue
        if name in RESERVED_NAMES:
            problems.append(f"{sub_path}: '{name}' collides with a dict method in templates; rename it")
            continue
        if path == "fields" and name in JINJA_RESERVED_NAMES:
            problems.append(f"{sub_path}: '{name}' is reserved in Jinja templates; rename it")
            continue
        spec = _parse_field(name, definition, sub_path, problems)
        if spec is not None:
            result[name] = spec
    return result


def _parse_field(name: str, raw: Any, path: str, problems: list[str]) -> FieldSpec | None:
    if not isinstance(raw, dict):
        problems.append(f"{path}: definition must be a mapping")
        return None
    type_ = raw.get("type")
    if type_ not in ALL_TYPES:
        problems.append(f"{path}: 'type' must be one of {', '.join(sorted(ALL_TYPES))} (got {type_!r})")
        return None

    for key in raw.keys() - _COMMON_KEYS - _TYPE_KEYS[type_]:
        problems.append(f"{path}: key '{key}' is not valid for type '{type_}'")

    spec = FieldSpec(name=name, type=type_)

    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        problems.append(f"{path}: 'description' must be a string")
    spec.description = description

    optional = raw.get("optional", False)
    if not isinstance(optional, bool):
        problems.append(f"{path}: 'optional' must be true or false")
    spec.optional = bool(optional)

    examples = raw.get("examples")
    if examples is not None and not isinstance(examples, list):
        problems.append(f"{path}: 'examples' must be a list")
        examples = None
    spec.examples = examples

    if type_ == "enum":
        values = raw.get("values")
        if not isinstance(values, list) or not values:
            problems.append(f"{path}: enum requires a non-empty 'values' list")
        elif len(set(map(repr, values))) != len(values):
            problems.append(f"{path}: enum 'values' contains duplicates")
        else:
            spec.values = values

    elif type_ == "list":
        if "items" not in raw:
            problems.append(f"{path}: list requires 'items'")
        else:
            spec.items = _parse_field("items", raw["items"], f"{path}.items", problems)
        for key in ("min_items", "max_items"):
            value = raw.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                problems.append(f"{path}: '{key}' must be a non-negative integer")
            else:
                setattr(spec, key, value)
        if spec.min_items is not None and spec.max_items is not None and spec.min_items > spec.max_items:
            problems.append(f"{path}: 'min_items' is greater than 'max_items'")

    elif type_ == "object":
        sub_fields = raw.get("fields")
        if not isinstance(sub_fields, dict) or not sub_fields:
            problems.append(f"{path}: object requires a non-empty 'fields' mapping")
        else:
            spec.fields = _parse_field_map(sub_fields, f"{path}.fields", problems)

    return spec
