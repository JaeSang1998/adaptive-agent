# ADR-004: Native Tool Calling 기본값 — opt-in 으로 전환

**상태**: 채택
**날짜**: 2026-04-13

---

## 맥락

이 프로젝트는 처음부터 **Ollama native tool calling 을 선호 경로**로 설계했다. ADR 으로 명시되지 않았지만 코드 (`OllamaClient`, `Planner._to_ollama_tools`) 와 README §4.2 ([`L275`](../../README.md), [`L283`](../../README.md), [`L289`](../../README.md)) 의 원래 추천이 native 였다. 이유:

- `role: "tool"` 메시지로 도구 결과가 user 메시지와 완전 분리 (fallback 의 "role 오염" 한계 회피)
- Ollama 가 tool schema 검증 → arguments 형식 오류 조기 차단
- JSON 추출/healing 오버헤드 없음
- Planner 외 Builder/Repair 까지 같은 프로토콜로 확장 가능

기본값은 `enable_native_tools=True` 였고, `OllamaClient` 가 첫 호출에서 capability auto-detection 을 수행해 미지원 모델만 prompt-based JSON + `json_repair` 경로로 fallback 하도록 구현했다.

## 문제

2026-04-13 추적 결과, **default 모델 `gemma4:26b` 가 native 모드를 production-safe 하게 못 쓴다**. 증상:

- eval `csv_analysis`, `read_before_code`, `multi_step` 이 deterministic 하게 실패. worker.log 에서 Call 1 (`read_file`) 은 `tool_calls=1` 정상 emit, 그 결과를 history 에 넣은 Call 2 는 `eval_count=72` 토큰을 소비하지만 `content=""`, `tool_calls=[]`, `thinking=""` — 생성 토큰이 어느 필드에도 surface 안 됨.
- 동일 body 를 raw curl 로 `/api/chat` 또는 `/v1/chat/completions` 에 직접 던져도 재현. `arguments` object/string, `think=true/false`, `stream=true/false` 모두 변화 없음. Ollama 본체 (0.20.6, gemma4 streaming tool-call 패치 포함) 의 다른 단일턴 / multi-tool 시나리오는 정상 동작.
- Bisection: **`read_file + 600+ chars structured tool result + save/write action 요청`** 이 동시에 만족될 때만 트리거. 같은 긴 결과를 `run_bash` 로 받으면 통과. 같은 `read_file` + "보여줘" 분석 요청은 통과.
- system prompt 의 `<examples>` 섹션 (2200~2500 char 구간, JSON tool-call 예시 포함) 을 제거하면 native 가 다시 동작. 단 그 섹션은 `optim/log.jsonl` iter1 의 `train 97.5% → 100%` 개선분이 누적된 핵심 튜닝이라 삭제하면 builder `KeyError` (사이블링 데이터 키 미전달) 등 즉각 regression.

요약: gemma4 가 "tool 관련 knowledge 가 일정 임계 이상 system prompt 에 누적되면 multi-turn tool-result state 에서 확률적으로 empty content 반환" 하는 model-side 제약을 가지며, 이는 prompt 레벨에서 robust 하게 우회 불가능.

## 검토한 대안

### A. `<examples>` 섹션 제거 + native on

JSON 텍스트 examples 가 native 모드를 깨뜨리니 예시를 모두 삭제.

**기각 이유**:
- iter1 commit 이후 누적된 `$ref` 패턴 학습 손실 → planner 가 `generate_code` 호출 시 데이터 sibling 키를 빼먹음 → builder 빈 입력으로 실행 → `KeyError`.
- 새 regression 클래스 도입.

### B. `<examples>` prose 재작성 + native on

JSON 텍스트만 빼고 자연어 prose 로 동일 knowledge 재작성 (`<patterns>` 실험).

**기각 이유**:
- bisection 결과 prose 로 써도 동일 임계 도달 시 트리거. JSON 텍스트가 아니라 "tool knowledge 분량" 자체가 문제.
- 어떤 줄이 트리거할지 비결정적 — fragile.

### C. `generate_code` schema description 에 패턴 박기

system prompt 가 아니라 Ollama tools 배열의 schema description 에 `$ref` 사용법을 박아 system prompt 충돌을 회피.

**기각 이유**:
- 14 개 tool 모두 schema description 을 손봐야 실효적 커버리지.
- 부분 fix 라 다른 패턴 (multi-file, inline) 못 잡음.

### D. 모델 교체 (`gemma4:31b`, `qwen3.5:9b` 등)

다른 모델로 전환해 native 살리기.

**보류 이유**:
- 하드웨어/성능 미검증 (특히 31b 메모리, qwen3.5 한글 능력).
- 기존 prompt 튜닝이 모델 의존적이라 교체 시 train/test 재검증 비용 큼.
- 별도 실험 가치 있음 — ADR 종결 후 follow-up 으로 분리.

### E. prompt-based default + native opt-in (채택)

`enable_native_tools` 기본값을 `false` 로. **native 경로 코드는 모두 유지**. CLI override / env / config.yaml 로 언제든 enable.

**채택 이유**:
- `optim/eval_smoke_no_native.json` (오늘 17:57) 에서 `csv_analysis`, `read_before_code` PASS. 10 시나리오 smoke 에서 10/10 PASS (`optim/eval_iter7_native_off.json`).
- `optim/log.jsonl` iter1 의 `train 97.5% → 100%` 가 prompt-based 경로에서 달성 — 기존 튜닝과 정합.
- `extract_and_heal_json` 은 dead path 가 아님. 다단계 파싱 + healing 이 production-grade 로 구현돼 있음 ([`planner.py:106-116`](../../src/adaptive_agent/agent/planner.py)).
- Capability detection / native code path 는 그대로 살아있으므로 모델 교체 또는 gemma4 stabilize 시 한 줄 (`AGENT_LLM_ENABLE_NATIVE_TOOLS=true`) 로 native 복귀.

## 결정

`LLMConfig.enable_native_tools` 기본값을 `False` 로 변경. native 경로 코드 (capability detection, `_to_ollama_tools`, `add_assistant_tool_call`, `add_tool_result` 의 `role: "tool"` 분기) 는 전부 유지. opt-in 경로:

- CLI: `python -m eval --override llm.enable_native_tools=true`
- 환경 변수: `AGENT_LLM_ENABLE_NATIVE_TOOLS=true python src/adaptive_agent/main.py`
- `~/.adaptive-agent/config.yaml` 의 `llm.enable_native_tools: true`

## 결과

- 모든 eval 시나리오가 prompt-based + `json_repair` 경로로 안정 동작 (10/10 smoke 실증).
- 기존 prompt 튜닝 (iter1+ 의 `<examples>` / `<rules>`) 이 그대로 유효.
- README §4.2 의 native 경로 설명은 유지하되 한 줄 deferral 주석 추가.

## 한계

- fallback 모드의 **role 오염** (도구 결과가 `role: "user"` + `[도구 ...]` 접두사로만 구분, README L287-289 기존 명시) 이 default 동작이 됨. 도구 결과에 악의적 텍스트가 포함되면 사용자 메시지로 오인 가능.
- 장기적 목표는 여전히 native. 이 ADR 은 임시 deferral 이지 native 기각이 아님.

## 재검토 조건

다음 중 하나가 충족되면 default 를 다시 `true` 로 되돌리는 것을 검토:

1. gemma4 (또는 후속 버전) 의 multi-turn tool-result empty content 이슈가 Ollama 또는 모델 측에서 fix 되어 동일 eval body 로 재현 안 됨.
2. 대체 모델 (`gemma4:31b`, `qwen3.5:9b`, `qwen3:4b` 등) 에서 train/test split 재검증 후 native 안정성 확인.
3. Builder/Repair 까지 native tool calling 으로 확장하는 설계 재개 시 (현재 README L674 의 향후 개선 항목) 동시에 default 반전.

## 참고

- README §4.2 LLM Layer ([README.md](../../README.md))
- `planner.py` `_to_ollama_tools` ([src/adaptive_agent/agent/planner.py](../../src/adaptive_agent/agent/planner.py))
- `client.py` capability detection ([src/adaptive_agent/llm/client.py](../../src/adaptive_agent/llm/client.py))
- `optim/eval_iter7_native_off.json` (10/10 smoke 실증)
- `optim/log.jsonl` iter1 (prompt-based 경로의 train 100% commit)
