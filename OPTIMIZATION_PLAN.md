# Prompt Engineering Guide

> Ollama + Local Model 기반 에이전트의 프롬프트 작성·개선·평가 가이드.
> OpenAI, Anthropic 등 주요 프로바이더 문서 및 학술 연구 참고.
> 지속 업데이트.

---

## 목차

1. [핵심 원칙](#1-핵심-원칙)
2. [프롬프트 구조 설계](#2-프롬프트-구조-설계)
3. [역할 & 페르소나](#3-역할--페르소나)
4. [Few-shot & 예시 전략](#4-few-shot--예시-전략)
5. [추론 전략](#5-추론-전략)
6. [출력 형식 제어](#6-출력-형식-제어)
7. [코드 생성 프롬프트](#7-코드-생성-프롬프트)
8. [에이전트 & 도구 사용 프롬프트](#8-에이전트--도구-사용-프롬프트)
9. [컨텍스트 관리](#9-컨텍스트-관리)
10. [실패 패턴 & 대응](#10-실패-패턴--대응)
11. [평가 & 반복 개선](#11-평가--반복-개선)
12. [Local Model 특화 기법](#12-local-model-특화-기법)
13. [참고 자료](#13-참고-자료)
14. [변경 이력](#14-변경-이력)

---

## 1. 핵심 원칙

### 1.1 명시적으로 써라

모델은 의도를 추측하지 못한다. 원하는 것을 정확히 서술.

```
❌ "이 데이터 정리해줘"
✅ "CSV에서 null 행 제거, date 컬럼을 ISO-8601로 변환, department 기준 오름차순 정렬하여 cleaned.csv로 저장"
```

- 작업 범위, 출력 형식, 언어 모두 명시
- 생략 = 모델이 임의 결정 = 비결정적 결과

> **OpenAI**: "Include details in your query to get more relevant answers"

### 1.2 엣지 케이스를 정의하라

비정상 입력에 대한 행동 지침을 미리 제공.

```
입력이 빈 문자열이면 {"error": "empty input"} 반환.
숫자가 아닌 값이 포함되면 해당 행을 건너뛰고 warnings에 기록.
파일이 없으면 도구를 호출하지 말고 사용자에게 경로 확인 요청.
```

정의 안 된 엣지 케이스 = 매번 다른 처리 = 비결정적 결과.

### 1.3 제약을 먼저, 자유를 나중에

규칙·금지사항·형식 제약 → 프롬프트 앞부분.
자유도 높은 지시 → 뒷부분.

모델은 앞부분 제약을 더 잘 지킨다 (primacy bias).

### 1.4 "하지 마"보다 "대신 이걸 해"

```
❌ "open() 쓰지 마"
✅ "파일 저장이 필요하면 open() 대신 suggested_file 딕셔너리로 반환해"
```

금지만 하면 모델이 대안 못 찾아 멈추거나 다른 위반 생성.
금지 + 대안을 항상 쌍으로.

### 1.5 한 번에 하나의 역할

한 프롬프트에 여러 역할(분석가 + 코드 생성기 + 리뷰어) → 품질 하락.
역할 분리, 필요하면 prompt chaining으로 연결.

> **Anthropic**: "Break complex tasks into subtasks. Each step: simpler prompt, easier to debug, higher accuracy"

---

## 2. 프롬프트 구조 설계

### 2.1 섹션 구분자 사용

구분자 없는 긴 텍스트 → 모델이 섹션 경계 혼동.

**XML 태그** (구조화된 multi-section에 최적):
```xml
<role>Python 코드 생성기</role>
<rules>허용 import: json, csv, re ...</rules>
<output_format>```python 코드 블록만 출력</output_format>
```

**마크다운 헤더** (가독성 중시):
```markdown
## Role
Python 코드 생성기

## Rules
허용 import: json, csv, re ...
```

| 방식 | 장점 | 적합한 경우 |
|------|------|------------|
| XML 태그 | 경계 인식 정확, 중첩 가능 | 규칙 많은 system prompt |
| 마크다운 | 사람이 읽기 쉬움 | user message, 짧은 지시 |
| 혼합 | 양쪽 장점 | XML로 대분류, 마크다운으로 내부 |

### 2.2 프롬프트 레이아웃 순서

```
1. 역할/정체성 (Identity)
2. 핵심 규칙 & 제약 (Rules)
3. 금지 사항 (Forbidden)
4. 출력 형식 (Output Format)
5. 예시 (Examples)
6. 동적 컨텍스트 (도구 목록, workspace 등)
7. 사용자 입력
```

**이유**:
- 역할이 먼저 → 이후 지시를 해당 역할 관점에서 해석
- 규칙을 앞에 → primacy bias 활용
- 동적 입력을 마지막에 → prompt caching 효율 극대화

> 정적 콘텐츠를 앞에, 동적 콘텐츠를 뒤에 → 동일 prefix 반복 시 캐싱 가능

### 2.3 규칙 넘버링 & 그룹화

규칙 5개 이상 → 번호. 10개 이상 → 그룹 분류.

```xml
<rules>
## 규칙 (번호가 작을수록 우선)

### 흐름 제어
1. 작업 완료 시 텍스트로 응답
2. 질문에는 설명으로 응답
3. 데이터 처리는 반드시 도구 사용

### 도구 사용
4. 파일 작업은 read_file 선행 필수
5. 기존 도구 재사용 우선
6. 실패 시 repair_tool

### 검증
7. 도구 결과 검증 후 진행
8. 3회 실패 시 접근 전환
</rules>
```

**왜 그룹화?**: 규칙 10개+ → 중간 규칙 무시 빈도 증가 (Lost-in-the-Middle). 그룹화하면 각 그룹 첫 번째 규칙이 anchor 역할.

---

## 3. 역할 & 페르소나

### 3.1 정체성 설정

```
❌ "당신은 도움이 되는 AI입니다"
✅ "당신은 Python 코드 생성기입니다. 주어진 스펙에 맞는 def run(input: dict) -> dict 함수를 생성합니다."
```

구체적 역할 → 해당 역할에 맞지 않는 행동 자연스럽게 억제:
- "코드 생성기" → 설명 줄이고 코드 집중
- "의사결정자" → 실행 않고 계획/선택 집중

### 3.2 Multi-Agent 역할 분리

각 LLM 호출에 다른 역할 부여:

| 호출 | 역할 | 출력 형식 |
|------|------|----------|
| Planner | 의사결정자 | JSON (도구 호출) or 텍스트 |
| Builder Manifest | 스키마 설계자 | JSON (도구 스펙) |
| Builder Code | 코드 생성기 | Python 코드 블록 |
| Repair | 코드 수정자 | Python 코드 블록 |

**핵심**: 한 호출에 여러 역할 혼합 금지. 출력 형식 충돌, 품질 저하.

---

## 4. Few-shot & 예시 전략

### 4.1 예시의 효과

Few-shot = 가장 신뢰도 높은 기법:
- 형식 준수율 대폭 향상
- 엣지 케이스 처리 방법 암묵적 전달
- "무엇을" + "어떻게" 동시 학습

> **Anthropic**: "3-5 examples usually sufficient"

### 4.2 예시 구성법

```xml
<examples>
<!-- 표준 케이스 -->
입력: "sample_data.csv 분석해줘"
출력: {"tool": "read_file", "input": {"path": "sample_data.csv"}}

<!-- 엣지 케이스 -->
입력: "이 데이터 분석해줘" (workspace에 후보 파일 없음)
출력: {"tool": "ask_user", "input": {"question": "어떤 파일을 분석할까요?"}}

<!-- 기존 도구 재사용 -->
입력: "10개만 가져와" (기존 도구 news_fetcher 있음)
출력: {"tool": "news_fetcher", "input": {"count": 10}}
</examples>
```

### 4.3 Negative Example

반복 실패 패턴에 가장 효과적인 교정법.

```
입력: "이 데이터를 분석해줘" (파일 미지정)
❌ "데이터를 분석하려면 파일이 필요합니다."
   → 왜 잘못?: 텍스트로 질문하면 안 됨. ask_user 도구 사용해야 함.
✅ {"tool": "ask_user", "input": {"question": "어떤 파일을 분석할까요?"}}
```

> **OpenAI**: "Negative examples are very effective for reducing specific failure patterns"

### 4.4 예시 수와 순서

| 상황 | 권장 수 |
|------|---------|
| 형식 학습 | 2-3개 |
| 분류/판단 | 3-5개 |
| 복잡한 로직 | 5-8개 |

**순서 효과**: 마지막 예시가 가장 큰 영향 (recency bias).
- 표준 동작 강화 → 일반 케이스를 마지막에
- 특정 패턴 교정 → 문제 케이스를 마지막에

---

## 5. 추론 전략

### 5.1 Zero-shot CoT

```
문제를 풀기 전에 단계별로 생각하세요.
```

- 다단계 추론 태스크에서 10-40% 정확도 향상
- 구현 비용 0. 추론 필요한 태스크에 기본 적용 권장.

### 5.2 Self-Verification

모델이 자기 답변을 검증:

```
답변 후 아래 체크리스트로 자가 검증:
- [ ] 모든 입력 필드를 처리했는가?
- [ ] 출력 형식이 스펙과 일치하는가?
- [ ] 엣지 케이스(빈 입력, null)를 처리했는가?
검증 실패 시 수정하여 최종 답변만 출력.
```

### 5.3 언제 CoT를 쓰지 말아야 하는가

- **단순 분류/추출**: 과잉 사고 → 오답
- **형식 변환**: 매핑이 명확하면 불필요
- **Constrained decoding**: JSON Schema 강제 시 추론 넣을 여지 없음

---

## 6. 출력 형식 제어

### 6.1 JSON 출력

**프롬프트에 스키마 명시**:
```
아래 JSON 스키마에 맞춰 출력. JSON만 출력. 설명 텍스트 금지.

{
  "name": "string",
  "description": "string",
  "input_schema": {"type": "object", "properties": {...}, "required": [...]},
  "tags": ["string"]
}
```

**Constrained Decoding (가장 안정적)**:
Ollama `format` 파라미터로 JSON Schema 전달 → 구조 100% 보장.
스키마 강제가 가능한 경우 항상 사용.

### 6.2 코드 블록 출력

````
반드시 ```python 코드 블록 안에 출력.
코드 블록 밖에 설명, 주석, 텍스트 포함 금지.
코드 블록은 정확히 1개만.
````

**강화 패턴** (코드 추출 실패 빈도가 높을 때):
````
출력 규칙:
1. ```python 으로 시작
2. 코드 내용
3. ``` 으로 끝
4. 위 외에 어떤 텍스트도 출력 금지
````

### 6.3 형식 혼합 방지

```
도구 호출 시: JSON만 출력. 텍스트 섞지 마.
텍스트 응답 시: 일반 텍스트만. JSON 섞지 마.
```

한 응답에 여러 형식 혼합 → 파싱 실패. 형식 하나로 강제.

---

## 7. 코드 생성 프롬프트

### 7.1 함수 시그니처 명시

인터페이스를 정확히 정의:

```
def run(input: dict) -> dict:
    """
    입력: {"data": list[dict], "threshold": float}
    출력: {"result": list[dict], "count": int}
    data에서 score가 threshold 이상인 항목만 필터링.
    """
```

### 7.2 허용/금지 목록

```
허용 import: json, csv, re, datetime, statistics, pathlib
금지: os, subprocess, requests, open(), eval(), exec()
금지 대안: 파일 저장 → suggested_file 딕셔너리 반환
```

화이트리스트(허용)가 블랙리스트(금지)보다 안전. 둘 다 쓰는 게 최선.

### 7.3 실제 데이터 제공

> **OpenAI**: "참조 텍스트 제공 시 환각 현저히 감소"

```
## 실행 시 전달될 input
{"data": [{"name": "Alice", "score": 85}, {"name": "Bob", "score": 42}], "threshold": 60}

이 데이터를 실제로 처리하세요. 가짜 데이터 하드코딩 금지.
```

효과:
- 데이터 구조 추론 불필요 → 정확도 향상
- 키 이름 오타 감소
- 하드코딩 유혹 차단

### 7.4 유사 코드 참조

```
## 참고: 유사한 기존 코드
아래 코드의 패턴을 참고하세요:
```python
def run(input: dict) -> dict:
    ...
```
```

기존 코드 = 암묵적 few-shot. 코딩 컨벤션, 에러 처리 패턴 자연스럽게 전달.

### 7.5 2-Stage Generation

코드 생성에서 JSON+코드 혼합 출력 → 파싱 실패.

해결: 2단계 분리:
```
Stage 1: 스키마 생성 (JSON, constrained decoding)
Stage 2: 코드 생성 (free-form, 코드 블록만)
```

이점:
- JSON 안 코드 escape 문제 제거
- 각 단계 독립 디버깅
- Stage 1 검증 후 Stage 2 진행 가능

---

## 8. 에이전트 & 도구 사용 프롬프트

### 8.1 도구 설명 작성법

도구 설명 > 파라미터 이름.

```
❌ - read_file: 파일 읽기. path(string)

✅ - read_file: 로컬 파일 내용을 텍스트로 읽어 반환.
     CSV, JSON, 텍스트 파일 처리 전 반드시 먼저 호출.
     파라미터: path(string) — 작업 디렉터리 내 상대 경로
     제한: 바이너리 불가, 최대 1MB
     불필요: 이미 _data에 내용 있을 때
```

포함할 것:
- **언제 사용** / **언제 불필요**
- **제한** 사항
- **파라미터**: 타입 + 제약

> **OpenAI**: "Descriptions matter more than parameter names"

### 8.2 도구 선택 규칙

Decision tree 형태가 나열보다 효과적:

```
도구 선택:
1. 파일 내용 필요 → read_file 먼저
2. 데이터 처리/계산 → generate_code
3. 기존 도구 + 파라미터 변경으로 해결 가능 → 기존 도구 재사용
4. 정보 부족 → ask_user
5. 개념 질문 → 텍스트 응답
```

### 8.3 에러 복구 지침

```
도구 실행 실패 시:
1. 에러 메시지 읽고 원인 파악
2. 동일 입력으로 재시도 금지
3. 파라미터 수정하여 재시도
4. 3회 실패 → 사용자에게 보고
```

복구 전략 미명시 → 모델이 같은 실패 무한 반복 or 즉시 포기.

### 8.4 루프 제어

```
최대 N단계까지 실행.
매 도구 사용 후 목표 달성 여부 자가 평가.
진전 없으면 접근 전환.
모든 단계 완료 시 결과 종합 응답.
```

무한 루프 방지 + 수렴 유도.

---

## 9. 컨텍스트 관리

### 9.1 관련 정보만 제공

큰 컨텍스트 ≠ 좋은 결과:
- 중간 정보 무시 (Lost-in-the-Middle, Liu et al. 2023)
- 노이즈 → 정확도 저하
- 비용·지연 증가

**원칙**: 관련 정보만, 구조화하여.

### 9.2 Context Compaction

다회 턴 대화에서 컨텍스트 관리:

**Observation Masking** (권장):
- 최근 N개 도구 결과만 유지, 나머지 "[이전 결과 생략]"으로 대체
- 추론 흐름(사용자 질문, 모델 판단)은 유지
- JetBrains 연구: LLM 요약 대비 solve rate +2.6%, 비용 -52%

**Sliding Window**:
- 최근 K개 메시지만 유지
- 단순하지만 초기 지시 소실 위험

### 9.3 정적/동적 분리

```
[정적: 역할, 규칙, 예시]       ← 매 호출 동일 → 캐시 가능
[준정적: 도구 목록]             ← 도구 변경 시만 갱신
[동적: 사용자 입력, 대화 이력]  ← 매 호출 변경
```

정적 부분이 길수록 캐시 히트율 ↑ → 비용·지연 ↓

---

## 10. 실패 패턴 & 대응

### 10.1 환각

모델이 없는 사실/라이브러리 생성.

| 대응 | 방법 |
|------|------|
| 참조 데이터 제공 | 실제 input 데이터를 프롬프트에 포함 |
| 화이트리스트 | 허용 import 목록 명시, 목록 외 금지 |
| 하드코딩 금지 | "가짜 데이터 금지, input 실제 처리" 명시 |

### 10.2 지시 무시

| 대응 | 방법 |
|------|------|
| 반복 | 핵심 지시를 시작과 끝에 반복 |
| 체크리스트 | "답변 전 아래 조건 확인" |
| Negative example | "❌ 이렇게 말고 → ✅ 이렇게" |

### 10.3 형식 위반

| 대응 | 방법 |
|------|------|
| 예시 | 정확한 출력 예시 포함 |
| Constrained decoding | Ollama `format` 파라미터 |
| 후처리 fallback | regex/parser로 extraction |

### 10.4 과잉 출력

```
"preamble 없이 바로 시작. 요약, 면책조항, 맺음말 금지."
"코드 블록만 출력. 코드 블록 밖 텍스트 금지."
```

### 10.5 반복 실패 루프

```
동일 도구를 동일 입력으로 2회+ 호출 금지.
실패 시 반드시 다른 파라미터나 접근법.
3회 연속 실패 → 사용자에게 보고.
```

### 10.6 위치 편향

긴 프롬프트에서 중간 내용 무시.

- 중요 규칙: 처음 + 마지막에 배치
- 규칙을 별도 섹션(`<rules>`, `<forbidden>`)으로 분리
- 그룹화로 anchor 생성

---

## 11. 평가 & 반복 개선

### 11.1 평가 먼저, 프롬프트 나중에

> **OpenAI**: "Test changes systematically"

프롬프트 개선 전 반드시:
1. 성공 기준 정의
2. 테스트 셋 구축 (최소 20-50개)
3. 현재 baseline 측정

없으면 개선 여부 판단 불가.

### 11.2 A/B 테스트

```
1. 변수 하나만 변경
2. 동일 테스트 셋으로 양쪽 실행
3. 비교: pass rate, 형식 준수율, 토큰 사용량, 지연
4. 30+ 샘플로 방향성, 100+ 샘플로 유의성
```

**한 번에 여러 변수 변경 금지** → 어떤 변경이 효과 냈는지 불명.

### 11.3 반복 개선 루프

```
1. 간단한 프롬프트로 시작
2. 10-20개 다양한 입력으로 테스트
3. 실패 패턴 분류 (형식? 정확도? 엣지케이스?)
4. 해당 패턴만 타겟 수정 (규칙 추가, 예시 추가)
5. 재테스트 → 회귀 확인
6. 반복
7. 불필요한 지시 제거 → 토큰 효율화
```

**핵심**: 감이 아니라 데이터 기반으로 수정.

### 11.4 프롬프트 버전 관리

- Git으로 버전 추적
- 변경 시 이유·테스트 결과 기록
- rollback 가능하게

---

## 12. Local Model 특화 기법

### 12.1 Constrained Decoding 적극 활용

Ollama `format` 파라미터 = JSON 출력 안정성의 핵심.
JSON 출력이 필요한 모든 호출에서 사용.

```python
response = client.chat(
    messages=messages,
    json_schema=manifest_schema,  # constrained decoding
)
```

프롬프트에서 "JSON만 출력" 지시하는 것보다 훨씬 안정적.

### 12.2 프롬프트 길이에 민감

Local model은 상용 API 모델 대비:
- 긴 프롬프트에서 성능 저하 더 큼
- 규칙 수 많을수록 준수율 하락 폭 큼

**대응**:
- 규칙을 최소한으로. 불필요한 규칙 제거.
- 예시도 최소 효과적 수로 유지 (3-5개)
- 중복 제거 철저히

### 12.3 Few-shot 의존도 높음

Local model = few-shot 예시에 크게 의존.
상용 모델보다 zero-shot 성능 낮으므로 예시 충분히 제공.

### 12.4 복잡한 추론 한계

Multi-step 추론 능력 제한적.

**대응**: Prompt chaining으로 분해.
- 한 호출에 복합 판단 요구 대신
- 단계별로 분리하여 각 호출의 판단 단순화
- 예: Manifest(스키마) → Code(구현) 2단계

### 12.5 Temperature 설정

| 역할 | 권장 | 이유 |
|------|------|------|
| Planner | 0.0 | 도구 선택은 결정론적 |
| Builder Manifest | 0.0 | JSON 스키마는 정확해야 |
| Builder Code | 0.0 | 코드 정확성 우선 |
| Repair | 0.0-0.3 | 다른 접근 시도 시 약간의 다양성 유용할 수 있음 |

### 12.6 Eval 재현성: seed 고정 + N-run Stability Gate

> **결정사항** (2026-04-12)

#### 문제

gemma4:26b 로 eval 돌릴 때 동일 prompt 에서 매 run 마다 결과 flip.
`tool_repair`, `repair_no_repeat`, `deprecated_scan` 등이 확률적 pass/fail → prompt 개선과 무관한 false regression 생산.
단일 run 비교로는 진짜 regression 과 noise 구분 불가.

#### 결정

1. **`seed=42` 고정** — `config.py` + Ollama `options.seed` 전달. 동일 입력 → 동일 sampling path → near-deterministic.
2. **3-run majority vote** — `tracker.merge_stability_reports()` 로 3회 eval 결과 합산, 과반(2/3+) pass 면 pass. `run_loop.sh` 에 `STABILITY_RUNS=3` 환경변수.
3. **regression 판정 = stable baseline vs stable after** — 3/3 pass 였던 scenario 가 0/3 또는 1/3 으로 떨어진 경우만 regression. 1회 flip 은 noise.

#### 학술 근거

| 논문 | 핵심 발견 |
|------|----------|
| [Towards Reproducible LLM Evaluation (2024)](https://arxiv.org/abs/2410.03492) | temp=0 + fixed seed → prediction interval < 0.01 에 **3회 반복 충분**. 로컬 모델은 seed 고정 시 완전 deterministic 가능. |
| [Non-Determinism of Deterministic LLM Settings (2024)](https://arxiv.org/abs/2408.04667) | temp=0 에서도 "alarming variance" 존재. 단일 run 보고는 불충분. min-max range 보고 권장. |
| [Numerical Sources of Nondeterminism (2025)](https://arxiv.org/abs/2506.09501) | 근본 원인 = floating-point 비결합성. FP32 → near-deterministic, BF16 → significant variance. GPU count/batch size 도 영향. |

#### 적용 위치

- `src/adaptive_agent/config.py` — `LLMConfig.seed: int = 42` 추가
- `src/adaptive_agent/llm/client.py` — Ollama options 에 `"seed": self._config.seed` 전달
- 별도 실험 러너 — `STABILITY_RUNS` 환경변수, stability report merge helper (저장소 외부 dev tool)

#### 비용

- 단일 eval: 기존과 동일 (seed 추가는 무비용)
- 3-run stability: eval 시간 3배. 60 scenarios × 3 runs = ~180 scenario 실행
- 권장: 개발 중은 `STABILITY_RUNS=1` (seed 만 의존), CI/최종 검증은 `STABILITY_RUNS=3`

---

## 13. 참고 자료

| 출처 | 핵심 내용 |
|------|----------|
| [OpenAI Prompt Engineering Guide](https://platform.openai.com/docs/guides/prompt-engineering) | 6대 전략: 명확한 지시, 참조 텍스트, 작업 분해, 사고 시간, 외부 도구, 체계적 테스트 |
| [Anthropic Prompt Engineering](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering) | XML 태그, prompt chaining, long context |
| [OpenAI Function Calling](https://platform.openai.com/docs/guides/function-calling) | 도구 설명 > 파라미터명, enum 활용 |
| Chain-of-Thought (Wei et al. 2022) | Few-shot CoT 추론 정확도 향상 |
| Lost in the Middle (Liu et al. 2023) | Long context 중간 정보 무시 현상 |
| JetBrains Observation Masking (2025) | Context compaction solve rate 향상 |
| [Towards Reproducible LLM Evaluation (2024)](https://arxiv.org/abs/2410.03492) | seed + temp=0 → 3회 반복으로 재현성 확보. 단일 run 불충분. |
| [LLM Stability (2024)](https://arxiv.org/abs/2408.04667) | temp=0 에서도 variance 존재. N-run min-max 보고 권장. |
| [Numerical Nondeterminism (2025)](https://arxiv.org/abs/2506.09501) | FP 비결합성이 근본 원인. precision format + GPU config 영향. |

---

## 14. 변경 이력

| 날짜 | 변경 대상 | 변경 내용 | 테스트 결과 |
|------|----------|----------|------------|
| | | | |

## 15. Active Iteration Tracker

### 현재 신뢰 기준
- `R19`: `29/32`
- 남은 이슈: `deprecated_scan`, `cross_session`, `repair_index_error`
- `R21`, `R22`는 샌드박스 worker가 local Ollama에 연결하지 못해 전 시나리오가 `0.0s`로 실패한 무효 run

### 이슈별 가설

#### 1. `deprecated_scan`
- **관측**: planner가 여전히 `def .*deprecated` 패턴을 고를 때가 있음
- **원인 가설**: "deprecated 함수"를 데코레이터가 붙은 함수가 아니라 이름에 `deprecated`가 들어간 정의로 오해
- **현재 대응**:
  - planner few-shot에 `@deprecated + context_after=1` negative/positive example 추가
  - `grep_search` description에 marker search 규칙 추가
- **다음 확인**: targeted eval에서 grep pattern이 `@deprecated`로 바뀌는지 확인

#### 2. `cross_session`
- **관측**: generate_code 이후 실행된 도구가 `filtered_count=0`, `original_count=0` 또는 입력 누락처럼 동작
- **원인 가설**:
  - builder가 structured input보다 `_data`를 우선하는 경향
  - generate_code few-shot이 실제 read 결과 대신 placeholder로 학습될 위험
- **현재 대응**:
  - builder prompt에 "비즈니스 키 우선, `_data`는 보조/폴백" 계약 명시
  - generate_code example에서 실제 read 결과 데이터를 넣도록 지시
- **다음 확인**: targeted eval에서 created tool input에 `monsters` 실제 데이터가 유지되는지 확인

#### 3. `repair_index_error`
- **관측**: 실행 결과는 맞지만 최종 텍스트 응답에 `None`이 빠져 verification 실패
- **원인 가설**: planner가 `null` 결과를 한국어 설명으로 바꾸면서 `None` 토큰을 잃음
- **현재 대응**:
  - planner few-shot에 `{"first": null, "last": null}` -> "None" 보존 예시 추가
- **다음 확인**: targeted eval에서 final response에 `None` 포함 여부 확인

### 최신 검증

### [2026-04-10] targeted_iter_r1
- **대상**: `src/adaptive_agent/llm/prompts.py`, `src/adaptive_agent/tools/registry.py`
- **변경**:
  - planner few-shot에 `read_file -> generate_code`, `ask_user`, `None` 보존, `@deprecated` 예시 추가
  - builder prompt에 structured input 우선 계약 추가
  - `grep_search` description에 marker search 규칙 추가
- **근거**: `R19` 기준 잔여 실패 3건 (`deprecated_scan`, `cross_session`, `repair_index_error`)
- **테스트**: `uv run python -m eval --filter deprecated_scan cross_session repair_index_error`
- **결과**: `3/3 PASS`
- **회귀**: 아직 full suite 미검증

### [2026-04-10] targeted_iter_r2
- **대상**: `src/adaptive_agent/llm/prompts.py`
- **변경**:
  - 여러 파일이 명시되면 필요한 파일을 모두 읽고 나서 응답/분석하도록 planner 규칙 추가
  - 코드/스크립트 버그 수정 요청은 `read_file` 설명으로 끝내지 말고 `generate_code`까지 이어가도록 규칙 추가
  - `think_first`, `tool_repair`, `repair_no_repeat`에 대응하는 일반 few-shot 추가
- **근거**: `iter_full_r1`에서 잔여 실패 3건이 모두 `read_file` 후 실제 처리 없이 끝나는 planner 종료 패턴
- **테스트**: `uv run python -m eval --filter tool_repair think_first repair_no_repeat`
- **결과**: `3/3 PASS`
- **회귀**: full suite 재확인 필요

### [2026-04-10] full_iter_r2
- **대상**: 전체 시나리오
- **테스트**: `uv run python -m eval`
- **결과**: `30/32 PASS`
- **잔여 이슈**:
  - `read_before_code`: `read_file -> generate_code` 흐름은 맞지만 CSV 원문 처리 결과가 빈 배열
  - `multi_output`: JSON 원문 처리/다중 파일 출력 계약이 흔들리며 파일 미생성
- **다음 액션**:
  - builder prompt에 `_data` JSON/CSV 정석 파싱 예시 반영
  - `read_before_code`, `multi_output` 2개만 targeted 검증

### [2026-04-10] targeted_iter_r5_r6
- **대상**: `src/adaptive_agent/llm/prompts.py`
- **변경**:
  - builder prompt에 CSV 필터링 / JSON 분기 저장 패턴 예시 추가
  - planner prompt에 multi-output generate_code 예시 추가
  - builder prompt에 `json.loads(str(input))` 같은 잘못된 전체-input 파싱 금지 명시
- **테스트**:
  - `uv run python -m eval --filter read_before_code multi_output`
  - `uv run python -m eval --filter multi_output`
- **결과**:
  - `read_before_code`: PASS
  - `multi_output`: PASS
- **회귀**: full suite 재확인 필요

### [2026-04-10] targeted_iter_r7_r9
- **대상**: `src/adaptive_agent/llm/prompts.py`, `src/adaptive_agent/agent/core.py`
- **변경**:
  - missing file을 임의 생성하지 않는 planner 규칙/negative example 추가
  - `_data`(원문)와 `_input_summary`(실행 파라미터)의 역할 분리 규칙 추가
  - 비영어권 키 필터링 / 통계 출력 키 유지 예시 추가
  - `read_file` 결과가 JSON/CSV면 `generate_code`에 raw text와 함께 구조화된 `data`/`rows`도 전달
- **테스트**:
  - `uv run python -m eval --filter unicode_json statistics_calc repair_keyerror nonexistent_file`
  - `uv run python -m eval --filter unicode_json`
- **결과**:
  - `statistics_calc`: PASS
  - `repair_keyerror`: PASS
  - `nonexistent_file`: PASS
  - `unicode_json`: PASS
- **회귀**: final full suite 필요

### 기록 템플릿

```markdown
### [YYYY-MM-DD] 변경명
- **대상**: 파일 경로 → 함수명
- **변경**: 무엇을 왜
- **근거**: 어떤 실패 데이터에 기반
- **테스트**: 어떤 시나리오로 검증
- **결과**: Before → After
- **회귀**: 기존 통과 케이스 영향 여부
```
