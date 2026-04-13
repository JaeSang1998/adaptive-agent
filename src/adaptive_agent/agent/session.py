"""대화 히스토리 + 에이전트 상태 관리."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from adaptive_agent.limits import (
    SESSION_RESULT_CHARS,
    SESSION_RESULT_HEAD,
    SESSION_RESULT_TAIL,
)

logger = logging.getLogger(__name__)

# observations dict 의 hard cap. $ref resolution fallback store 라 무한 증가 방지용.
# typical 시나리오 (≤30 step) 에선 절대 발동 안 함. 100+ 턴 stress 시에만 oldest evict.
_MAX_OBSERVATIONS = 100


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_name: str
    success: bool
    output: Any = None
    error: str | None = None


@dataclass
class PlanStep:
    """계획의 개별 단계."""

    content: str
    status: str = "pending"  # pending | in_progress | completed


@dataclass
class Session:
    """한 세션의 대화 상태."""

    messages: list[dict[str, Any]] = field(default_factory=lambda: [])
    temp_tools: dict[str, Any] = field(default_factory=lambda: {})
    repair_history: dict[str, list[str]] = field(default_factory=lambda: {})
    current_step: int = 0
    successful_tools: set[str] = field(default_factory=lambda: set[str]())  # 성공한 도구 이름들
    plan: list[PlanStep] = field(default_factory=lambda: [])
    recent_actions: list[str] = field(default_factory=lambda: [])
    last_user_request: str = ""  # handle_user_input 시점의 원본 요청 (ask_user 답변 제외)
    original_request: str = ""  # 세션 첫 사용자 메시지 (불변 보존, compaction에서도 유실 안 됨)
    native_tools: bool = False  # native tool calling 활성 여부
    # 단일 source: insertion-order 가 최신 순이며 path 또는 synthetic key 로 lookup.
    # `get_observation_by_path()` 는 path 기반 조회. record_observation 은 LRU 처럼
    # 같은 key 재기록 시 dict 끝으로 이동.
    observations: dict[str, dict[str, Any]] = field(default_factory=lambda: {})

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def add_assistant_tool_call(self, tool_call: dict[str, Any]) -> None:
        """Native tool calling: 내부 포맷 tool_call → Ollama 프로토콜 형식으로 히스토리에 추가."""
        self.messages.append({
            "role": "assistant",
            "tool_calls": [{
                "function": {
                    "name": tool_call.get("tool", ""),
                    "arguments": tool_call.get("input", {}),
                },
            }],
        })

    def add_tool_result(self, result: ToolResult) -> None:
        """도구 실행 결과를 대화 히스토리에 추가. 큰 결과는 truncate."""
        if result.success:
            output = str(result.output or "")
            if len(output) > SESSION_RESULT_CHARS:
                kept = SESSION_RESULT_HEAD + SESSION_RESULT_TAIL
                omitted = len(output) - kept
                output = (
                    output[:SESSION_RESULT_HEAD]
                    + f"\n...[{omitted}자 생략 — read_file의 offset/limit로 부분 읽기하거나 generate_code로 처리하세요]...\n"
                    + output[-SESSION_RESULT_TAIL:]
                )
            content = f"[도구 {result.tool_name} 실행 성공]\n{output}"
        else:
            content = f"[도구 {result.tool_name} 실행 실패]\n{result.error}"

        if self.native_tools:
            self.messages.append({
                "role": "tool",
                "tool_name": result.tool_name,
                "content": content,
            })
        else:
            self.messages.append({"role": "user", "content": content})

    def record_repair_error(self, tool_name: str, error: str) -> None:
        self.repair_history.setdefault(tool_name, []).append(error)

    def get_repair_errors(self, tool_name: str) -> list[str]:
        return self.repair_history.get(tool_name, [])

    def mark_tool_success(self, tool_name: str) -> None:
        """도구 실행 성공 기록. 저장 제안 시 성공 여부 판단에 사용."""
        self.successful_tools.add(tool_name)

    def get_context_messages(self) -> list[dict[str, Any]]:
        """Planner에 전달할 대화 컨텍스트."""
        return list(self.messages)

    def record_observation(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        output: Any,
    ) -> None:
        """generate_code 가 $ref 로 재사용할 원시 데이터를 누적.

        호출자(core)가 Registry.is_observation_producer() 로 사전 필터링 후 호출.
        같은 key 가 다시 기록되면 dict 끝으로 이동(move-to-end)해 최신 순서 유지.
        path 가 명시된 경우 그 path 가 key, 아니면 `<{tool_name}>` synthetic key.
        """
        if output is None:
            return

        observation: dict[str, Any] = {
            "tool_name": tool_name,
            "output": output,
        }
        path = input_data.get("path")
        if isinstance(path, str) and path:
            observation["path"] = path
        pattern = input_data.get("pattern")
        if isinstance(pattern, str) and pattern:
            observation["pattern"] = pattern

        key = path if isinstance(path, str) and path else f"<{tool_name}>"
        if key in self.observations:
            del self.observations[key]
        self.observations[key] = observation

        # hard cap (oldest evict). $ref resolution 은 lookup 시 None graceful 처리됨.
        while len(self.observations) > _MAX_OBSERVATIONS:
            oldest = next(iter(self.observations))
            del self.observations[oldest]
            logger.debug("observation evicted (cap=%d): %s", _MAX_OBSERVATIONS, oldest)

    def get_observation_by_path(self, path: str) -> dict[str, Any] | None:
        """path-keyed observation lookup. $ref resolver 가 사용."""
        return self.observations.get(path)

    # 반복해도 결과가 같은 도구만 stuck 감지 대상.
    # 읽기 전용(read_file 등)·제어 도구(repair_tool 등)는 반복이 자연스럽다.
    # update_plan 은 같은 plan 반복은 비정상이라 stuck 감지에 포함.
    _STUCK_EXCLUDED = frozenset({
        "repair_tool", "think", "ask_user",                       # 제어/메타
        "read_file", "list_directory", "glob_search", "grep_search",  # 읽기 전용
    })

    def record_action(self, tool_name: str, input_data: dict[str, Any]) -> bool:
        """행동 기록. 제외 대상이 아닌 같은 행동이 3회 연속 반복되면 True."""
        if tool_name in self._STUCK_EXCLUDED:
            return False
        try:
            canon = json.dumps(input_data, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            canon = str(input_data)
        key = f"{tool_name}:{canon}"
        self.recent_actions.append(key)
        if len(self.recent_actions) > 10:
            self.recent_actions.pop(0)
        return (
            len(self.recent_actions) >= 3
            and self.recent_actions[-1] == self.recent_actions[-2] == self.recent_actions[-3]
        )

    def format_plan(self) -> str:
        """현재 계획 상태를 문자열로 포맷."""
        if not self.plan:
            return "(계획 없음)"
        return format_plan_steps(self.plan)

    def reset_step(self) -> None:
        self.current_step = 0
        # user turn 전환 시 stuck 검출 윈도우도 초기화. 이전 turn 의 도구 호출 history
        # 가 다음 turn 에 잘못 stuck 으로 잡히는 false-positive 방지.
        self.recent_actions.clear()

    def increment_step(self) -> int:
        self.current_step += 1
        return self.current_step


def format_plan_steps(plan: list[PlanStep]) -> str:
    """PlanStep 리스트를 사람이 읽을 수 있는 문자열로 포맷."""
    icons = {"pending": "○", "in_progress": "▶", "completed": "✓"}
    lines: list[str] = []
    for i, step in enumerate(plan):
        icon = icons.get(step.status, "○")
        lines.append(f"{icon} {i}. {step.content}")
    return "\n".join(lines)
