# slotdown 프로젝트 초기화

새 Python 프로젝트 `slotdown`을 초기화해줘.

## 프로젝트 목적

slotdown은 AI가 생성하는 마크다운 문서의 **구조와 품질을 일관되게 유지**하기 위한 템플릿 엔진이다.
핵심 원칙은 "배치는 템플릿이, 내용은 AI가" 맡는 것이다.
AI가 지시에 따라 데이터를 생성한 후, 이 프로그램의 CLI Command를 호출하여 문서로 변환할 수 있다.

1. 사람이 **문서 스키마**로 AI가 채울 내용을 필드 단위로 정의하고, 각 필드에 "무엇을 어떻게 쓸지" 추상적인 지시를 단다.
2. AI(API, 로컬 CLI 에이전트)는 스키마에 맞는 **구조화 데이터(JSON)만** 생성한다. 레이아웃에는 관여하지 않는다.
3. 생성된 데이터를 검증한 뒤 **Jinja2 템플릿**으로 마크다운에 렌더링한다.
   - 위치: 템플릿이 100% 결정
   - 조건부 포함: optional 필드 + `{% if %}` (AI가 null을 반환하면 섹션 생략)
   - 반복: list 필드 + `{% for %}`


문서를 정의하고 생성하는 과정은 아래와 같다.

1. 사용자(사람)이 '문서 스키마'와 'markdown 템플릿' 작성. 
2. 사용자가 AI에게 필요한 데이터와 스키마를 전달한다.
3. AI가 스키마를 읽고 스키마에서 지정한 데이터를 JSON 형태로 생성
4. 사용자(AI 또는 사람)가 cli program을 호출하여 생성된 JSON을 markdown 템플릿에 맞게 markdown 문서로 변환

이 과정에서 AI는 3단계에만 개입하고, 나머지는 프로그램에 의해 진행된다. 


## 기술 스택

- Python 3.11+, 패키지 관리는 `uv`, `pyproject.toml` 기반, `src/` 레이아웃
- Pydantic v2 (스키마 → 모델 변환 및 검증)
- Jinja2 (`StrictUndefined` 사용)
- PyYAML (스키마 파싱), `tomllib` (설정 파일)
- Typer (CLI)
- pytest (테스트), ruff (린트/포맷)


## 문서 스키마 형식 (`*.sd`)

하나의 파일에 YAML 프런트매터(스키마)와 Jinja2 본문(템플릿)을 함께 둔다.

```yaml
---
name: weekly_report
description: 팀 주간 업무 보고서
instructions: |            # 문서 전체에 적용되는 공통 지시 (톤, 언어, 독자 등)
  경영진이 읽는 보고서다. 한국어, 간결한 문어체로 작성한다.
inputs:                    # 사용자가 직접 넣는 값 (AI가 생성하지 않음)
  team_name: { type: string }
  raw_notes: { type: string, description: 이번 주 업무 메모 원문 }
fields:                    # AI가 생성할 값
  summary:
    type: string
    description: 핵심 성과를 수치 포함 3문장으로 요약
  risks:
    type: list
    max_items: 5
    description: 중요도 순 리스크
    items:
      type: object
      fields:
        title: { type: string, description: 10자 이내 리스크 이름 }
        mitigation: { type: string, description: 구체적 완화 방안 1~2문장 }
  open_questions:
    type: list
    optional: true
    description: 미해결 사항. 없으면 null
    items: { type: string }
---
# {{ inputs.team_name }} 주간 보고

## 요약
{{ summary }}

## 리스크
{% for r in risks %}
### {{ loop.index }}. {{ r.title }}
{{ r.mitigation }}
{% endfor %}

{% if open_questions %}
## 미해결 사항
{% for q in open_questions %}- {{ q }}
{% endfor %}
{% endif %}
```

지원 타입: `string`, `integer`, `number`, `boolean`, `enum`(`values` 지정), `list`(`items`, `min_items`, `max_items`), `object`(`fields`).
공통 속성: `description`, `optional`(기본 false), `examples`(선택).

## 구현할 기능

1. **스키마 로더**: `.sd` 파일을 파싱해 프런트매터와 템플릿 본문을 분리하고, `fields` 정의로 Pydantic 모델을 동적으로 생성한다.

2. **정적 검증** (`slotdown check`):
   - 스키마 문법 오류
   - 템플릿에서 참조하는 변수가 `fields`/`inputs`에 정의되어 있는지 (Jinja2 AST 분석)
   - 정의했지만 템플릿에서 쓰이지 않는 필드 경고

3. JSON을 markdown 문서로 변환
    - 테스트시에는 AI 개입 없이 사전에 지정된 데이터를 JSON에 삽입
