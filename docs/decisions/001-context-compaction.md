# ADR-001: Observation Masking 기반 컨텍스트 압축

**상태**: 채택  
**날짜**: 2026-04-07

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

## 결정

**Observation Masking + Sliding Window fallback** 2단계 파이프라인을 채택한다.

```
Stage 1: Observation Masking (비용 0)
  - 도구 결과 메시지에서 헤더("[도구 xxx 실행 성공/실패]")는 보존
  - 본문을 "[결과 생략]"으로 교체
  - 최근 2개 도구 결과는 원문 보존
  - 사용자 메시지, assistant 메시지(reasoning)는 건드리지 않음

Stage 2: Sliding Window (비용 0)
  - Stage 1 후에도 예산 초과 시 오래된 메시지 삭제
  - 최근 6개 메시지 보존
```

## 근거

1. **LLM 호출 0회**: Ollama 로컬 환경에서 지연/실패 위험 제거
2. **Reasoning 보존**: Planner가 "왜 이 도구를 만들었고, 성공/실패했는지"를 인식할 수 있음
3. **실험적 우위**: JetBrains 결과에서 observation masking이 LLM 요약보다 더 좋은 성능
4. **단순성**: 파싱 실패, fallback 분기, 요약 프롬프트 관리가 불필요
5. **점진적 degradation**: Stage 1이 대부분 충분하고, Stage 2는 극단적인 경우에만 발동

## 구현

- `src/adaptive_agent/agent/compaction.py`: `compact(session, token_budget=128_000)`
- `src/adaptive_agent/agent/core.py`: `_run_loop()` 매 step 시작 시 호출
- `session.summary` 필드 제거 (LLM 요약이 없으므로 불필요)

## 참고

- JetBrains Research (2025): "Efficient Context Management for Code-Generation Agents"
- Chroma Research (2025): "Context Rot: How Increasing Input Tokens Impacts LLM Performance"
- Anthropic (2025): "Effective Context Engineering for AI Agents"
