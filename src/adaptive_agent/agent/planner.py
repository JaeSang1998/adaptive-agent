"""Planner: LLM에게 다음 행동을 결정받는 모듈.

출력 경로 (dual-mode):
  1. Native tool calling (Ollama v0.20.3+):
     LLM이 tool_calls로 직접 도구 호출 → JSON 파싱 불필요, 안정적.
  2. Prompt-based fallback (구버전 Ollama / 미지원 모델):
     LLM이 텍스트로 JSON 출력 → json_parser로 추출·복구.
     json_repair 의존성은 이 fallback 경로를 위해 존재.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from adaptive_agent.agent.meta_tools import META_TOOL_SCHEMAS
from adaptive_agent.agent.session import PlanStep, format_plan_steps
from adaptive_agent.llm.client import LLMClientProtocol
from adaptive_agent.llm.json_parser import extract_and_heal_json
from adaptive_agent.llm.prompts import format_tool_descriptions, planner_messages, planner_system
from adaptive_agent.llm.schemas import ToolCall

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PlannerDecision:
    """Planner의 결정. tool_call이 있으면 도구 실행, 없으면 text가 최종 응답."""
    tool_call: dict[str, Any] | None  # {"tool": "...", "input": {...}}
    text: str | None  # 일반 텍스트 응답
    is_native_tool_call: bool = False  # native tool calling 경로로 생성된 결정인지


def _to_ollama_tools(tool_descriptions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """내부 도구 목록을 Ollama tools 파라미터 포맷으로 변환.

    Ollama 포맷: [{"type": "function", "function": {"name", "description", "parameters"}}]
    registry 도구 + meta 도구(meta_tools.META_TOOL_SCHEMAS)를 합산.
    """
    tools: list[dict[str, Any]] = []
    for desc in tool_descriptions:
        name: str = desc.get("name", "")
        params: dict[str, Any] = desc.get("parameters", {"type": "object", "properties": {}})
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": str(desc.get("description", "")),
                "parameters": params,
            },
        })
    tools.extend(META_TOOL_SCHEMAS)
    return tools


class Planner:
    def __init__(self, client: LLMClientProtocol) -> None:
        self._client = client

    def decide(
        self,
        conversation: list[dict[str, Any]],
        tool_descriptions: list[dict[str, Any]],
        *,
        tool_notices: list[str] | None = None,
        plan: list[PlanStep] | None = None,
        workspace_context: str = "",
    ) -> PlannerDecision:
        """대화 컨텍스트 + 툴 목록 + 계획 상태 → 다음 행동 결정.

        Native tool calling 우선 → 실패/미지원 시 prompt-based JSON fallback.
        tool_notices 는 tool list 와 분리되어 별도 섹션으로 렌더링됨.
        """
        tool_desc_str = format_tool_descriptions(tool_descriptions)
        plan_context = format_plan_steps(plan) if plan else ""
        system = planner_system(
            tool_desc_str,
            plan_context,
            workspace_context=workspace_context,
            tool_notices=tool_notices or [],
        )
        messages = planner_messages(system, conversation)

        # native tool calling이 지원되면 tools 파라미터 전달
        ollama_tools = _to_ollama_tools(tool_descriptions) if self._client.native_tools_supported else None
        response = self._client.chat(messages, tools=ollama_tools, phase="planner")

        # 경로 1: native tool_calls 응답
        if response.tool_calls:
            tc = response.tool_calls[0]  # 첫 번째 tool call만 사용 (한 턴에 하나)
            logger.debug("Native tool call: %s", tc.get("tool"))
            return PlannerDecision(tool_call=tc, text=None, is_native_tool_call=True)

        # 경로 2: prompt-based JSON fallback (텍스트에서 JSON 추출)
        # 빈 응답이면 temperature를 올려 1회 재시도
        if not response.content.strip():
            response = self._client.chat(messages, temperature=0.3, phase="planner")

        return self._parse(response.content)

    def _parse(self, content: str) -> PlannerDecision:
        """LLM 텍스트 응답 파싱: JSON이면 tool call, 아니면 텍스트 응답."""
        if not content or not content.strip():
            return PlannerDecision(tool_call=None, text="응답을 생성하지 못했습니다.")

        parsed = extract_and_heal_json(content, ToolCall)
        if parsed is not None and "tool" in parsed:
            return PlannerDecision(tool_call=parsed, text=None)

        # JSON 파싱 실패 = 일반 텍스트 응답
        return PlannerDecision(tool_call=None, text=content.strip())
