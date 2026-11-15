import pytest
from pydantic import ValidationError

from slotdown.loader import parse_document, split_front_matter
from slotdown.models import build_fields_model
from slotdown.schema import SchemaError, parse_schema


def doc(front: str, body: str = "") -> str:
    return f"---\n{front}\n---\n{body}"


class TestFrontMatter:
    def test_splits_schema_and_template(self):
        front, body, offset = split_front_matter("---\nname: x\n---\n# {{ a }}\n")
        assert front == "name: x\n"
        assert body == "# {{ a }}\n"
        assert offset == 3

    def test_requires_opening_delimiter(self):
        with pytest.raises(SchemaError, match="must start with"):
            split_front_matter("name: x\n")

    def test_requires_closing_delimiter(self):
        with pytest.raises(SchemaError, match="not closed"):
            split_front_matter("---\nname: x\n")

    def test_invalid_yaml(self):
        with pytest.raises(SchemaError, match="invalid YAML"):
            parse_document(doc("name: [unclosed"))


class TestParseSchema:
    def test_parses_nested_fields(self):
        schema = parse_schema(
            {
                "name": "n",
                "inputs": {"who": {"type": "string"}},
                "fields": {
                    "tags": {"type": "list", "max_items": 3, "items": {"type": "string"}},
                    "meta": {"type": "object", "fields": {"level": {"type": "enum", "values": ["a", "b"]}}},
                },
            }
        )
        assert schema.inputs["who"].type == "string"
        assert schema.fields["tags"].items.type == "string"
        assert schema.fields["tags"].max_items == 3
        assert schema.fields["meta"].fields["level"].values == ["a", "b"]

    def test_collects_all_problems(self):
        with pytest.raises(SchemaError) as exc_info:
            parse_schema(
                {
                    "name": "n",
                    "bogus": 1,
                    "fields": {
                        "a": {"type": "text"},
                        "b": {"type": "enum"},
                        "c": {"type": "list"},
                        "d": {"type": "object", "fields": {}},
                        "e": {"type": "string", "max_items": 3},
                        "f": {"type": "list", "items": {"type": "string"}, "min_items": 3, "max_items": 1},
                        "1bad": {"type": "string"},
                        "items": {"type": "string"},
                    },
                }
            )
        problems = "\n".join(exc_info.value.problems)
        for expected in (
            "unknown top-level key 'bogus'",
            "fields.a: 'type' must be one of",
            "fields.b: enum requires",
            "fields.c: list requires 'items'",
            "fields.d: object requires",
            "fields.e: key 'max_items' is not valid for type 'string'",
            "fields.f: 'min_items' is greater than 'max_items'",
            "fields.1bad: invalid field name",
            "fields.items: 'items' collides with a dict method",
        ):
            assert expected in problems

    def test_name_and_fields_required(self):
        with pytest.raises(SchemaError) as exc_info:
            parse_schema({})
        assert any("'name' is required" in p for p in exc_info.value.problems)
        assert any("'fields' is required" in p for p in exc_info.value.problems)

    def test_inputs_is_reserved_field_name(self):
        with pytest.raises(SchemaError, match="reserved"):
            parse_schema({"name": "n", "fields": {"inputs": {"type": "string"}}})


class TestFieldsModel:
    @pytest.fixture
    def model(self):
        schema = parse_schema(
            {
                "name": "n",
                "fields": {
                    "title": {"type": "string"},
                    "count": {"type": "integer"},
                    "score": {"type": "number"},
                    "done": {"type": "boolean"},
                    "level": {"type": "enum", "values": ["low", "high"]},
                    "items_": {
                        "type": "list",
                        "min_items": 1,
                        "max_items": 2,
                        "items": {"type": "object", "fields": {"json": {"type": "string"}}},
                    },
                    "note": {"type": "string", "optional": True},
                },
            }
        )
        return build_fields_model(schema)

    VALID = {
        "title": "t",
        "count": 1,
        "score": 1,
        "done": True,
        "level": "low",
        "items_": [{"json": "shadowing BaseModel.json is fine"}],
    }

    def test_valid_data_round_trips_with_original_names(self, model):
        dumped = model.model_validate(self.VALID).model_dump(by_alias=True)
        assert dumped == {**self.VALID, "note": None}

    @pytest.mark.parametrize(
        ("patch", "loc"),
        [
            ({"count": "1"}, ("count",)),  # strict: no string -> int coercion
            ({"done": 1}, ("done",)),
            ({"level": "mid"}, ("level",)),
            ({"items_": []}, ("items_",)),
            ({"items_": [{"json": "a"}] * 3}, ("items_",)),
            ({"items_": [{}]}, ("items_", 0, "json")),
            ({"unknown": 1}, ("unknown",)),
        ],
    )
    def test_rejects_invalid_data(self, model, patch, loc):
        with pytest.raises(ValidationError) as exc_info:
            model.model_validate({**self.VALID, **patch})
        assert exc_info.value.errors()[0]["loc"] == loc

    def test_optional_accepts_null(self, model):
        assert model.model_validate({**self.VALID, "note": None}).model_dump(by_alias=True)["note"] is None

    def test_required_rejects_null(self, model):
        with pytest.raises(ValidationError):
            model.model_validate({**self.VALID, "title": None})
