# ADR-005: `agent/core.py` → `agent/core/` 패키지 분리

**상태**: 채택
**날짜**: 2026-04-13

---

## 문제

`agent/core.py` 가 660 LOC, 27 메서드, 단일 `AgentCore` 클래스로 비대해졌다. 한 파일에 다음 책임이 모두 있었다:

- 메인 루프 / 단일 step 실행 / dispatch
- planner timeout fallback
- `$ref` dehydration
- workspace context (planner grounding 용 디렉토리 스냅샷)
- `generate_code` 파이프라인 (build → validate → register → run)
- `repair_tool` 루프
- `think` / `ask_user` / `update_plan` 메타 도구
- 일반 builtin / persistent / session 도구 dispatch
- 결과 기록 + suggested_files 추출

추적성은 좋았지만 27 메서드 + 660 LOC 는:

- 한 메서드를 수정할 때 다른 책임 영역까지 화면에 들어옴
- handler 단위 unit test 가 어려움 (전체 AgentCore 인스턴스 필요)
- 새 도구/메타 추가 시 어디에 붙일지 자명하지 않음

## 검토한 대안

### A. 현재 유지

장점: 한 파일 추적성. 단점: 위 문제 그대로.

### B. Mixin 분리

`CodeGenMixin`, `RepairMixin`, `MetaHandlerMixin` 으로 분리 후 `class AgentCore(*Mixin)`.

**기각**: mixin 은 self type narrowing 어려움. mypy/pyright 에 unfriendly. self 가 어떤 attribute 에 의존하는지 mixin 만 보고 알 수 없음.

### C. Composition (handler 객체) — 채택

`CodeGenHandler`, `RepairHandler`, `MetaHandlers` 클래스가 각각 자신의 의존성을 생성자에서 받음. AgentCore 가 handler 들을 instantiate 하고 dispatch 시 위임.

**채택 이유**:
- 각 handler 의 의존성이 생성자 시그니처에 명시
- handler 단독 unit test 가능 (mock session/registry/builder 만 전달)
- AgentCore 의 dispatch (`_execute_tool` 의 match) 는 그대로 — 메인 루프 추적성 유지
- 자유 함수 가능한 부분 (`refs.py`, `workspace.py`) 은 클래스 없이 함수만

## 결정

```
agent/core/
├── __init__.py    ── AgentCore (slim 352 LOC) + 메인 루프 + dispatch + 공통 콜백
├── refs.py        ── resolve_refs (자유 함수)
├── workspace.py   ── workspace_context (자유 함수)
├── codegen.py     ── CodeGenHandler
├── repair.py      ── RepairHandler
└── meta.py        ── MetaHandlers (think / ask_user / update_plan / handle_tool)
```

**LOC 분포**:

| 파일 | LOC | 책임 |
|---|---|---|
| `__init__.py` | 352 | AgentCore + 메인 루프 + dispatch + `_fail` / `_run_and_record` |
| `meta.py` | 140 | think / ask_user / update_plan / 일반 도구 dispatch |
| `codegen.py` | 130 | generate_code 파이프라인 + tool name resolution |
| `repair.py` | 89 | repair_tool 루프 |
| `refs.py` | 63 | $ref dehydration + JSON/CSV 자동 parse |
| `workspace.py` | 59 | planner grounding 디렉토리 스냅샷 |
| **합계** | **833** | (분리 전 660 LOC + 책임 명시 docstring 추가분) |

분리 전 660 LOC 단일 파일 대비, **각 파일이 단일 책임 + ≤350 LOC**.

## 의존성 주입 패턴

handler 클래스는 모두 keyword-only 인자로 의존성 받음. AgentContext 같은 단일 dataclass 는 만들지 않음 — 각 handler 가 진짜 필요한 것만 명시.

```python
class CodeGenHandler:
    def __init__(
        self, *,
        session: Session,
        registry: ToolRegistry,
        builder: ToolBuilder,
        status: StatusCallback,
        fail: FailCallback,
        run_and_record: RunAndRecordCallback,
    ) -> None:
        ...
```

`fail` 과 `run_and_record` 는 AgentCore 의 메서드 — handler 가 상태 기록 / suggested_files dispatch 를 직접 알 필요 없게 캡슐화.

## 분리하지 않은 것

다음은 그대로 단일 파일 유지 — 단일 책임 + 합리적 LOC:

- `agent/planner.py` (112 LOC)
- `agent/session.py` (188 LOC)
- `agent/compaction.py` (105 LOC)
- `agent/events.py` (70 LOC, EventType Literal 추가 후)
- `agent/meta_tools.py` (88 LOC)
- `tools/builtin.py` (485 LOC, 8개 도구) — 분리 후보지만 이번 범위 외

## 영향

- 기능 변경 1건: `generate_code` 의 **이름 충돌 정책** 변경 — 기존엔 `existing` → `existing_2` 자동 suffix, 이제는 fail (recovery hint 로 planner 가 다른 이름으로 재시도). silent rename 의 누적 (`tool_2`, `tool_3` ...) 으로 인한 도구 카탈로그 오염을 방지. prompts.py rule 10 ("generic 이름 금지") 와 같은 정신.
- 외부 API 동일: `from adaptive_agent.agent.core import AgentCore`, 생성자 시그니처 동일.
- 테스트 1개 갱신: `TestResolveToolName` → `TestToolNameCollision` (충돌 fail 동작 검증).
- 152 unit test 모두 PASS.

## 참고

- god object anti-pattern (Riel 1996, "Object-Oriented Design Heuristics")
- composition over inheritance — Effective Java Item 18
- claude-code 의 tool handler 분리 패턴 (subagent / tool-handler 단위)
