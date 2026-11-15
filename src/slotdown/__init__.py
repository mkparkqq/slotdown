"""slotdown: the template decides the layout, the AI fills the slots."""

from slotdown.check import Issue, check_document
from slotdown.config import Config, load_config
from slotdown.loader import Document, load_document, parse_document
from slotdown.render import DataError, RenderError, render_document
from slotdown.schema import DocSchema, FieldSpec, SchemaError

__all__ = [
    "Config",
    "DataError",
    "DocSchema",
    "Document",
    "FieldSpec",
    "Issue",
    "RenderError",
    "SchemaError",
    "check_document",
    "load_config",
    "load_document",
    "parse_document",
    "render_document",
]
