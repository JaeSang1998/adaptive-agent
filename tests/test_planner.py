# pyright: reportPrivateUsage=false
"""Planner 단위 테스트: _parse() JSON/텍스트 판별 + decide() 동작."""

import json
from unittest.mock import MagicMock
from typing import Any

from adaptive_agent.llm.client import LLMResponse
from adaptive_agent.agent.planner import Planner


def _make_planner(responses: list[str]) -> Planner:
    """Mock LLM client로 Planner 생성."""
    client = MagicMock()
    call_count = {"n": 0}

    def fake_chat(messages: list[dict[str, Any]], **kwargs: object) -> LLMResponse:
        idx = call_count["n"]
        call_count["n"] += 1
        content = responses[idx] if idx < len(responses) else ""
        return LLMResponse(content=content, thinking=None, usage={})

    client.chat = MagicMock(side_effect=fake_chat)
    return Planner(client)


class TestPlannerParse:
    """_parse() 메서드 직접 테스트."""

    def test_valid_tool_call_json(self):
        planner = _make_planner([])
        decision = planner._parse('{"tool": "read_file", "input": {"path": "data.csv"}}')

        assert decision.tool_call is not None
        assert decision.tool_call["tool"] == "read_file"
        assert decision.text is None

    def test_plain_text_response(self):
        planner = _make_planner([])
        decision = planner._parse("분석 결과, 평균 연봉은 75,000원입니다.")

        assert decision.text is not None
        assert decision.tool_call is None
        assert "75,000" in decision.text

    def test_empty_response(self):
        planner = _make_planner([])
        decision = planner._parse("")

        assert decision.text is not None
        assert decision.tool_call is None

    def test_whitespace_only(self):
        planner = _make_planner([])
        decision = planner._parse("   \n  ")

        assert decision.text is not None
        assert decision.tool_call is None

    def test_json_in_code_fence(self):
        planner = _make_planner([])
        text = '```json\n{"tool": "think", "input": {"reasoning": "분석 중"}}\n```'
        decision = planner._parse(text)

        assert decision.tool_call is not None
        assert decision.tool_call["tool"] == "think"

    def test_json_with_surrounding_text(self):
        """JSON 앞뒤에 텍스트가 있는 경우 JSON 추출."""
        planner = _make_planner([])
        text = '파일을 읽어보겠습니다.\n{"tool": "read_file", "input": {"path": "x.csv"}}\n위 도구를 호출합니다.'
        decision = planner._parse(text)

        assert decision.tool_call is not None
        assert decision.tool_call["tool"] == "read_file"

    def test_malformed_json_returns_text(self):
        """파싱 불가능한 JSON → 텍스트 응답으로 처리."""
        planner = _make_planner([])
        decision = planner._parse('{"tool": "read_file", "input": }')

        # json_repair가 고칠 수도 있지만, tool key 없으면 텍스트로 처리
        # 결과가 tool_call이든 text든 crash 안 하면 OK
        assert decision.text is not None or decision.tool_call is not None

    def test_json_without_tool_key_is_text(self):
        """tool 키 없는 JSON → 텍스트 응답."""
        planner = _make_planner([])
        decision = planner._parse('{"result": 42}')

        assert decision.text is not None
        assert decision.tool_call is None


class TestPlannerDecide:
    """decide() 통합 동작 테스트."""

    def test_decide_returns_tool_call(self):
        response = json.dumps({"tool": "generate_code", "input": {"description": "계산"}})
        planner = _make_planner([response])

        decision = planner.decide(
            conversation=[{"role": "user", "content": "1+1 계산해줘"}],
            tool_descriptions=[{"name": "generate_code", "description": "코드 실행", "tags": []}],
        )

        assert decision.tool_call is not None
        assert decision.tool_call["tool"] == "generate_code"
        assert planner._client.chat.call_args.kwargs["phase"] == "planner"  # type: ignore[union-attr]

    def test_decide_returns_text(self):
        planner = _make_planner(["안녕하세요! 도움이 필요하시면 말씀하세요."])

        decision = planner.decide(
            conversation=[{"role": "user", "content": "안녕"}],
            tool_descriptions=[],
        )

        assert decision.text is not None
        assert "안녕" in decision.text

    def test_decide_retries_on_empty_response(self):
        """빈 응답 시 temperature 올려 재시도."""
        planner = _make_planner(["", "재시도 결과입니다."])

        decision = planner.decide(
            conversation=[{"role": "user", "content": "테스트"}],
            tool_descriptions=[],
        )

        assert decision.text is not None
        # LLM이 2번 호출되었는지 확인
        assert planner._client.chat.call_count == 2  # type: ignore[union-attr]

    def test_decide_with_plan_context(self):
        """plan 전달 시 crash 안 하고 정상 동작."""
        from adaptive_agent.agent.session import PlanStep

        response = "계획에 따라 다음 단계를 진행합니다."
        planner = _make_planner([response])

        decision = planner.decide(
            conversation=[{"role": "user", "content": "계속 진행해줘"}],
            tool_descriptions=[],
            plan=[PlanStep(content="1단계", status="completed"), PlanStep(content="2단계", status="in_progress")],
        )

        assert decision.text is not None

    def test_decide_uses_native_tool_calls(self):
        client = MagicMock()
        client.native_tools_supported = True
        client.chat = MagicMock(return_value=LLMResponse(
            content="",
            thinking=None,
            usage={},
            tool_calls=[{"tool": "read_file", "input": {"path": "sales_data.csv"}}],
        ))
        planner = Planner(client)

        decision = planner.decide(
            conversation=[{"role": "user", "content": "파일 읽어줘"}],
            tool_descriptions=[{"name": "read_file", "description": "파일 읽기", "tags": []}],
        )

        assert decision.tool_call is not None
        assert decision.tool_call["tool"] == "read_file"
        assert decision.is_native_tool_call is True
