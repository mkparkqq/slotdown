"""Load `.sd` files: split YAML front matter (schema) from the Jinja2 body (template)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from slotdown.schema import DocSchema, SchemaError, parse_schema

_DELIMITER = "---"


class _SchemaLoader(yaml.SafeLoader):
    """SafeLoader without YAML 1.1 surprises: `yes`/`no`/`on`/`off` and bare dates
    stay strings, so enum values and examples match what the AI sees in JSON."""


_SchemaLoader.yaml_implicit_resolvers = {
    first: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag not in ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp")
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_SchemaLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


@dataclass
class Document:
    schema: DocSchema
    template: str
    # Number of file lines before the template body; add it to Jinja line numbers
    # to point at the right line in the `.sd` file.
    template_line_offset: int
    path: Path | None = None


def split_front_matter(text: str) -> tuple[str, str, int]:
    """Return (front_matter, body, body_line_offset)."""
    lines = text.removeprefix("\ufeff").splitlines(keepends=True)
    if not lines or lines[0].rstrip() != _DELIMITER:
        raise SchemaError(["file must start with a '---' line opening the YAML front matter"])
    for index in range(1, len(lines)):
        # Only an unindented `---` closes the front matter; an indented one may be
        # content of a block scalar (e.g. a markdown rule inside `instructions`).
        if lines[index].rstrip() == _DELIMITER:
            front = "".join(lines[1:index])
            body = "".join(lines[index + 1 :])
            return front, body, index + 1
    raise SchemaError(["front matter is not closed by a '---' line"])


def parse_document(text: str, path: Path | None = None) -> Document:
    front, body, offset = split_front_matter(text)
    try:
        raw = yaml.load(front, Loader=_SchemaLoader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        raise SchemaError([f"invalid YAML in front matter: {exc}"]) from exc
    return Document(schema=parse_schema(raw), template=body, template_line_offset=offset, path=path)


def load_document(path: str | Path) -> Document:
    path = Path(path)
    return parse_document(path.read_text(encoding="utf-8"), path)
