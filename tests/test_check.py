from pathlib import Path

from slotdown.check import check_document
from slotdown.loader import load_document, parse_document

FIXTURES = Path(__file__).parent / "fixtures"

FRONT = """\
---
name: t
inputs:
  team: { type: string }
fields:
  summary: { type: string }
  risks:
    type: list
    items:
      type: object
      fields:
        title: { type: string }
        detail: { type: string }
  extra: { type: string, optional: true }
---
"""


def issues_for(body: str) -> list[str]:
    return [str(issue) for issue in check_document(parse_document(FRONT + body))]


def test_example_document_is_clean():
    assert check_document(load_document(FIXTURES / "weekly_report.sd")) == []


def test_all_fields_used_is_clean():
    body = (
        "{{ inputs.team }} {{ summary }}"
        "{% for r in risks %}{{ r.title }}{{ r.detail }}{% endfor %}{{ extra }}"
    )
    assert issues_for(body) == []


def test_undefined_variable_is_error_with_file_line():
    issues = issues_for("{{ summary }}\n{{ risks }}{{ extra }}\n{{ nope }}")
    # template line 3 + 15 front matter lines
    assert issues == ["error: line 18: 'nope' is not defined in fields or inputs"]


def test_unknown_input_is_error():
    issues = issues_for("{{ summary }}{{ risks }}{{ extra }}{{ inputs.missing }}")
    assert issues == ["error: line 16: inputs has no field 'missing'"]


def test_unknown_nested_attribute_is_error():
    issues = issues_for(
        "{{ summary }}{{ extra }}{% for r in risks %}{{ r.title }}{{ r.detail }}{{ r.owner }}{% endfor %}"
    )
    assert issues == ["error: line 16: 'risks[]' has no field 'owner'"]


def test_unused_fields_are_warnings():
    issues = issues_for("{{ summary }}")
    assert issues == [
        "warning: 'risks' is defined but never used in the template",
        "warning: 'extra' is defined but never used in the template",
    ]


def test_unused_nested_field_is_warning():
    issues = issues_for("{{ summary }}{{ extra }}{% for r in risks %}{{ r.title }}{% endfor %}")
    assert issues == ["warning: 'risks[].detail' is defined but never used in the template"]


def test_condition_alone_does_not_count_children_as_used():
    issues = issues_for("{{ summary }}{{ extra }}{% if risks %}x{% endif %}")
    assert issues == [
        "warning: 'risks[].title' is defined but never used in the template",
        "warning: 'risks[].detail' is defined but never used in the template",
    ]


def test_whole_value_usage_counts_children():
    assert issues_for("{{ summary }}{{ extra }}{{ risks | tojson }}") == []


def test_list_preserving_filter_keeps_item_resolution():
    body = (
        "{{ summary }}{{ extra }}"
        "{% for r in risks | sort(attribute='title') %}{{ r.title }}{{ r.nope }}{% endfor %}"
    )
    assert issues_for(body) == [
        "error: line 16: 'risks[]' has no field 'nope'",
        "warning: 'risks[].detail' is defined but never used in the template",
    ]


def test_loop_variable_shadows_field():
    body = "{{ extra }}{% for summary in risks %}{{ summary.title }}{{ summary.detail }}{% endfor %}"
    assert issues_for(body) == ["warning: 'summary' is defined but never used in the template"]


def test_set_variables_and_builtins_are_not_errors():
    body = (
        "{% set x = summary %}{{ x }}{{ range(3) | list }}{{ risks }}{{ extra }}"
        "{% for r in risks %}{{ loop.index }}{% endfor %}"
    )
    assert issues_for(body) == []


def test_template_syntax_error():
    issues = issues_for("{{ summary }}\n{% for x in risks %}")
    assert len(issues) == 1
    assert issues[0].startswith("error: line 17: template syntax error")
