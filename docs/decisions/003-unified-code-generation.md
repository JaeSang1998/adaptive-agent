# ADR-003: run_code/create_tool 통합 → generate_code

**상태**: 채택  
**날짜**: 2026-04-12

---

## 문제

`run_code`(일회성 코드 실행)와 `create_tool`(재사용 도구 생성)이 별도 액션으로 존재했다. 공유 파이프라인(`_build_and_run_tool`)을 사용하면서 진입점만 2개 → 불필요한 복잡성.

추가로 `run_code` → 저장 시 manifest 없이 저장되는 gap이 존재했다 (input_schema 유실).

## 검토한 대안

### A. 현재 유지 (2개 액션 + 2단계 Builder)

- Planner가 run_code/create_tool 중 선택해야 함 → 판단 부담
- manifest LLM 호출이 create_tool에만 존재 → run_code → 저장 시 manifest 부재
- 분리의 실체가 `skip_manifest` 플래그 하나

### B. 통합 (1개 액션 + 후속 저장) — 채택

모든 production 시스템이 이 패턴 사용:
- **Claude Code**: 단일 루프, "도구 생성" 개념 없음
- **CodeAct (Apple)**: 통합 코드 액션 → 기존 agent 대비 20% 높은 성공률
- **Voyager**: 실행 → 성공하면 자동 저장. 명시적 분리 없음
- **LATM**: dispatcher가 판단하지만 코드 생성 메커니즘은 동일

## 결정

`generate_code` 하나로 통합. persistence는 실행 성공 후 사용자 판단. manifest는 AST inspect으로 코드에서 추출 (LLM 호출 불필요).

```
통합 모델:
  Planner → generate_code (하나만)
    → code 생성 → validate → execute
    → 성공 시 → "저장할까요?"
    → 저장 시 → AST에서 run() docstring 추출 → manifest 자동 생성
  + 기존 도구 재사용: Planner가 직접 호출 (변경 없음)
```

## 근거

1. **Planner 판단 단순화**: 2개 액션 → 1개. LLM의 선택 부담 감소
2. **manifest LLM 호출 제거**: 지연 3-5초 절감 + 파싱 실패 경로 제거
3. **저장 시 스키마 항상 존재**: inspect 기반이므로 run_code → 저장 gap 해소
4. **"일회성 → 재사용" 전환 자연스러움**: 실행 성공 후 사용자가 저장 여부 결정
5. **코드 ~120줄 순 감소**: Builder manifest 단계, create_tool 핸들러, BuilderManifest 모델 삭제

## 영향

- 삭제: `_handle_run_code`, `_handle_create_tool`, `_build_manifest`, `BuilderManifest`, `builder_manifest_messages`
- 추가: `_handle_generate_code`, `_auto_name`, `_resolve_tool_name`, `_find_similar_tool_code`, `extract_manifest_from_code`
- eval 시나리오: `06_cross_session.yaml` 이벤트 검증 `creating_tool` → `tool_created`
- 테스트: `run_code`/`create_tool` 참조 → `generate_code`로 통합

## 참고

- CodeAct (Apple Research, 2024): "Executable Code Actions Elicit Better LLM Agents"
- LATM (ICLR 2024): "Large Language Models as Tool Makers"
- Voyager (2023): "An Open-Ended Embodied Agent with Large Language Models"
