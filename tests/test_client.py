"""OllamaClient 단위 테스트."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from adaptive_agent.config import Config, ExecutionConfig, LLMConfig
from adaptive_agent.llm.client import OllamaClient


class FakeHTTPClient:
    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        timeout: httpx.Timeout,
    ) -> httpx.Response:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        pass


def _make_config(**llm_overrides: object) -> Config:
    llm = LLMConfig(**llm_overrides)
    return Config(
        llm=llm,
        execution=ExecutionConfig(),
        tools_dir=Path("/tmp/test_client_tools"),
    )


def _response(status_code: int, payload: dict[str, object] | None = None, text: str = "") -> httpx.Response:
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    if payload is not None:
        return httpx.Response(status_code, request=request, json=payload)
    return httpx.Response(status_code, request=request, text=text)


def test_planner_phase_uses_phase_limits():
    client = OllamaClient(_make_config(
        planner_max_tokens=111,
        planner_timeout_seconds=12.5,
    ))
    fake_http = FakeHTTPClient([
        _response(200, {"message": {"content": "ok"}}),
    ])
    client._http = fake_http  # pyright: ignore[reportPrivateUsage]

    response = client.chat([{"role": "user", "content": "안녕"}], phase="planner")

    assert response.content == "ok"
    assert len(fake_http.calls) == 1
    body = fake_http.calls[0]["json"]
    assert isinstance(body, dict)
    assert body["options"]["num_predict"] == 111  # type: ignore[index]
    timeout = fake_http.calls[0]["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == 12.5


def test_native_tools_400_falls_back_and_disables_native(caplog: pytest.LogCaptureFixture):
    client = OllamaClient(_make_config(enable_native_tools=True))
    fake_http = FakeHTTPClient([
        _response(400, text='{"error":"tools unsupported"}'),
        _response(200, {"message": {"content": "fallback ok"}}),
    ])
    client._http = fake_http  # pyright: ignore[reportPrivateUsage]

    with caplog.at_level(logging.WARNING):
        response = client.chat(
            [{"role": "user", "content": "파일 읽어줘"}],
            tools=[{
                "type": "function",
                "function": {"name": "read_file", "description": "파일 읽기", "parameters": {}},
            }],
            phase="planner",
        )

    assert response.content == "fallback ok"
    assert len(fake_http.calls) == 2
    first_body = fake_http.calls[0]["json"]
    second_body = fake_http.calls[1]["json"]
    assert isinstance(first_body, dict) and "tools" in first_body
    assert isinstance(second_body, dict) and "tools" not in second_body
    assert client.native_tools_supported is False
    assert "prompt-based fallback" in caplog.text


def test_http_500_retries_and_logs_excerpt(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setattr("adaptive_agent.llm.client.time.sleep", lambda _seconds: None)

    client = OllamaClient(_make_config())
    fake_http = FakeHTTPClient([
        _response(500, text='{"error":"runner crashed"}'),
        _response(200, {"message": {"content": "recovered"}}),
    ])
    client._http = fake_http  # pyright: ignore[reportPrivateUsage]

    with caplog.at_level(logging.WARNING):
        response = client.chat([{"role": "user", "content": "수정해줘"}], phase="repair")

    assert response.content == "recovered"
    assert len(fake_http.calls) == 2
    assert "phase=repair" in caplog.text
    assert "runner crashed" in caplog.text


def test_transport_timeout_raises_runtime_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("adaptive_agent.llm.client.time.sleep", lambda _seconds: None)

    client = OllamaClient(_make_config(code_timeout_seconds=3.0))
    fake_http = FakeHTTPClient([
        httpx.ReadTimeout("slow response"),
    ])
    client._http = fake_http  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(RuntimeError, match="slow response"):
        client.chat([{"role": "user", "content": "코드 생성"}], phase="codegen")

    assert len(fake_http.calls) == 1
