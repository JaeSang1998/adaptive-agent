"""자가 수정: 실패한 코드 + traceback → LLM 수정 → 재실행."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adaptive_agent.llm.client import LLMClientProtocol
from adaptive_agent.llm.prompts import repair_messages
from adaptive_agent.llm.code_extractor import extract_code


@dataclass
class RepairResult:
    success: bool
    code: str = ""
    error: str | None = None


class ToolRepairer:
    def __init__(self, client: LLMClientProtocol) -> None:
        self._client = client

    def repair(
        self,
        source_code: str,
        manifest: dict[str, Any],
        input_data: dict[str, Any],
        error_traceback: str,
        previous_errors: list[str],
        attempt: int,
        user_request: str = "",
    ) -> RepairResult:
        """LLM에게 코드 수정 요청."""
        messages = repair_messages(
            source_code=source_code,
            error_traceback=error_traceback,
            manifest=manifest,
            input_data=input_data,
            previous_errors=previous_errors,
            attempt=attempt,
            user_request=user_request,
        )

        try:
            response = self._client.chat(messages, phase="repair")
        except Exception as e:
            return RepairResult(success=False, error=f"LLM 호출 실패: {e}")

        code = extract_code(response.content)
        if not code:
            return RepairResult(success=False, error="LLM 응답에서 Python 코드를 추출할 수 없습니다.")

        return RepairResult(success=True, code=code)
