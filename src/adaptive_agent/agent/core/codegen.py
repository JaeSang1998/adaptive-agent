"""generate_code 파이프라인 핸들러.

흐름: validate description/tool_name → builder.build → sanitize name →
**충돌 시 fail** (planner 가 다른 이름으로 재시도) → validate code → register
session tool → run_and_record (caller 의 콜백).

`run_and_record` 콜백은 AgentCore 가 주입 — codegen handler 는 subprocess 실행
의 결과 기록 / suggested_files dispatch 를 직접 알 필요 없음.

설계 결정 (이름 충돌):
  자동 suffix (`existing` → `existing_2`) 대신 명시적 실패. planner 가 의도된
  이름을 골랐는데 충돌이면 silent rename 보다 재시도가 안전 — `_2` suffix 가
  쌓이면 의미 없는 도구 이름이 누적되어 재사용 매칭 품질이 떨어진다 (prompts.py
  rule 10 의 "generic 이름 금지" 와 같은 정신).
"""

from __future__ import annotations

from typing import Any, Callable

from adaptive_agent.agent.events import EventType
from adaptive_agent.agent.session import Session
from adaptive_agent.tools.builder import ToolBuilder
from adaptive_agent.tools.persistence import ToolPersistence
from adaptive_agent.tools.registry import ToolRegistry
from adaptive_agent.tools.validator import validate_tool_code

StatusCallback = Callable[[EventType, dict[str, Any]], None]
FailCallback = Callable[..., None]
RunAndRecordCallback = Callable[[str, str, dict[str, Any]], None]


class CodeGenHandler:
    def __init__(
        self,
        *,
        session: Session,
        registry: ToolRegistry,
        builder: ToolBuilder,
        status: StatusCallback,
        fail: FailCallback,
        run_and_record: RunAndRecordCallback,
    ) -> None:
        self._session = session
        self._registry = registry
        self._builder = builder
        self._status = status
        self._fail = fail
        self._run_and_record = run_and_record

    def handle(self, data: dict[str, Any]) -> None:
        """코드 생성 + 실행. 성공 시 세션 도구로 등록 → 저장은 _offer_save 에서."""
        description = str(data.get("description", ""))
        tool_name = str(data.get("tool_name", "")).strip()
        if not tool_name:
            self._fail(
                "generate_code",
                "generate_code 호출 시 tool_name 을 반드시 지정해야 합니다.",
                recovery=(
                    "작업 목적이 드러나는 snake_case 이름을 tool_name 키로 추가해서 다시 호출하세요. "
                    "예: {\"tool\": \"generate_code\", \"input\": {\"tool_name\": \"sales_top5_extractor\", "
                    "\"description\": \"...\", ...}}"
                ),
            )
            return
        input_data = _prepare_code_input(data)

        self._status("generating_code", {"description": description})
        self._build_and_run(description, input_data, tool_name=tool_name)

    def _build_and_run(
        self,
        description: str,
        input_data: dict[str, Any],
        *,
        tool_name: str,
    ) -> None:
        """Builder → Validator → Registry 등록 → Runner 실행."""
        user_request = self._session.last_user_request

        # 이름 충돌 사전 검사 — 빌드 전에 fail 해서 LLM 호출 비용을 절약.
        sanitized = ToolPersistence.sanitize_name(tool_name)
        if self._registry.get_tool(sanitized) is not None:
            self._fail(
                sanitized,
                f"도구 이름 '{sanitized}' 가 이미 존재합니다.",
                recovery=(
                    "다른 snake_case 이름으로 generate_code 를 다시 호출하세요. "
                    "기존 도구를 재사용하려면 generate_code 대신 그 도구를 직접 호출하세요. "
                    "예: 기존 'csv_salary_averager' 가 있으면 같은 작업에 그 도구를 부르세요."
                ),
            )
            return

        build_result = self._builder.build(
            description, user_request,
            input_data=input_data,
        )

        if not build_result.success:
            self._fail(sanitized, f"빌드 실패: {build_result.error}",
                       recovery="description을 더 구체적으로 수정하거나 ask_user로 추가 정보를 요청하세요.")
            return

        tool_name = sanitized

        self._status("creating_tool", {"tool_name": tool_name, "input": input_data})

        validation = validate_tool_code(build_result.code)
        if not validation.valid:
            self._fail(tool_name, f"검증 실패: {validation.reason}",
                       recovery=(
                           "이 시도는 등록되지 않았습니다. generate_code를 다시 호출하되, "
                           "허용된 표준 라이브러리만 사용하도록 description에 명시하세요. "
                           "예: 'bs4 대신 re/html.parser 사용'."
                       ))
            return

        manifest: dict[str, Any] = {"name": tool_name, "description": description, "tags": []}
        self._registry.register_session_tool(
            name=tool_name, code=build_result.code, manifest=manifest,
        )
        self._session.temp_tools[tool_name] = {
            "code": build_result.code, "manifest": manifest,
        }
        self._status("tool_created", {"tool_name": tool_name})

        self._registry.record_last_input(tool_name, input_data)
        self._run_and_record(tool_name, build_result.code, input_data)


def _prepare_code_input(data: dict[str, Any]) -> dict[str, Any]:
    """Planner 가 명시한 explicit input key 를 builder 에 전달.

    데이터는 Planner 가 `{"$ref": "<path>"}` (file) 또는 literal 값 (inline) 로
    명시. `resolve_refs` 가 _run_step 에서 이미 dehydrate 한 값이 `data` 에 들어옴.
    builder 는 description 을 별도 인자로 받으므로 여기서 제외.
    """
    return {k: v for k, v in data.items() if k not in ("description", "tool_name")}
