import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from slotdown.check import check_document
from slotdown.cli import app
from slotdown.config import Config, ConfigError, load_config
from slotdown.loader import load_document, parse_document
from slotdown.render import DataError, render_document

FIXTURES = Path(__file__).parent / "fixtures"
DOC = FIXTURES / "weekly_report.sd"
INPUTS = {"team_name": "플랫폼팀", "raw_notes": "-"}
runner = CliRunner()


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", ["full", "minimal"])
def test_render_matches_golden(case):
    # Predefined JSON stands in for the AI's output, so rendering is deterministic.
    output = render_document(load_document(DOC), load_json(f"weekly_report.{case}.json"), INPUTS)
    assert output == (FIXTURES / f"weekly_report.{case}.md").read_text(encoding="utf-8")


def test_optional_section_omitted_when_null_or_missing():
    data = load_json("weekly_report.minimal.json")
    del data["open_questions"]
    output = render_document(load_document(DOC), data, INPUTS)
    assert "미해결 사항" not in output


def test_invalid_fields_report_paths():
    data = load_json("weekly_report.full.json")
    data["risks"] = [{"title": "x"}] * 6
    with pytest.raises(DataError) as exc_info:
        render_document(load_document(DOC), data, INPUTS)
    assert exc_info.value.source == "fields"
    assert any(p.startswith("risks: ") for p in exc_info.value.problems)  # max_items 5


def test_missing_inputs_rejected():
    with pytest.raises(DataError) as exc_info:
        render_document(load_document(DOC), load_json("weekly_report.full.json"), {})
    assert exc_info.value.source == "inputs"
    assert "team_name: Field required" in exc_info.value.problems


def test_blank_line_collapsing_is_configurable():
    doc = parse_document("---\nname: t\nfields:\n  a: { type: string }\n---\n{{ a }}\n\n\n\nend\n")
    assert render_document(doc, {"a": "x"}) == "x\n\nend\n"
    assert render_document(doc, {"a": "x"}, config=Config(collapse_blank_lines=False)) == "x\n\n\n\nend\n"


class TestConfig:
    def test_defaults_without_file(self, tmp_path):
        assert load_config(start=tmp_path) == Config()

    def test_reads_slotdown_toml_upward(self, tmp_path):
        (tmp_path / "slotdown.toml").write_text("warnings_as_errors = true\n")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert load_config(start=nested).warnings_as_errors is True

    def test_reads_pyproject_tool_table(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.slotdown]\ntrim_blocks = false\n")
        assert load_config(start=tmp_path).trim_blocks is False

    def test_rejects_unknown_keys(self, tmp_path):
        path = tmp_path / "slotdown.toml"
        path.write_text("colour = true\n")
        with pytest.raises(ConfigError, match="unknown setting"):
            load_config(path)


class TestCli:
    def test_check_ok(self):
        result = runner.invoke(app, ["check", str(DOC)])
        assert result.exit_code == 0
        assert "ok" in result.output

    def test_check_warnings_as_errors(self, tmp_path):
        doc = tmp_path / "d.sd"
        doc.write_text("---\nname: t\nfields:\n  a: { type: string }\n  b: { type: string }\n---\n{{ a }}\n")
        assert runner.invoke(app, ["check", str(doc)]).exit_code == 0
        config = tmp_path / "slotdown.toml"
        config.write_text("warnings_as_errors = true\n")
        result = runner.invoke(app, ["check", str(doc), "--config", str(config)])
        assert result.exit_code == 1
        assert "'b' is defined but never used" in result.output

    def test_check_schema_error(self, tmp_path):
        doc = tmp_path / "d.sd"
        doc.write_text("---\nname: t\nfields:\n  a: { type: text }\n---\n")
        result = runner.invoke(app, ["check", str(doc)])
        assert result.exit_code == 1
        assert "schema error" in result.output

    def test_render_from_stdin_with_file_input(self, tmp_path):
        notes = tmp_path / "notes.txt"
        notes.write_text("메모")
        result = runner.invoke(
            app,
            ["render", str(DOC), "-", "-i", "team_name=플랫폼팀", "-i", f"raw_notes=@{notes}"],
            input=(FIXTURES / "weekly_report.full.json").read_text(encoding="utf-8"),
        )
        assert result.exit_code == 0, result.output
        assert result.output == (FIXTURES / "weekly_report.full.md").read_text(encoding="utf-8")

    def test_render_to_file_with_inputs_json(self, tmp_path):
        inputs = tmp_path / "inputs.json"
        inputs.write_text(json.dumps(INPUTS))
        out = tmp_path / "out.md"
        data = FIXTURES / "weekly_report.minimal.json"
        result = runner.invoke(app, ["render", str(DOC), str(data), "--inputs", str(inputs), "-o", str(out)])
        assert result.exit_code == 0, result.output
        assert out.read_text(encoding="utf-8") == (FIXTURES / "weekly_report.minimal.md").read_text(
            encoding="utf-8"
        )

    def test_render_reports_invalid_json(self):
        result = runner.invoke(
            app, ["render", str(DOC), "-", "-i", "team_name=x", "-i", "raw_notes=y"], input="{not json"
        )
        assert result.exit_code == 1
        assert "not valid JSON" in result.output

    def test_render_reports_validation_errors(self):
        result = runner.invoke(
            app,
            ["render", str(DOC), "-", "-i", "team_name=x", "-i", "raw_notes=y"],
            input='{"summary": 1, "risks": []}',
        )
        assert result.exit_code == 1
        assert "invalid fields:" in result.output
        assert "summary: Input should be a valid string" in result.output

    def test_render_parses_typed_inputs_as_json(self, tmp_path):
        doc = tmp_path / "d.sd"
        doc.write_text(
            "---\nname: t\ninputs:\n  n: { type: integer }\nfields:\n  a: { type: string }\n---\n"
            "{{ a }} x{{ inputs.n + 1 }}\n"
        )
        result = runner.invoke(app, ["render", str(doc), "-", "-i", "n=41"], input='{"a": "v"}')
        assert result.exit_code == 0, result.output
        assert result.output == "v x42\n"

    def test_schema_command(self):
        result = runner.invoke(app, ["schema", str(DOC)])
        assert result.exit_code == 0
        schema = json.loads(result.output)
        assert schema["title"] == "weekly_report"
        assert "경영진" in schema["description"]
        assert schema["required"] == ["summary", "risks"]
        assert schema["properties"]["risks"]["maxItems"] == 5


def test_stock_picks_matches_golden():
    # Real candles output (KODEX 200, 2026-11-13) captured as fixed data.
    doc = load_document(FIXTURES / "stock-picks.sd")
    output = render_document(doc, load_json("stock-picks.json"), {"base_ticker": "069500", "horizon": "long"})
    assert output == (FIXTURES / "stock-picks.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "path", sorted((FIXTURES.parent.parent / "examples").glob("*.sd")), ids=lambda p: p.name
)
def test_examples_pass_check(path):
    assert [str(i) for i in check_document(load_document(path))] == []
