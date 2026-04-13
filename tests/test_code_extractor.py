# pyright: reportPrivateUsage=false
"""code_extractor 단위 테스트: fence 제거 + AST 검증."""

from adaptive_agent.llm.code_extractor import extract_code, _strip_fences, _is_valid_tool_code


class TestStripFences:
    def test_python_fence(self):
        text = "```python\nprint('hello')\n```"
        assert _strip_fences(text) == "print('hello')"

    def test_plain_fence(self):
        text = "```\nprint('hello')\n```"
        assert _strip_fences(text) == "print('hello')"

    def test_no_fence(self):
        text = "print('hello')"
        assert _strip_fences(text) == "print('hello')"

    def test_whitespace_around_fence(self):
        text = "  ```python\n  x = 1\n```  "
        result = _strip_fences(text)
        assert "x = 1" in result

    def test_empty_fence(self):
        text = "```python\n\n```"
        assert _strip_fences(text).strip() == ""


class TestIsValidToolCode:
    def test_valid(self):
        code = "def run(input: dict) -> dict:\n    return {'result': 1}"
        assert _is_valid_tool_code(code)

    def test_missing_run(self):
        code = "def process(input: dict) -> dict:\n    return {}"
        assert not _is_valid_tool_code(code)

    def test_syntax_error(self):
        code = "def run(input: dict -> dict:\n    return {}"
        assert not _is_valid_tool_code(code)

    def test_run_as_substring(self):
        """'run'이 다른 함수 이름에 포함된 경우 (run_task 등)."""
        code = "def run_task(input: dict) -> dict:\n    return {}"
        assert not _is_valid_tool_code(code)

    def test_nested_run(self):
        """run()이 내부 함수로 정의된 경우도 유효."""
        code = (
            "def outer():\n"
            "    def run(input: dict) -> dict:\n"
            "        return {}\n"
        )
        assert _is_valid_tool_code(code)


class TestExtractCode:
    def test_fenced_valid_code(self):
        """_strip_fences는 re.match로 문자열 시작부터 fence를 찾으므로,
        fence가 텍스트 맨 앞에 있어야 함 (builder 프롬프트가 이를 보장)."""
        text = "```python\ndef run(input: dict) -> dict:\n    return {'result': 42}\n```"
        code = extract_code(text)
        assert "def run(" in code
        assert "42" in code

    def test_raw_code_no_fence(self):
        text = "def run(input: dict) -> dict:\n    return {'result': 1}"
        code = extract_code(text)
        assert "def run(" in code

    def test_surrounding_text_still_extracts_code(self):
        text = (
            "아래 코드입니다.\n"
            "```python\n"
            "def run(input: dict) -> dict:\n"
            "    return {'result': 42}\n"
            "```\n"
            "설명 끝."
        )
        code = extract_code(text)
        assert "def run(" in code
        assert "42" in code

    def test_extracts_from_first_run_definition_without_fence(self):
        text = (
            "설명 먼저\n"
            "def run(input: dict) -> dict:\n"
            "    return {'result': 'ok'}\n"
            "# 끝"
        )
        code = extract_code(text)
        assert "def run(" in code
        assert "'ok'" in code

    def test_prefers_full_code_over_run_slice_when_imports_exist(self):
        text = (
            "import json\n"
            "def run(input: dict) -> dict:\n"
            "    return {'result': json.dumps({'ok': True})}\n"
        )
        code = extract_code(text)
        assert "import json" in code

    def test_invalid_code_returns_empty(self):
        text = "이건 코드가 아닙니다. 그냥 설명입니다."
        assert extract_code(text) == ""

    def test_empty_input(self):
        assert extract_code("") == ""
        assert extract_code("   ") == ""

    def test_code_without_run_returns_empty(self):
        text = "```python\ndef process(x):\n    return x\n```"
        assert extract_code(text) == ""

    def test_syntax_error_returns_empty(self):
        text = "```python\ndef run(input dict) -> dict:\n    return {}\n```"
        assert extract_code(text) == ""
