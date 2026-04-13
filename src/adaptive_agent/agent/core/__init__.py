"""핵심 루프: 순수 tool loop 오케스트레이터.

god-object 였던 단일 `core.py` (660 LOC) 를 책임 단위로 분할 (ADR-005):
  - `__init__.py` — `AgentCore` 클래스 + `_run_loop` + dispatch + 공통 유틸
  - `refs.py`     — `$ref` dehydration 자유 함수
  - `workspace.py` — planner grounding 디렉토리 스냅샷
  - `codegen.py`  — `CodeGenHandler` (generate_code 파이프라인)
  - `repair.py`   — `RepairHandler` (repair_tool 루프)
  - `meta.py`     — `MetaHandlers` (think / ask_user / update_plan / 일반 도구)

AgentCore 는 dispatch + 공통 콜백 (run_and_record, fail) 만 들고, 실제 도구별
핸들러는 composition 으로 주입. AgentContext 는 명시적 dataclass 가 아니라
"필요한 것만 handler 에 직접 inject" 패턴 — 의존성 명시는 handler 생성자에서.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, TypedDict, cast

from adaptive_agent.agent.compaction import compact
from adaptive_agent.agent.core.codegen import CodeGenHandler
from adaptive_agent.agent.core.meta import MetaHandlers
from adaptive_agent.agent.core.refs import resolve_refs
from adaptive_agent.agent.core.repair import RepairHandler
from adaptive_agent.agent.core.workspace import workspace_context
from adaptive_agent.agent.events import EventType
from adaptive_agent.agent.meta_tools import META_TOOL_NAMES
from adaptive_agent.agent.planner import Planner, PlannerDecision
from adaptive_agent.agent.session import Session, ToolResult
from adaptive_agent.llm.client import LLMClientProtocol
from adaptive_agent.tools.builder import ToolBuilder
from adaptive_agent.tools.registry import ToolRegistry
from adaptive_agent.tools.repair import ToolRepairer
from adaptive_agent.tools.runner import ToolRunner

logger = logging.getLogger(__name__)

StatusCallback = Callable[[EventType, dict[str, Any]], None]
ApprovalCallback = Callable[[str, dict[str, Any]], bool]
AskUserCallback = Callable[[str, list[str] | None], str]


class FileSpec(TypedDict):
    """suggested_file/suggested_files 계약의 파일 스펙."""
    path: str
    content: str
    encoding: str


def _default_ask_user(question: str, choices: list[str] | None) -> str:
    """기본 ask_user 콜백. CLI에서 직접 input() 호출."""
    if choices:
        lines = [question]
        for i, choice in enumerate(choices, 1):
            lines.append(f"  {i}. {choice}")
        try:
            answer = input("\n".join(lines) + "\n> ").strip()
        except EOFError:
            answer = ""
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1]
        return answer or "(응답 없음)"
    try:
        return input(f"  {question}\n> ").strip() or "(응답 없음)"
    except EOFError:
        return "(응답 없음)"


class AgentCore:
    """오케스트레이터. Planner 결정에 따라 tool을 실행.

    내부적으로 codegen / repair / meta handler 를 composition 으로 보유.
    `_execute_tool` 의 match 분기가 dispatch entry point — 한 화면에서 모든
    상태 전이가 보인다.
    """

    _DEFAULT_RECOVERY = (
        "\n복구 옵션: (1) generate_code로 다른 접근 시도 "
        "(2) update_plan으로 작업 분해 (3) ask_user로 도움 요청"
    )

    def __init__(
        self,
        planner: Planner,
        session: Session,
        registry: ToolRegistry,
        client: LLMClientProtocol,
        *,
        max_steps: int = 15,
        max_repair_attempts: int = 3,
        status_callback: StatusCallback | None = None,
        approval_callback: ApprovalCallback | None = None,
        ask_user_callback: AskUserCallback | None = None,
    ) -> None:
        self._planner = planner
        self._session = session
        self._registry = registry
        self._max_steps = max_steps
        self._max_repair = max_repair_attempts
        self._status: StatusCallback = status_callback or (lambda _e, _d: None)
        self._approval: ApprovalCallback = approval_callback or (lambda _n, _d: True)
        self._ask_user: AskUserCallback = ask_user_callback or _default_ask_user

        self._runner = ToolRunner()
        self._builder = ToolBuilder(
            client, on_progress=lambda msg: self._status("build_progress", {"message": msg}),
        )
        self._repairer = ToolRepairer(client)

        # Composition handlers — 모든 handler 가 같은 session/registry/status/fail/run_and_record 를 공유.
        self._codegen = CodeGenHandler(
            session=session, registry=registry, builder=self._builder,
            status=self._status, fail=self._fail, run_and_record=self._run_and_record,
        )
        self._repair = RepairHandler(
            session=session, registry=registry, repairer=self._repairer,
            max_repair_attempts=max_repair_attempts,
            status=self._status, fail=self._fail, run_and_record=self._run_and_record,
        )
        self._meta = MetaHandlers(
            session=session, registry=registry,
            status=self._status, approval=self._approval, ask_user=self._ask_user,
            fail=self._fail, run_and_record=self._run_and_record,
        )

    def handle_user_input(self, user_input: str) -> str | None:
        """사용자 입력 처리. 최종 응답 문자열 반환."""
        if not user_input.strip():
            msg = "무엇을 도와드릴까요? 작업을 설명해 주세요."
            self._session.add_assistant_message(msg)
            return msg

        self._session.add_user_message(user_input)
        self._session.last_user_request = user_input
        if not self._session.original_request:
            self._session.original_request = user_input
        self._session.reset_step()

        return self._run_loop()

    def _run_loop(self) -> str | None:
        """Planner → tool 실행 → 관찰 루프."""
        while True:
            step = self._session.increment_step()
            if step > self._max_steps:
                return f"최대 {self._max_steps} 단계에 도달했습니다. 여기까지의 결과를 정리합니다."

            try:
                result = self._run_step(step)
                if result is not None:
                    return result
            except KeyboardInterrupt:
                raise
            except Exception:
                logger.exception("step %d 내부 오류", step)
                raise

    def _run_step(self, step: int) -> str | None:
        """단일 step 실행. 정상 종료 시 응답 문자열, 계속이면 None 반환."""
        compact(self._session)

        self._status("planning", {"step": step})
        tool_descs, tool_notices = self._registry.get_tool_descriptions()
        ws_context = workspace_context(self._session)

        decision = self._plan_with_fallback(tool_descs, tool_notices, ws_context)

        # 텍스트 응답 = 루프 종료
        if decision.text is not None:
            self._session.add_assistant_message(decision.text)
            return decision.text

        # tool call 실행
        tool_call = decision.tool_call
        if tool_call is None:
            return "도구 호출을 생성하지 못했습니다."

        # Native tool calling: assistant의 tool_calls를 히스토리에 추가
        if decision.is_native_tool_call:
            self._session.native_tools = True
            self._session.add_assistant_tool_call(tool_call)

        tool_name = tool_call.get("tool", "")
        raw_input = tool_call.get("input", {})

        # $ref resolution: planner 가 {"$ref": "<path>"} 로 데이터 참조
        ref_errors: list[str] = []
        input_data = resolve_refs(raw_input, self._session, ref_errors)
        if ref_errors:
            error_msg = " | ".join(ref_errors)
            self._session.add_user_message(
                f"[시스템] 입력 reference 해석 실패: {error_msg}"
            )
            self._status("tool_result", {
                "tool_name": tool_name, "success": False, "error": error_msg, "output": "",
            })
            return None

        if self._session.record_action(tool_name, input_data):
            # 같은 도구+입력 3회 연속 반복 → 실행 스킵, 기존 결과로 응답 강제
            self._session.add_user_message(
                "[시스템] 같은 도구를 같은 입력으로 반복 호출하고 있습니다. "
                "이전 실행 결과를 바탕으로 사용자에게 텍스트로 응답하세요. "
                "도구를 다시 호출하지 마세요."
            )
            return None

        self._execute_tool(tool_name, input_data)
        return None

    def _plan_with_fallback(
        self,
        tool_descs: list[dict[str, Any]],
        tool_notices: list[str],
        ws_context: str,
    ) -> PlannerDecision:
        """planner 호출. timeout 은 상위 step 예외 처리로 propagate."""
        context = self._session.get_context_messages()
        return self._planner.decide(
            context,
            tool_descs,
            tool_notices=tool_notices,
            plan=self._session.plan or None,
            workspace_context=ws_context,
        )

    def _execute_tool(self, tool_name: str, input_data: dict[str, Any]) -> None:
        """tool 실행. 결과를 세션에 추가.

        meta tool 은 handler 가 _status 를 직접 부르지 않는 분기가 있어 여기서
        미리 emit. 일반 도구는 MetaHandlers.handle_tool 안에서 approval 통과
        후 emit (거부 시 emit 안 됨).
        """
        if tool_name in META_TOOL_NAMES:
            self._status("using_tool", {"tool_name": tool_name, "input": input_data})
        match tool_name:
            case "think":
                self._meta.handle_think(input_data)
            case "ask_user":
                self._meta.handle_ask_user(input_data)
            case "generate_code":
                self._codegen.handle(input_data)
            case "repair_tool":
                self._repair.handle(input_data)
            case "update_plan":
                self._meta.handle_update_plan(input_data)
            case _:
                self._meta.handle_tool(tool_name, input_data)

    # -- 공통 콜백 (handler 들이 주입받음) -------------------------------------

    def _fail(self, tool_name: str, error: str, *, recoverable: bool = False, recovery: str = "") -> None:
        """실패 결과를 세션에 기록. recovery 힌트가 있으면 포함."""
        hint = recovery or (self._DEFAULT_RECOVERY if recoverable else "")
        msg = f"{error}\n{hint}" if hint else error
        self._session.add_tool_result(ToolResult(tool_name=tool_name, success=False, error=msg))
        self._status("tool_result", {"tool_name": tool_name, "success": False, "error": msg})

    def _run_and_record(self, tool_name: str, code: str, input_data: dict[str, Any]) -> None:
        """subprocess 실행 → 결과 기록 → 성공/실패 표시.

        suggested_file(s) dispatch 는 일반 tool handler (`_meta.handle_tool`) 로
        write_file 호출 — 사용자 approval gate 를 자동 통과.
        """
        run_result = self._runner.run(code, input_data)
        self._session.add_tool_result(ToolResult(
            tool_name=tool_name, success=run_result.success,
            output=run_result.output, error=run_result.error,
        ))
        self._status("tool_result", {
            "tool_name": tool_name, "success": run_result.success,
            "error": run_result.error, "output": run_result.output,
        })
        if run_result.success:
            self._session.mark_tool_success(tool_name)
            output_for_registry = (
                run_result.parsed_output
                if run_result.parsed_output is not None
                else run_result.output
            )
            self._registry.record_last_output(tool_name, output_for_registry)
            suggested_files = self._extract_suggested_files(run_result.parsed_output)
            for suggested_file in suggested_files:
                self._status("suggested_file_detected", {
                    "tool_name": tool_name,
                    "path": suggested_file["path"],
                })
                self._meta.handle_tool("write_file", dict(suggested_file))
        else:
            self._session.record_repair_error(tool_name, run_result.error or "")

    @staticmethod
    def _extract_file_spec(raw: object) -> FileSpec | None:
        """도구 출력에서 파일 스펙 추출. raw 는 LLM 생성 dict 라 object 로 받고 narrow."""
        if not isinstance(raw, dict):
            return None
        raw_dict = cast(dict[str, object], raw)

        path = str(raw_dict.get("path", ""))
        if not path.strip():
            return None

        encoding = str(raw_dict.get("encoding") or "utf-8")
        raw_content = raw_dict.get("content", "")
        content = (
            raw_content
            if isinstance(raw_content, str)
            else json.dumps(raw_content, ensure_ascii=False, indent=2)
        )
        return FileSpec(path=path, content=content, encoding=encoding)

    def _extract_suggested_files(self, parsed_output: object) -> list[FileSpec]:
        """생성 코드가 제안한 단일/다중 파일 출력 계약을 추출."""
        if not isinstance(parsed_output, dict):
            return []
        out = cast(dict[str, object], parsed_output)

        extracted: list[FileSpec] = []
        single = self._extract_file_spec(out.get("suggested_file"))
        if single is not None:
            extracted.append(single)

        raw_multi = out.get("suggested_files")
        if isinstance(raw_multi, list):
            for item in cast(list[object], raw_multi):
                file_spec = self._extract_file_spec(item)
                if file_spec is not None:
                    extracted.append(file_spec)

        return extracted

