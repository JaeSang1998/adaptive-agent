"""Pydantic 스키마: LLM 응답 validation 및 Ollama format 파라미터용."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ToolCall(BaseModel):
    """Planner의 도구 호출 스키마."""
    tool: str
    input: dict[str, Any] = {}
