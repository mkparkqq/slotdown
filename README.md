# slotdown

AI가 생성하는 마크다운 문서의 구조와 품질을 일관되게 유지하는 템플릿 엔진.
**배치는 템플릿이, 내용은 AI가** 맡는다.

1. 사람이 `.sd` 파일에 문서 스키마(YAML 프런트매터)와 Jinja2 템플릿(본문)을 작성한다.
2. AI는 스키마를 읽고 `fields`에 맞는 JSON만 생성한다.
3. `slotdown render`가 JSON을 검증하고 템플릿으로 마크다운을 만든다.

## 설치

```sh
uv sync
uv run slotdown --help
```

## 사용법

```sh
# 정적 검증: 스키마 문법, 템플릿이 참조하는 변수/필드 존재 여부, 미사용 필드 경고
slotdown check examples/weekly_report.sd

# AI에게 넘길 JSON Schema (문서 description + instructions 포함)
slotdown schema examples/weekly_report.sd

# 렌더링: data 생략 또는 '-' 이면 stdin에서 읽는다
slotdown render examples/weekly_report.sd examples/weekly_report.json \
  -i team_name=플랫폼팀 -i raw_notes=@notes.txt -o report.md
```

- `-i key=value`: 입력값 (반복 가능). `key=@path`는 파일 내용을 값으로 쓴다.
  `string`이 아닌 입력은 JSON으로 해석한다 (`-i count=3`, `-i tags='["a"]'`).
- `--inputs inputs.json`: 입력값을 JSON 객체로 한 번에 지정.
- 검증 실패 시 `risks.0.title: Field required`처럼 경로별 오류를 stderr로 출력하고 exit 1.
  AI 에이전트가 오류를 읽고 JSON을 고쳐 다시 호출할 수 있다.

## `.sd` 형식

```yaml
---
name: weekly_report
description: 팀 주간 업무 보고서
instructions: |            # 문서 전체 공통 지시
  경영진이 읽는 보고서다. 한국어, 간결한 문어체로 작성한다.
inputs:                    # 사용자가 넣는 값. 템플릿에서는 inputs.<name>
  team_name: { type: string }
fields:                    # AI가 생성하는 값. 템플릿에서는 최상위 변수
  summary: { type: string, description: 핵심 성과 3문장 요약 }
  risks:
    type: list
    max_items: 5
    items:
      type: object
      fields:
        title: { type: string }
        mitigation: { type: string }
  open_questions: { type: list, optional: true, items: { type: string } }
---
# {{ inputs.team_name }} 주간 보고
{{ summary }}
{% for r in risks %}- {{ r.title }}: {{ r.mitigation }}
{% endfor %}
{% if open_questions %}## 미해결 사항 ...{% endif %}
```

- 타입: `string`, `integer`, `number`, `boolean`, `enum`(`values`), `list`(`items`, `min_items`, `max_items`), `object`(`fields`)
- 공통 속성: `description`, `optional`(기본 false), `examples`
- 검증은 strict: 타입 변환 없음(`"3"`은 integer가 아님), 스키마에 없는 키는 오류.
- `optional` 필드는 `null`이거나 생략 가능 → 템플릿의 `{% if %}`로 섹션을 생략한다.
- 필드 이름은 `items`, `keys`, `values`, `get` 등 dict 메서드와 겹칠 수 없다 (템플릿에서 `obj.items`가 메서드로 해석되므로).
- 미사용 경고는 `fields`에만 적용된다. `inputs`는 AI에게 맥락만 주는 용도(예: 원문 메모)일 수 있으므로 경고하지 않는다. 단, 선언된 필수 `inputs`는 렌더링 시에도 넘겨야 한다.

## 설정

`slotdown.toml` 또는 `pyproject.toml`의 `[tool.slotdown]` (현재 디렉터리부터 상위로 탐색, `--config`로 지정 가능):

```toml
trim_blocks = true            # 블록 태그 뒤 개행 제거
lstrip_blocks = true          # 블록 태그 앞 공백 제거
collapse_blank_lines = true   # 3줄 이상 연속 개행을 빈 줄 하나로
warnings_as_errors = false    # check에서 경고도 실패 처리
```

## 개발

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

테스트는 AI 없이 `tests/fixtures/*.json`의 고정 데이터를 렌더링해 `*.md` 골든 파일과 비교한다.
