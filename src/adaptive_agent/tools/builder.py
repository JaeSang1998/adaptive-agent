"""코드 생성기: LLM으로 Python 코드를 생성."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from adaptive_agent.llm.client import LLMClientProtocol
from adaptive_agent.llm.code_extractor import extract_code
from adaptive_agent.llm.prompts import builder_code_messages

logger = logging.getLogger(__name__)


@dataclass
class BuildResult:
    success: bool
    code: str = ""
    error: str | None = None


ProgressCallback = Callable[[str], None]


class ToolBuilder:

    def __init__(
        self,
        client: LLMClientProtocol,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self._client = client
        self._progress: ProgressCallback = on_progress or (lambda _msg: None)

    def build(
        self,
        description: str,
        user_request: str,
        input_data: dict[str, Any] | None = None,
    ) -> BuildResult:
        """LLM으로 Python 코드 생성."""
        input_chars = len(str(input_data)) if input_data is not None else 0
        logger.info(
            "Tool build start desc_chars=%d request_chars=%d input_chars=%d",
            len(description),
            len(user_request),
            input_chars,
        )

        self._progress("Python 코드 생성 중...")
        messages = builder_code_messages(
            description, user_request,
            input_data=input_data,
        )

        try:
            response = self._client.chat(messages, phase="codegen")
        except Exception as e:
            return BuildResult(success=False, error=f"코드 생성 LLM 호출 실패: {e}")

        code = extract_code(response.content)
        if code:
            logger.info("Tool build ok code_chars=%d", len(code))
            return BuildResult(success=True, code=code)

        preview = " ".join(response.content.split())[:400]
        logger.warning("code 추출 실패 content_chars=%d preview=%s", len(response.content), preview)
        return BuildResult(
            success=False,
            error=(
                "LLM 응답에서 Python 코드를 추출할 수 없습니다. "
                f"응답 미리보기: {preview or '(빈 응답)'}"
            ),
        )
