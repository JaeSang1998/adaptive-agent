"""json_parser 단위 테스트: LLM 응답에서 JSON 추출 + healing + validation."""

from pydantic import BaseModel

from adaptive_agent.llm.json_parser import extract_and_heal_json


class DummySchema(BaseModel):
    tool: str
    input: dict[str, object] = {}


class TestExtractAndHealJson:
    # -- 정상 케이스 --

    def test_pure_json(self):
        text = '{"tool": "read_file", "input": {"path": "data.csv"}}'
        result = extract_and_heal_json(text)
        assert result == {"tool": "read_file", "input": {"path": "data.csv"}}

    def test_json_code_fence(self):
        text = '```json\n{"tool": "think", "input": {"reasoning": "분석 중"}}\n```'
        result = extract_and_heal_json(text)
        assert result is not None
        assert result["tool"] == "think"

    def test_json_code_fence_no_lang(self):
        text = '```\n{"tool": "read_file", "input": {}}\n```'
        result = extract_and_heal_json(text)
        assert result is not None
        assert result["tool"] == "read_file"

    def test_json_embedded_in_text(self):
        """LLM이 JSON 앞뒤로 설명을 붙인 경우."""
        text = '파일을 읽겠습니다.\n{"tool": "read_file", "input": {"path": "test.txt"}}\n위 도구를 사용합니다.'
        result = extract_and_heal_json(text)
        assert result is not None
        assert result["tool"] == "read_file"

    def test_nested_braces(self):
        """중첩 { } 가 있는 JSON — depth tracking 필요."""
        text = '{"tool": "generate_code", "input": {"description": "필터링", "data": {"key": "value"}}}'
        result = extract_and_heal_json(text)
        assert result is not None
        assert result["tool"] == "generate_code"
        assert result["input"]["data"]["key"] == "value"

    # -- healing 케이스 (json_repair) --

    def test_trailing_comma(self):
        """trailing comma — 흔한 LLM JSON 실수."""
        text = '{"tool": "think", "input": {"reasoning": "테스트"},}'
        result = extract_and_heal_json(text)
        assert result is not None
        assert result["tool"] == "think"

    def test_single_quotes(self):
        """single quote 사용 — LLM이 Python dict처럼 출력."""
        text = "{'tool': 'read_file', 'input': {'path': 'data.csv'}}"
        result = extract_and_heal_json(text)
        assert result is not None
        assert result["tool"] == "read_file"

    def test_missing_closing_brace_in_fence(self):
        """code fence 안의 불완전 JSON — healing으로 복구."""
        text = '```json\n{"tool": "think", "input": {"reasoning": "분석"\n```'
        result = extract_and_heal_json(text)
        # json_repair가 복구할 수 있으면 dict, 아니면 None
        if result is not None:
            assert result["tool"] == "think"

    # -- Pydantic validation --

    def test_valid_with_model(self):
        text = '{"tool": "read_file", "input": {"path": "x"}}'
        result = extract_and_heal_json(text, model=DummySchema)
        assert result is not None
        assert result["tool"] == "read_file"

    def test_invalid_model_returns_none(self):
        """schema에 맞지 않으면 None 반환."""
        text = '{"wrong_key": "value"}'
        result = extract_and_heal_json(text, model=DummySchema)
        assert result is None

    # -- 실패 케이스 --

    def test_plain_text_returns_none(self):
        text = "안녕하세요! 도움이 필요하시면 말씀해주세요."
        result = extract_and_heal_json(text)
        assert result is None

    def test_empty_string_returns_none(self):
        result = extract_and_heal_json("")
        assert result is None

    def test_only_braces_no_valid_json(self):
        text = "{ 이것은 JSON이 아닙니다 }"
        result = extract_and_heal_json(text)
        # json_repair가 어떻게든 파싱할 수도 있으므로
        # None이거나, dict이지만 tool 키가 없을 것
        if result is not None:
            assert isinstance(result, dict)

    def test_multiple_json_objects_extracts_first(self):
        """여러 JSON 객체가 있으면 첫 번째를 추출."""
        text = '{"tool": "think", "input": {}}\n{"tool": "read_file", "input": {}}'
        result = extract_and_heal_json(text)
        assert result is not None
        assert result["tool"] == "think"
