"""Validate generated data and render it through the document template."""

from __future__ import annotations

import re
from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateError
from pydantic import BaseModel, ValidationError

from slotdown.config import Config
from slotdown.loader import Document
from slotdown.models import build_fields_model, build_inputs_model

_BLANK_RUN = re.compile(r"\n{3,}")


class DataError(Exception):
    """Raised when inputs or AI-generated fields fail validation."""

    def __init__(self, source: str, problems: list[str]):
        self.source = source
        self.problems = problems
        super().__init__(f"invalid {source}:\n" + "\n".join(problems))


class RenderError(Exception):
    pass


def make_environment(config: Config) -> Environment:
    return Environment(
        undefined=StrictUndefined,
        trim_blocks=config.trim_blocks,
        lstrip_blocks=config.lstrip_blocks,
        keep_trailing_newline=True,
        autoescape=False,
    )


def validate(model: type[BaseModel], data: Any, source: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise DataError(source, [f"expected a JSON object, got {type(data).__name__}"])
    try:
        instance = model.model_validate(data)
    except ValidationError as exc:
        raise DataError(source, [_format_error(err) for err in exc.errors()]) from exc
    return instance.model_dump(by_alias=True)


def render_document(
    doc: Document, fields: Any, inputs: Any | None = None, config: Config | None = None
) -> str:
    config = config or Config()
    context = validate(build_fields_model(doc.schema), fields, "fields")
    context["inputs"] = validate(build_inputs_model(doc.schema), inputs or {}, "inputs")

    env = make_environment(config)
    try:
        output = env.from_string(doc.template).render(context)
    except TemplateError as exc:
        raise RenderError(f"template error: {exc}") from exc
    except Exception as exc:  # e.g. `{{ x | length }}` on a null optional field
        raise RenderError(f"template error: {type(exc).__name__}: {exc}") from exc

    if config.collapse_blank_lines:
        output = _BLANK_RUN.sub("\n\n", output)
    return output.strip("\n") + "\n"


def _format_error(err: dict[str, Any]) -> str:
    location = ".".join(str(part) for part in err["loc"]) or "<root>"
    message = err["msg"]
    if err["type"] == "extra_forbidden":
        message = "field is not defined in the schema"
    return f"{location}: {message}"
