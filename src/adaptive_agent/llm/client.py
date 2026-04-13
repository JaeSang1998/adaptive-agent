"""LLM 클라이언트: Protocol 추상화 + Ollama 네이티브 구현체.

설계 결정:
  - LLMClientProtocol로 provider 교체 지점 명확화.
    OllamaClient가 유일한 구현체이지만, Protocol만 구현하면
    OpenAI/Anthropic 등으로 전환 가능.
  - Native tool calling 우선, prompt-based JSON fallback:
    Ollama v0.20.3+에서 gemma4 native tool calling 안정화.
    구버전 Ollama 또는 미지원 모델은 자동 감지 후 prompt-based로 fallback.
    json_repair + multi-strategy 파서는 이 fallback 경로를 지원.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import json

import httpx

from adaptive_agent.config import Config

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    thinking: str | None
    usage: dict[str, int]
    tool_calls: list[dict[str, Any]] = field(default_factory=lambda: [])


@runtime_checkable
class LLMClientProtocol(Protocol):
    """LLM 클라이언트 인터페이스.

    provider 교체 시 이 Protocol만 구현하면 됨.
    Planner, Builder, Repairer 등 소비자는 이 Protocol에만 의존.
    """

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
        think: bool = False,
        json_schema: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        phase: str = "default",
    ) -> LLMResponse: ...

    @property
    def native_tools_supported(self) -> bool: ...

    def close(self) -> None: ...


class OllamaClient:
    """Ollama 네이티브 /api/chat 클라이언트.

    Native tool calling (Ollama v0.20.3+):
      첫 호출 시 tools 파라미터를 포함하여 시도.
      모델/버전이 tool calling을 지원하지 않으면 자동 감지 후
      이후 호출에서 tools를 생략 (prompt-based JSON fallback).
    """

    def __init__(self, config: Config) -> None:
        self._config = config.llm
        self._http = httpx.Client(
            base_url=self._config.base_url,
            timeout=httpx.Timeout(self._config.request_timeout_seconds, connect=10.0),
        )
        # None = 아직 감지 안 됨, True/False = 감지 완료
        self._native_tools_supported: bool | None = (
            None if self._config.enable_native_tools else False
        )

    @property
    def native_tools_supported(self) -> bool:
        """Native tool calling 지원 여부. 첫 호출 전에는 True로 가정."""
        return self._native_tools_supported is not False

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
        think: bool = False,
        json_schema: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        phase: str = "default",
    ) -> LLMResponse:
        """동기 chat completion 호출.

        tools가 주어지고 native tool calling이 지원되면 Ollama tools 파라미터 사용.
        지원 안 되면 tools 무시하고 prompt-based로 진행.
        """
        effective_max_tokens = self._resolve_max_tokens(phase, max_tokens)
        effective_timeout = self._resolve_timeout(phase, timeout_seconds)

        # native tool calling 미지원 확인된 경우 tools 무시
        effective_tools = tools if (tools and self._native_tools_supported is not False) else None
        mode = "native" if effective_tools else ("fallback" if tools else "text")
        message_chars = sum(len(str(m.get("content", ""))) for m in messages)

        body = self._build_body(
            messages,
            temperature=temperature,
            max_tokens=effective_max_tokens,
            think=think,
            json_schema=json_schema,
            tools=effective_tools,
        )

        started_at = time.monotonic()
        logger.info(
            "LLM chat start phase=%s mode=%s timeout=%.1fs max_tokens=%d messages=%d message_chars=%d",
            phase,
            mode,
            effective_timeout,
            effective_max_tokens,
            len(messages),
            message_chars,
        )
        resp = self._request_with_retry(
            body,
            tools=effective_tools,
            timeout_seconds=effective_timeout,
            phase=phase,
            max_retries=self._resolve_retries(phase),
        )
        message = resp.get("message", {})
        content = message.get("content", "")
        thinking = message.get("thinking") or None

        # native tool_calls 파싱
        raw_tool_calls: list[dict[str, Any]] = message.get("tool_calls") or []
        tool_calls: list[dict[str, Any]] = self._parse_tool_calls(raw_tool_calls)

        # capability detection: 첫 시도 결과로 지원 여부 확정
        if tools and self._native_tools_supported is None:
            if tool_calls:
                self._native_tools_supported = True
                logger.info("Native tool calling 지원 확인")
            # tool_calls 없어도 content가 있으면 모델이 텍스트로 응답한 것 (정상)
            # 실패는 _request_with_retry에서 처리됨

        usage = self._extract_usage(resp)
        duration = time.monotonic() - started_at
        logger.info(
            "LLM chat ok phase=%s mode=%s duration=%.2fs tool_calls=%d prompt_tokens=%d completion_tokens=%d",
            phase,
            mode,
            duration,
            len(tool_calls),
            usage["prompt_tokens"],
            usage["completion_tokens"],
        )

        return LLMResponse(
            content=content,
            thinking=thinking,
            usage=usage,
            tool_calls=tool_calls,
        )

    # -- internal --

    def _build_body(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        think: bool = False,
        json_schema: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        temp = temperature if temperature is not None else self._config.temperature
        body: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temp,
                "seed": self._config.seed,
                "num_predict": max_tokens or self._config.max_tokens,
                "num_ctx": self._config.num_ctx,
            },
        }
        body["think"] = bool(think)
        if json_schema is not None:
            body["format"] = json_schema
        if tools:
            body["tools"] = tools
        return body

    def _resolve_max_tokens(self, phase: str, requested: int | None) -> int:
        if requested is not None:
            return requested
        return {
            "planner": self._config.planner_max_tokens,
            "codegen": self._config.code_max_tokens,
            "repair": self._config.repair_max_tokens,
        }.get(phase, self._config.max_tokens)

    def _resolve_timeout(self, phase: str, requested: float | None) -> float:
        if requested is not None:
            return requested
        return {
            "planner": self._config.planner_timeout_seconds,
            "codegen": self._config.code_timeout_seconds,
            "repair": self._config.repair_timeout_seconds,
        }.get(phase, self._config.request_timeout_seconds)

    @staticmethod
    def _resolve_retries(phase: str) -> int:
        return {
            "codegen": 1,
            "repair": 2,
            "planner": 3,
        }.get(phase, 3)

    @staticmethod
    def _build_timeout(timeout_seconds: float) -> httpx.Timeout:
        connect_timeout = min(timeout_seconds, 10.0)
        return httpx.Timeout(timeout_seconds, connect=connect_timeout)

    @staticmethod
    def _preview_text(text: str, limit: int = 300) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit] + "..."

    @staticmethod
    def _extract_usage(resp: dict[str, Any]) -> dict[str, int]:
        """Ollama 네이티브 응답에서 usage 정보 추출."""
        return {
            "prompt_tokens": resp.get("prompt_eval_count", 0),
            "completion_tokens": resp.get("eval_count", 0),
        }

    @staticmethod
    def _parse_tool_calls(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Ollama tool_calls 응답을 내부 포맷으로 변환.

        Ollama 형태: [{"function": {"name": "...", "arguments": {...}}}]
        내부 형태:   [{"tool": "...", "input": {...}}]
        """
        result: list[dict[str, Any]] = []
        for tc in raw:
            func: dict[str, Any] = tc.get("function", {})
            name: str = func.get("name", "")
            args: dict[str, Any] = func.get("arguments", {})
            if name:
                result.append({"tool": name, "input": args})
        return result

    def _request_with_retry(
        self,
        body: dict[str, Any],
        *,
        tools: list[dict[str, Any]] | None = None,
        timeout_seconds: float | None = None,
        phase: str = "default",
        max_retries: int = 3,
    ) -> dict[str, Any]:
        last_err: Exception | None = None
        request_timeout = self._build_timeout(
            timeout_seconds or self._config.request_timeout_seconds,
        )
        for attempt in range(max_retries):
            try:
                resp = self._http.post("/api/chat", json=body, timeout=request_timeout)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "LLM HTTP error phase=%s attempt=%d/%d status=%d body=%s",
                    phase,
                    attempt + 1,
                    max_retries,
                    exc.response.status_code,
                    self._preview_text(exc.response.text),
                )
                # 구버전 Ollama가 tools 파라미터를 모르면 4xx 반환 가능
                if tools and exc.response.status_code in (400, 422) and self._native_tools_supported is None:
                    logger.warning(
                        "Native tool calling 미지원 감지 phase=%s (HTTP %d). prompt-based fallback 전환.",
                        phase,
                        exc.response.status_code,
                    )
                    self._native_tools_supported = False
                    # tools 제거 후 재시도
                    body_without_tools = {k: v for k, v in body.items() if k != "tools"}
                    return self._request_with_retry(
                        body_without_tools,
                        timeout_seconds=timeout_seconds,
                        phase=phase,
                        max_retries=max_retries - attempt,
                    )
                last_err = exc
                if attempt < max_retries - 1:
                    time.sleep(min(2 ** attempt, 8))
            except (httpx.TransportError, json.JSONDecodeError) as exc:
                logger.warning(
                    "LLM transport error phase=%s attempt=%d/%d error=%s",
                    phase,
                    attempt + 1,
                    max_retries,
                    exc,
                )
                last_err = exc
                if attempt < max_retries - 1:
                    time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(f"LLM API 호출 실패 ({max_retries}회 재시도): {last_err}") from last_err

    def close(self) -> None:
        self._http.close()
