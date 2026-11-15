"""Command line interface: `slotdown check | render | schema`."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from slotdown.check import check_document
from slotdown.config import Config, ConfigError, load_config
from slotdown.loader import Document, load_document
from slotdown.models import build_fields_model
from slotdown.render import DataError, RenderError, render_document
from slotdown.schema import SchemaError

app = typer.Typer(
    help="Render AI-generated JSON into markdown through schema-checked Jinja2 templates.",
    no_args_is_help=True,
    add_completion=False,
)

DocArg = Annotated[Path, typer.Argument(help="Document definition (.sd file).", exists=True, dir_okay=False)]
ConfigOpt = Annotated[
    Path | None,
    typer.Option("--config", help="Config file (default: nearest slotdown.toml or pyproject.toml)."),
]


def _fail(lines: list[str] | str, header: str | None = None) -> NoReturn:
    if header:
        typer.echo(header, err=True)
    for line in [lines] if isinstance(lines, str) else lines:
        typer.echo(f"  {line}" if header else line, err=True)
    raise typer.Exit(1)


def _load(path: Path) -> Document:
    try:
        return load_document(path)
    except SchemaError as exc:
        _fail(exc.problems, f"{path}: schema error")


def _config(path: Path | None) -> Config:
    try:
        return load_config(path)
    except ConfigError as exc:
        _fail(str(exc))


def _read_json(source: str, label: str) -> Any:
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        _fail(f"cannot read {label}: {exc}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        _fail(f"{label} is not valid JSON: {exc}")


@app.command()
def check(doc_path: DocArg, config_path: ConfigOpt = None) -> None:
    """Statically check a document: schema syntax and template/field cross-references."""
    config = _config(config_path)
    doc = _load(doc_path)
    issues = check_document(doc, config)
    for issue in issues:
        typer.echo(f"{doc_path}: {issue}", err=True)

    errors = sum(issue.level == "error" for issue in issues)
    warnings = len(issues) - errors
    if errors or (warnings and config.warnings_as_errors):
        raise typer.Exit(1)
    typer.echo(f"{doc_path}: ok" + (f" ({warnings} warning(s))" if warnings else ""))


@app.command()
def render(
    doc_path: DocArg,
    data: Annotated[
        str, typer.Argument(help="JSON file with the AI-generated fields, or '-' for stdin.")
    ] = "-",
    input_values: Annotated[
        list[str] | None,
        typer.Option(
            "--input",
            "-i",
            help="Input value as key=value (repeatable). Use key=@path to read the value from a file.",
        ),
    ] = None,
    inputs_file: Annotated[str | None, typer.Option("--inputs", help="JSON file with input values.")] = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write markdown here instead of stdout.")
    ] = None,
    config_path: ConfigOpt = None,
) -> None:
    """Validate generated JSON against the schema and render it to markdown."""
    config = _config(config_path)
    doc = _load(doc_path)

    errors = [issue for issue in check_document(doc, config) if issue.level == "error"]
    if errors:
        _fail([str(issue) for issue in errors], f"{doc_path}: template check failed")

    inputs: dict[str, Any] = {}
    if inputs_file is not None:
        loaded = _read_json(inputs_file, "inputs file")
        if not isinstance(loaded, dict):
            _fail("inputs file must contain a JSON object")
        inputs.update(loaded)
    for item in input_values or []:
        key, value = _parse_input(item, doc)
        inputs[key] = value

    fields = _read_json(data, "data")
    try:
        markdown = render_document(doc, fields, inputs, config)
    except DataError as exc:
        _fail(exc.problems, f"invalid {exc.source}:")
    except RenderError as exc:
        _fail(str(exc))

    if output is None:
        typer.echo(markdown, nl=False)
    else:
        output.write_text(markdown, encoding="utf-8")


@app.command()
def schema(doc_path: DocArg) -> None:
    """Print the JSON Schema the AI's output must satisfy (instructions included)."""
    doc = _load(doc_path)
    json_schema = build_fields_model(doc.schema).model_json_schema(by_alias=True)
    json_schema["title"] = doc.schema.name
    description = "\n\n".join(
        part.strip() for part in (doc.schema.description, doc.schema.instructions) if part
    )
    if description:
        json_schema["description"] = description
    typer.echo(json.dumps(json_schema, ensure_ascii=False, indent=2))


def _parse_input(item: str, doc: Document) -> tuple[str, Any]:
    key, sep, raw = item.partition("=")
    if not sep:
        _fail(f"--input expects key=value, got {item!r}")
    if raw.startswith("@"):
        try:
            raw = Path(raw[1:]).read_text(encoding="utf-8")
        except OSError as exc:
            _fail(f"--input {key}: {exc}")
    spec = doc.schema.inputs.get(key)
    # Strings are taken verbatim; other types are parsed as JSON (`3`, `true`, `["a"]`).
    if spec is None or spec.type == "string" or (spec.type == "enum" and raw in (spec.values or [])):
        return key, raw
    try:
        return key, json.loads(raw)
    except json.JSONDecodeError:
        if spec.type == "enum":
            return key, raw
        _fail(f"--input {key}: value for type '{spec.type}' must be JSON, got {raw!r}")


def main() -> None:
    app()
