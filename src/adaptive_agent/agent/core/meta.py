"""메타 도구 + 일반 도구 dispatch 핸들러.

think / ask_user / update_plan 의 메타 도구와 builtin / persistent / session
도구를 한 클래스에서 처리. 메타 도구는 LLM 호출 없이 session 상태만 갱신.
일반 도구는 approval gate → execute_builtin 또는 _registry.get_tool 후 run_and_record.
"""

from __future__ import annotations

from typing import Any, Callable, cast

from adaptive_agent.agent.events import EventType
from adaptive_agent.agent.session import PlanStep, Session
from adaptive_agent.tools.registry import ToolRegistry

StatusCallback = Callable[[EventType, dict[str, Any]], None]
ApprovalCallback = Callable[[str, dict[str, Any]], bool]
AskUserCallback = Callable[[str, list[str] | None], str]
FailCallback = Callable[..., None]
RunAndRecordCallback = Callable[[str, str, dict[str, Any]], None]


class MetaHandlers:
    def __init__(
        self,
        *,
        session: Session,
        registry: ToolRegistry,
        status: StatusCallback,
        approval: ApprovalCallback,
        ask_user: AskUserCallback,
        fail: FailCallback,
        run_and_record: RunAndRecordCallback,
    ) -> None:
        self._session = session
        self._registry = registry
        self._status = status
        self._approval = approval
        self._ask_user = ask_user
        self._fail = fail
        self._run_and_record = run_and_record

    # -- think ----------------------------------------------------------------

    def handle_think(self, data: dict[str, Any]) -> None:
        """reasoning을 세션에 기록. LLM 호출 없음."""
        reasoning = data.get("reasoning", "")
        self._status("thinking", {"reasoning": reasoning})
        self._session.add_assistant_message(f"[추론] {reasoning}")

    # -- ask_user -------------------------------------------------------------

    def handle_ask_user(self, data: dict[str, Any]) -> None:
        """사용자에게 질문. 선택지가 있으면 번호로 선택."""
        question = str(data.get("question", "추가 정보가 필요합니다."))
        raw_choices = data.get("choices")
        choices: list[str] | None = None
        if isinstance(raw_choices, list):
            items = cast(list[Any], raw_choices)
            choices = [str(c) for c in items]

        self._status("ask_user", {"question": question, "choices": choices})

        answer = self._ask_user(question, choices)

        self._session.add_assistant_message(question)
        self._session.add_user_message(answer or "(응답 없음)")

    # -- update_plan -----------------------------------------------------------

    def handle_update_plan(self, data: dict[str, Any]) -> None:
        """계획 생성·갱신. 새 단계 추가, 상태 변경, 단계 수정을 처리."""
        raw_steps = data.get("steps")
        completed = data.get("completed", [])
        in_progress = data.get("in_progress")

        if raw_steps and isinstance(raw_steps, list):
            raw_items = cast(list[Any], raw_steps)
            steps: list[str] = []
            for s in raw_items:
                if isinstance(s, str):
                    steps.append(s)
                elif isinstance(s, dict):
                    step_dict = cast(dict[str, Any], s)
                    desc = step_dict.get("description") or step_dict.get("content") or step_dict.get("step")
                    if isinstance(desc, str) and desc:
                        steps.append(desc)
                    else:
                        steps.append(str(step_dict))
                else:
                    steps.append(str(s))
            self._session.plan = [PlanStep(content=s) for s in steps]
            self._status("plan_updated", {"action": "created", "steps": steps})

        for idx in completed:
            if isinstance(idx, int) and 0 <= idx < len(self._session.plan):
                self._session.plan[idx].status = "completed"

        if isinstance(in_progress, int) and 0 <= in_progress < len(self._session.plan):
            for step in self._session.plan:
                if step.status == "in_progress":
                    step.status = "pending"
            self._session.plan[in_progress].status = "in_progress"

        self._session.add_assistant_message(f"[계획 갱신]\n{self._session.format_plan()}")

    # -- 일반 도구 실행 (builtin + session + persistent) ----------------------

    def handle_tool(self, tool_name: str, input_data: dict[str, Any]) -> None:
        """registry 에 등록된 도구 실행."""
        if self._registry.requires_approval(tool_name):
            approval_data = self._registry.build_approval_data(tool_name, input_data)
            if not self._approval(tool_name, approval_data):
                self._fail(tool_name, "사용자가 실행을 거부했습니다.")
                return

        self._status("using_tool", {"tool_name": tool_name, "input": input_data})

        # built-in 도구
        builtin_result = self._registry.execute_builtin(tool_name, input_data)
        if builtin_result is not None:
            self._session.add_tool_result(builtin_result)
            self._status("tool_result", {
                "tool_name": tool_name, "success": builtin_result.success,
                "error": builtin_result.error, "output": builtin_result.output,
            })
            if builtin_result.success:
                self._registry.record_last_output(tool_name, builtin_result.output)
                if self._registry.is_observation_producer(tool_name):
                    self._session.record_observation(tool_name, input_data, builtin_result.output)
            return

        # 생성/persistent 도구
        tool_info = self._registry.get_tool(tool_name)
        if tool_info is None:
            self._fail(tool_name, f"도구 '{tool_name}'을 찾을 수 없습니다.")
            return

        self._registry.record_last_input(tool_name, input_data)
        self._run_and_record(tool_name, tool_info["code"], input_data)
