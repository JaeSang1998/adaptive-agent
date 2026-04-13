# ADR-001: Observation Masking 기반 컨텍스트 압축

**상태**: 채택
**날짜**: 2026-04-07 (최초) / 2026-04-13 (단일 primitive 단순화)

---

## 문제

Agent 루프에서 도구 생성·실행·수정을 반복하면 대화 히스토리가 누적된다. 특히 도구 실행 결과(JSON 출력, 파일 내용 등)는 수천~수만 토큰에 달할 수 있다.

Gemma 4의 256K context window는 넉넉하지만, 연구에 따르면 실제 성능 저하는 window 한계 이전에 발생한다:

- **Context rot**: 50K 토큰 이후 정확도가 급격히 저하 (Chroma Research 2025)
- **Lost-in-the-middle**: 모델이 시작/끝에는 집중하지만 중간부 정보를 놓침 (30%+ 정확도 감소)
- **Attention dilution**: 100K 토큰 = 100억 개 pairwise attention 관계

따라서 context window를 꽉 채우는 것이 아니라, **고신호 토큰만 유지하는 압축 전략**이 필요하다.

## 검토한 대안

### A. LLM 기반 요약 (기존 구현)

기존에는 3단계 파이프라인(도구 결과 스니펫 → LLM 요약 → sliding window)을 사용했다.

**장점**: 의미론적으로 정확한 요약 가능
**단점**:
- Ollama 로컬 환경에서 요약 1회당 수 초 지연
- JSON 파싱 실패 시 fallback 품질이 낮음
- LLM 호출이 agent 루프를 blocking
- 요약 과정에서 도구 stop signal이나 디버깅 단서가 소실될 수 있음

### B. Observation Masking (채택)

JetBrains Research (2025)의 실험 결과:
- Reasoning/action 히스토리는 그대로 유지
- 오래된 도구 실행 결과(observation)의 본문만 마스킹
- **LLM 요약 대비 solve rate 2.6% 높음, 비용 52% 절감**

핵심 인사이트: **agent의 판단 흐름(어떤 도구를 왜 실행했고, 성공/실패했는지)이 실제 출력값보다 더 중요하다.**

### C. 순수 Sliding Window

최근 N개 메시지만 보존하고 나머지 삭제.

**장점**: 구현 단순
**단점**: 초기 사용자 요청, 도구 생성 결정 등 핵심 컨텍스트가 유실됨

### D. 무처리

256K가 넉넉하니 압축하지 않는다.

**단점**: Context rot 연구에 따르면 50K 이후 성능이 저하되므로, 장시간 세션에서 agent 품질이 떨어짐

### E. Multi-stage Compaction (이전 구현, 2026-04-13 폐기)

초기에는 (B) Observation Masking 위에 (C) Sliding Window 를 fallback 으로 얹은 2-stage + planner/normal/aggressive 3-stage trigger 를 채택했다.

**폐기 이유**:
- **실측 사용량 vs budget gap**: 본 프로젝트 train/test 51 시나리오 실측에서 max prompt_tokens ~1,900, max session message_chars ~17K. `num_ctx=131,072` 의 **1.5% 만 사용**. sliding window fallback 은 절대 발동 안 함 (~96 개 unmasked tool result 가 누적되어야 trigger).
- **Dead code 비용**: 3 stage 분기 + token estimation + first message preserve + aggressive observation prune 등 ~80 LOC 가 typical 워크로드에서 실행되지 않으면서 유지보수 부담만 증가.
- **YAGNI**: defense-in-depth 가 정당화되려면 실측 max 가 budget 의 50% 이상이어야 함. 1.5% 사용률에서 추가 안전망은 과잉 설계.
- **Failure mode 동일성**: sliding window 가 늦게 firing 해도 그 이전에 이미 Ollama silent truncation 이 발생할 수 있음. fallback 이 "안전한 silent failure" 를 보장하지 못함 → fail fast 가 debug 더 쉬움.

## 결정

**단일 primitive: Observation Masking.** sliding window / token budget 체크 / multi-stage 분기 모두 제거.

```
compact(session)  매 step 시작 전 1 회 호출
  - tool result 메시지 (role="tool" 또는 content 가 "[도구 " 로 시작) 식별
  - 최근 _KEEP_RECENT_FULL=5 개는 원문 유지
  - 그 이전은 header ("[도구 xxx 실행 성공/실패]") 만 남기고 본문을 "[결과 생략]" 로 교체
  - user/assistant/system/assistant.tool_calls 메시지는 건드리지 않음
  - idempotent
```

`session.observations` dict (`$ref` resolution 의 fallback store) 는 별도 hard cap (`_MAX_OBSERVATIONS=100`) 로 leak 방지. lookup 실패 시 `_resolve_refs` 가 graceful 하게 error_list 에 추가.

극단적 워크로드 (단일 100KB+ tool result) 는 `SESSION_RESULT_CHARS=12_000` (head 8K + tail 4K) hard safety net 이 cap. runner 의 `RUNNER_OUTPUT_BYTES=30_000` 이 OS 레벨 boundary.

## 근거

1. **LLM 호출 0회**: Ollama 로컬 환경에서 지연/실패 위험 제거
2. **Reasoning 보존**: Planner가 "왜 이 도구를 만들었고, 성공/실패했는지"를 인식할 수 있음
3. **실험적 우위**: JetBrains 결과에서 observation masking이 LLM 요약보다 더 좋은 성능
4. **단순성**: 단일 함수, 단일 trigger, 단일 invariant. multi-stage / fallback / token estimation 제거로 ~130 LOC 감소
5. **실측 기반**: 본 프로젝트 max prompt_tokens ~1,900, max session ~17K chars, num_ctx=131K 의 1.5% 사용 — defensive 다층화는 YAGNI

## 구현

- `src/adaptive_agent/agent/compaction.py`: `compact(session)` 단일 함수 (~30 LOC)
- `src/adaptive_agent/agent/core/__init__.py`: `_run_step()` 시작 시 1회 호출
- `src/adaptive_agent/agent/session.py`: `record_observation` 에 hard cap. `add_tool_result` 에서 `SESSION_RESULT_CHARS` hard safety net.
- `src/adaptive_agent/limits.py`: `SESSION_RESULT_CHARS=12_000`. `FIRST_MSG_PRESERVE_CHARS` 제거.

## 전제

- **`config.llm.num_ctx` 가 충분히 커야 함** (현재 기본값 `131072`). num_ctx 가 작으면 multi-turn + 긴 tool result 를 Ollama 가 silent truncation → native tool calling 의 응답이 empty content 로 돌아옴 (실제 관찰됨, 2026-04-13).
- 워크로드가 본 프로젝트 실측 (~17K chars/session) 의 5x 이상으로 커질 가능성이 보이면 explicit token accounting + sliding window 재도입 검토.

## 참고

- JetBrains Research (2025): "Efficient Context Management for Code-Generation Agents"
- Chroma Research (2025): "Context Rot: How Increasing Input Tokens Impacts LLM Performance"
- Anthropic (2025): "Effective Context Engineering for AI Agents"
