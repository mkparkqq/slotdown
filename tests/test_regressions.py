"""Regression tests for edge cases found in review."""

import json

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from slotdown.check import check_document
from slotdown.cli import app
from slotdown.loader import parse_document
from slotdown.models import build_fields_model
from slotdown.render import RenderError, render_document
from slotdown.schema import SchemaError

runner = CliRunner()


def sd(fields: str, body: str = "", inputs: str = "") -> str:
    return f"---\nname: t\n{inputs}fields:\n{fields}---\n{body}"


def errors(text: str) -> list[str]:
    return [str(i) for i in check_document(parse_document(text)) if i.level == "error"]


def test_runtime_error_in_template_becomes_render_error():
    doc = parse_document(
        sd("  qs: { type: list, optional: true, items: { type: string } }\n", "{{ qs | length }}")
    )
    with pytest.raises(RenderError, match="TypeError"):
        render_document(doc, {"qs": None})


def test_undefined_namespace_ref_does_not_crash():
    assert errors(sd("  a: { type: string }\n", "{% set ns.x = 1 %}{{ a }}")) == [
        "error: line 6: 'ns' is not defined in fields or inputs"
    ]


@pytest.mark.parametrize(
    "tag", ['{% include "x.md" %}', '{% import "x" as m %}', '{% from "x" import m %}', '{% extends "x" %}']
)
def test_external_templates_are_errors(tag):
    found = errors(sd("  a: { type: string }\n", tag + "{{ a }}"))
    assert len(found) == 1 and "cannot load other files" in found[0]


def test_list_item_constraints_and_descriptions_apply():
    doc = parse_document(
        sd(
            "  grid: { type: list, items: { type: list, min_items: 2, max_items: 2,\n"
            "                                items: { type: integer } } }\n"
            "  tags: { type: list, items: { type: string, description: ITEMDESC } }\n"
        )
    )
    model = build_fields_model(doc.schema)
    with pytest.raises(ValidationError):
        model.model_validate({"tags": ["x"], "grid": [[1]]})
    schema = json.dumps(model.model_json_schema(by_alias=True))
    assert "ITEMDESC" in schema and "minItems" in schema


def test_yaml_yes_no_and_dates_stay_strings():
    doc = parse_document(
        sd("  yn: { type: enum, values: [yes, no] }\n  d: { type: enum, values: [2024-01-01] }\n")
    )
    assert doc.schema.fields["yn"].values == ["yes", "no"]
    assert doc.schema.fields["d"].values == ["2024-01-01"]
    build_fields_model(doc.schema).model_validate({"yn": "yes", "d": "2024-01-01"})


def test_indented_delimiter_does_not_close_front_matter():
    doc = parse_document(
        "---\nname: t\ninstructions: |\n  a\n  ---\n  b\nfields:\n  a: { type: string }\n---\n{{ a }}"
    )
    assert "---" in doc.schema.instructions
    assert doc.template == "{{ a }}"


def test_utf8_bom_is_accepted():
    assert parse_document("﻿" + sd("  a: { type: string }\n")).schema.name == "t"


@pytest.mark.parametrize("name", ["self", "none", "loop", "not", "if"])
def test_jinja_reserved_field_names_rejected(name):
    with pytest.raises(SchemaError, match="reserved in Jinja"):
        parse_document(sd(f"  {name}: {{ type: string }}\n"))


def test_field_name_with_trailing_newline_rejected():
    with pytest.raises(SchemaError, match="invalid field name"):
        parse_document(sd('  "a\\n": { type: string }\n'))


def test_enum_input_value_that_looks_like_json(tmp_path):
    path = tmp_path / "d.sd"
    path.write_text(
        sd(
            "  a: { type: string }\n",
            "{{ a }} {{ inputs.year }} {{ inputs.ok }}\n",
            'inputs:\n  year: { type: enum, values: ["2024"] }\n  ok: { type: enum, values: ["true"] }\n',
        )
    )
    result = runner.invoke(
        app, ["render", str(path), "-", "-i", "year=2024", "-i", "ok=true"], input='{"a": "v"}'
    )
    assert result.exit_code == 0, result.output
    assert result.output == "v 2024 true\n"


def test_number_keeps_ints_and_rejects_nan():
    doc = parse_document(sd("  n: { type: number }\n", "{{ n }}"))
    assert render_document(doc, {"n": 3}) == "3\n"
    assert render_document(doc, {"n": 2.5}) == "2.5\n"
    with pytest.raises(Exception, match="finite"):
        render_document(doc, {"n": float("nan")})


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("{{ s.foo }}", "'s' is a string; it has no attribute 'foo'"),
        ("{{ xs.title }}", "'xs' is a list; it has no attribute 'title'"),
        ("{{ xs['title'] }}", "'xs' is a list; it has no field 'title'"),
        ("{{ o[0] }}", "'o' is an object; index it by field name, not 0"),
    ],
)
def test_invalid_access_on_typed_values(body, message):
    fields = (
        "  s: { type: string }\n"
        "  xs: { type: list, items: { type: object, fields: { title: { type: string } } } }\n"
        "  o: { type: object, fields: { a: { type: string } } }\n"
    )
    found = errors(sd(fields, body))
    assert len(found) == 1 and message in found[0], found


def test_methods_on_values_are_allowed():
    fields = "  s: { type: string }\n  xs: { type: list, items: { type: string } }\n"
    assert errors(sd(fields, "{{ s.upper() }}{{ xs.count('a') }}{{ s[0] }}")) == []
