"""Builder 단위 테스트: 코드 생성."""

from unittest.mock import MagicMock

from adaptive_agent.llm.client import LLMResponse
from adaptive_agent.tools.builder import ToolBuilder


def _make_builder(responses: list[str]) -> ToolBuilder:
    """Mock LLM client로 ToolBuilder 생성."""
    client = MagicMock()
    call_count = {"n": 0}

    def fake_chat(messages: list[dict[str, str]], **kwargs: object) -> LLMResponse:
        idx = call_count["n"]
        call_count["n"] += 1
        content = responses[idx] if idx < len(responses) else ""
        return LLMResponse(content=content, thinking=None, usage={})

    client.chat = MagicMock(side_effect=fake_chat)
    return ToolBuilder(client)


class TestToolBuilder:
    def test_successful_build(self):
        """코드 생성 성공."""
        code = "def run(input: dict) -> dict:\n    return {'result': input['x'] * 2}"

        builder = _make_builder([code])
        result = builder.build("숫자를 2배로", "5를 2배로 해줘")

        assert result.success
        assert "def run(" in result.code

    def test_code_generation_failure(self):
        """빈 응답 → 실패. retry 없음 (single attempt)."""
        builder = _make_builder([""])
        result = builder.build("테스트", "테스트")

        assert not result.success
        assert result.error is not None
        assert "추출할 수 없습니다" in result.error

    def test_llm_call_exception(self):
        """LLM 호출 자체가 실패."""
        client = MagicMock()
        client.chat = MagicMock(side_effect=RuntimeError("서버 연결 실패"))
        builder = ToolBuilder(client)

        result = builder.build("테스트", "테스트")

        assert not result.success
        assert result.error is not None
        assert "LLM 호출 실패" in result.error

    def test_input_data_passed_to_code_messages(self):
        """input_data가 코드 생성 메시지에 포함되는지 확인."""
        code = "def run(input: dict) -> dict:\n    return {'result': input['x']}"

        client = MagicMock()
        captured_messages: list[list[dict[str, str]]] = []

        def fake_chat(messages: list[dict[str, str]], **kwargs: object) -> LLMResponse:
            captured_messages.append(messages)
            return LLMResponse(content=code, thinking=None, usage={})

        client.chat = MagicMock(side_effect=fake_chat)
        builder = ToolBuilder(client)

        result = builder.build("d", "req", input_data={"x": 42})

        assert result.success
        assert len(captured_messages) == 1
        user_msg = captured_messages[0][-1]["content"]
        assert "42" in user_msg
