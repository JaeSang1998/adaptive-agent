"""핵심 루프: 순수 tool loop 오케스트레이터."""

from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path
from typing import Any, Callable, TypedDict, cast

from adaptive_agent.agent.compaction import compact
from adaptive_agent.agent.meta_tools import META_TOOL_NAMES
from adaptive_agent.agent.planner import Planner, PlannerDecision
from adaptive_agent.agent.session import PlanStep, Session, ToolResult
from adaptive_agent.llm.client import LLMClientProtocol
from adaptive_agent.tools.builder import ToolBuilder
from adaptive_agent.tools.registry import ToolRegistry
from adaptive_agent.tools.repair import ToolRepairer
from adaptive_agent.tools.runner import ToolRunner
from adaptive_agent.tools.validator import validate_tool_code

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str, dict[str, Any]], None]
ApprovalCallback = Callable[[str, dict[str, Any]], bool]
AskUserCallback = Callable[[str, list[str] | None], str]


class FileSpec(TypedDict):
    """suggested_file/suggested_files 계약의 파일 스펙."""
    path: str
    content: str
    encoding: str


def _maybe_parse_structured(ref_path: str, raw: Any) -> Any:
    """$ref 로 가져온 raw output 을 path 확장자 기반으로 parse.

    JSON/CSV 는 builder 가 즉시 구조화 데이터로 받게 한다. 파싱 실패 시 raw string 반환.
    """
    if not isinstance(raw, str):
        return raw
    suffix = Path(ref_path).suffix.lower()
    try:
        if suffix == ".json":
            return json.loads(raw)
        if suffix == ".csv":
            return list(csv.DictReader(io.StringIO(raw)))
    except (json.JSONDecodeError, csv.Error):
        return raw
    return raw


def _resolve_refs(
    value: Any,
    session: Session,
    errors: list[str],
) -> Any:
    """input_data 안의 {"$ref": "<path>"} 를 session.observations 에서 lookup 해 dehydrate.

    재귀적으로 dict/list 안쪽까지 처리. JSON/CSV 는 자동 parse.
    Resolve 실패 시 errors 에 누적, 원본 ref dict 그대로 둠 (caller 가 에러 처리).
    """
    if isinstance(value, dict):
        value_dict = cast(dict[str, Any], value)
        # $ref dict 패턴: {"$ref": "<path>"}
        if set(value_dict.keys()) == {"$ref"} and isinstance(value_dict["$ref"], str):
            ref_path: str = value_dict["$ref"]
            obs = session.get_observation_by_path(ref_path)
            if obs is None:
                errors.append(
                    f"$ref 를 찾을 수 없습니다: '{ref_path}'. "
                    f"먼저 read_file로 해당 파일을 읽으세요."
                )
                return value_dict
            return _maybe_parse_structured(ref_path, obs.get("output", ""))
        # 일반 dict: 재귀
        return {k: _resolve_refs(v, session, errors) for k, v in value_dict.items()}
    if isinstance(value, list):
        value_list = cast(list[Any], value)
        return [_resolve_refs(item, session, errors) for item in value_list]
    return value


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
    """오케스트레이터. Planner 결정에 따라 tool을 실행."""

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
        compact(self._session, stage="planner")
        compact(self._session, stage="normal")

        self._status("planning", {"step": step})
        tool_descs, tool_notices = self._registry.get_tool_descriptions()
        workspace_context = self._workspace_context()

        decision = self._plan_with_fallback(tool_descs, tool_notices, workspace_context)

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
        input_data = _resolve_refs(raw_input, self._session, ref_errors)
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
        workspace_context: str,
    ) -> PlannerDecision:
        """planner 호출. timeout 시 context 축소 후 1회 재시도."""
        context = self._session.get_context_messages()
        try:
            return self._planner.decide(
                context,
                tool_descs,
                tool_notices=tool_notices,
                plan=self._session.plan or None,
                workspace_context=workspace_context,
            )
        except RuntimeError:
            logger.warning("Planner timeout — context 축소 후 재시도")
            compact(self._session, stage="aggressive")
            context = self._session.get_context_messages()
            return self._planner.decide(
                context,
                tool_descs,
                tool_notices=tool_notices,
                plan=self._session.plan or None,
                workspace_context=workspace_context,
            )

    def _execute_tool(self, tool_name: str, input_data: dict[str, Any]) -> None:
        """tool 실행. 결과를 세션에 추가."""
        # 모든 도구 호출에 using_tool 이벤트 emit (관측 가능성 일관성).
        # meta tool 도 observability 측면에서 "호출됨" 으로 기록되어야 함.
        if tool_name in META_TOOL_NAMES:
            self._status("using_tool", {"tool_name": tool_name, "input": input_data})
        match tool_name:
            case "think":
                self._handle_think(input_data)
            case "ask_user":
                self._handle_ask_user(input_data)
            case "generate_code":
                self._handle_generate_code(input_data)
            case "repair_tool":
                self._handle_repair_tool(input_data)
            case "update_plan":
                self._handle_update_plan(input_data)
            case _:
                self._handle_tool(tool_name, input_data)

    # -- 공통 헬퍼 -------------------------------------------------------------

    _DEFAULT_RECOVERY = (
        "\n복구 옵션: (1) generate_code로 다른 접근 시도 "
        "(2) update_plan으로 작업 분해 (3) ask_user로 도움 요청"
    )

    def _fail(self, tool_name: str, error: str, *, recoverable: bool = False, recovery: str = "") -> None:
        """실패 결과를 세션에 기록. recovery 힌트가 있으면 포함."""
        hint = recovery or (self._DEFAULT_RECOVERY if recoverable else "")
        msg = f"{error}\n{hint}" if hint else error
        self._session.add_tool_result(ToolResult(tool_name=tool_name, success=False, error=msg))
        self._status("tool_result", {"tool_name": tool_name, "success": False, "error": msg})

    def _run_and_record(self, tool_name: str, code: str, input_data: dict[str, Any]) -> None:
        """subprocess 실행 → 결과 기록 → 성공/실패 표시."""
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
                self._handle_tool("write_file", dict(suggested_file))
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

    # -- think ----------------------------------------------------------------

    def _handle_think(self, data: dict[str, Any]) -> None:
        """reasoning을 세션에 기록. LLM 호출 없음."""
        reasoning = data.get("reasoning", "")
        self._status("thinking", {"reasoning": reasoning})
        self._session.add_assistant_message(f"[추론] {reasoning}")

    # -- ask_user -------------------------------------------------------------

    def _handle_ask_user(self, data: dict[str, Any]) -> None:
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

    def _handle_update_plan(self, data: dict[str, Any]) -> None:
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
                    # planner 가 {"description": "...", "status": "..."} 형태로 emit 한 경우
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

    # -- 코드 생성 파이프라인 -------------------------------------------------

    def _handle_generate_code(self, data: dict[str, Any]) -> None:
        """코드 생성 + 실행. 성공 시 세션 도구로 등록 → 저장은 _offer_save에서."""
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
        input_data = self._prepare_code_input(data)

        self._status("generating_code", {"description": description})
        self._build_and_run_tool(description, input_data, tool_name=tool_name)

    def _resolve_tool_name(self, base_name: str) -> str:
        """이름 충돌 시 suffix 자동 부여."""
        if self._registry.get_tool(base_name) is None:
            return base_name
        for i in range(2, 100):
            candidate = f"{base_name}_{i}"
            if self._registry.get_tool(candidate) is None:
                return candidate
        return base_name

    def _build_and_run_tool(
        self,
        description: str,
        input_data: dict[str, Any],
        *,
        tool_name: str,
    ) -> None:
        """Builder → Validator → Registry 등록 → Runner 실행."""
        from adaptive_agent.tools.persistence import ToolPersistence

        user_request = self._find_last_user_request()

        build_result = self._builder.build(
            description, user_request,
            input_data=input_data,
        )

        if not build_result.success:
            self._fail(tool_name, f"빌드 실패: {build_result.error}",
                       recovery="description을 더 구체적으로 수정하거나 ask_user로 추가 정보를 요청하세요.")
            return

        tool_name = ToolPersistence.sanitize_name(tool_name)
        tool_name = self._resolve_tool_name(tool_name)

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

    # -- repair ---------------------------------------------------------------

    def _handle_repair_tool(self, data: dict[str, Any]) -> None:
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
            user_request=self._find_last_user_request(),
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

    # -- 일반 도구 실행 (builtin + session + persistent) ----------------------

    def _handle_tool(self, tool_name: str, input_data: dict[str, Any]) -> None:
        """registry에 등록된 도구 실행."""
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

    # -- 헬퍼 -----------------------------------------------------------------

    def _find_last_user_request(self) -> str:
        """handle_user_input 시점의 원본 요청 반환. ask_user 답변이 섞이지 않음."""
        return self._session.last_user_request

    def _prepare_code_input(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Planner 가 명시한 explicit input key 를 builder 에 전달.

        데이터는 Planner 가 `{"$ref": "<path>"}` (file) 또는 literal 값 (inline) 로
        명시. `_resolve_refs` 가 _run_step 에서 이미 dehydrate 한 값이 `data` 에 들어옴.
        builder 는 description 을 별도 인자로 받으므로 여기서 제외.
        """
        return {k: v for k, v in data.items() if k not in ("description", "tool_name")}

    @staticmethod
    def _is_visible_workspace_file(path: Path) -> bool:
        hidden_or_internal = {
            "sessions",
            "_tools",
            "worker.log",
            ".git",
            ".venv",
            "__pycache__",
        }
        if path.name in hidden_or_internal:
            return False
        if path.name.startswith(".") or path.name.startswith("_"):
            return False
        return path.is_file()

    def _candidate_workspace_files(self, suffixes: tuple[str, ...]) -> list[Path]:
        root = Path(".")
        try:
            entries = sorted(root.iterdir())
        except OSError:
            return []
        candidates: list[Path] = []
        for entry in entries:
            if not self._is_visible_workspace_file(entry):
                continue
            if suffixes and entry.suffix.lower() not in suffixes:
                continue
            candidates.append(entry)
        return candidates

    def _workspace_context(self) -> str:
        """planner grounding을 위한 현재 디렉터리 파일 스냅샷 + 원본 요청."""
        parts: list[str] = []
        if self._session.original_request:
            parts.append(f"원본 요청: {self._session.original_request}")
        entries = self._candidate_workspace_files(())
        if entries:
            names = [entry.name for entry in entries[:20]]
            if len(entries) > 20:
                names.append(f"... 외 {len(entries) - 20}개")
            parts.append("\n".join(names))
        return "\n".join(parts) if parts else "(표시할 파일 없음)"

