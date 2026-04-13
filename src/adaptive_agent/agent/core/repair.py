"""repair_tool 핸들러: 실패 traceback + previous_errors 를 LLM 에 보내 코드 수정."""

from __future__ import annotations

from typing import Any, Callable

from adaptive_agent.agent.events import EventType
from adaptive_agent.agent.session import Session
from adaptive_agent.tools.registry import ToolRegistry
from adaptive_agent.tools.repair import ToolRepairer
from adaptive_agent.tools.validator import validate_tool_code

StatusCallback = Callable[[EventType, dict[str, Any]], None]
FailCallback = Callable[..., None]
RunAndRecordCallback = Callable[[str, str, dict[str, Any]], None]


class RepairHandler:
    def __init__(
        self,
        *,
        session: Session,
        registry: ToolRegistry,
        repairer: ToolRepairer,
        max_repair_attempts: int,
        status: StatusCallback,
        fail: FailCallback,
        run_and_record: RunAndRecordCallback,
    ) -> None:
        self._session = session
        self._registry = registry
        self._repairer = repairer
        self._max_repair = max_repair_attempts
        self._status = status
        self._fail = fail
        self._run_and_record = run_and_record

    def handle(self, data: dict[str, Any]) -> None:
        """도구 수정 루프."""
        tool_name = data.get("tool_name", "")
        if not tool_name:
            self._fail("repair_tool", "tool_name 파라미터가 필요합니다.",
                       recovery="repair_tool 호출 시 tool_name 을 지정하세요. 등록되지 않은 도구는 generate_code 로 새로 작성.")
            return
        previous_errors = self._session.get_repair_errors(tool_name)
        self._status("repairing_tool", {
            "tool_name": tool_name,
            "attempt": len(previous_errors) + 1,
            "max_attempts": self._max_repair,
        })

        tool_info = self._registry.get_tool(tool_name)
        if tool_info is None:
            self._fail(tool_name, f"수정할 도구 '{tool_name}'을 찾을 수 없습니다.")
            return

        if len(previous_errors) >= self._max_repair:
            msg = (
                f"도구 '{tool_name}'의 수정을 {self._max_repair}회 시도했으나 실패했습니다.\n"
                f"마지막 에러: {previous_errors[-1] if previous_errors else '알 수 없음'}"
            )
            self._fail(tool_name, msg,
                       recovery="완전히 다른 접근이 필요합니다. generate_code로 새 코드를 작성하세요.")
            return

        last_input = self._registry.get_last_input(tool_name) or {}

        repair_result = self._repairer.repair(
            source_code=tool_info["code"], manifest=tool_info["manifest"],
            input_data=last_input,
            error_traceback=previous_errors[-1] if previous_errors else "",
            previous_errors=previous_errors, attempt=len(previous_errors) + 1,
            user_request=self._session.last_user_request,
        )

        if not repair_result.success:
            self._session.record_repair_error(tool_name, repair_result.error or "수정 실패")
            self._fail(tool_name, repair_result.error or "수정 실패", recoverable=True)
            return

        validation = validate_tool_code(repair_result.code)
        if not validation.valid:
            err = f"수정된 코드 검증 실패: {validation.reason}"
            self._session.record_repair_error(tool_name, err)
            self._fail(tool_name, err, recoverable=True)
            return

        self._registry.update_session_tool(tool_name, repair_result.code)
        self._run_and_record(tool_name, repair_result.code, last_input)
