"""Runner 단위 테스트."""

from adaptive_agent.tools.runner import ToolRunner


class TestToolRunner:
    def setup_method(self):
        self.runner = ToolRunner(timeout=10)

    def test_simple_execution(self):
        code = "def run(input: dict) -> dict:\n    return {'result': input['x'] * 2}"
        result = self.runner.run(code, {"x": 21})
        assert result.success
        assert result.output is not None
        assert result.parsed_output == {"result": 42}
        assert '"result": 42' in result.output

    def test_string_processing(self):
        code = (
            "def run(input: dict) -> dict:\n"
            "    return {'result': input['text'].upper()}"
        )
        result = self.runner.run(code, {"text": "hello"})
        assert result.success
        assert result.output is not None
        assert "HELLO" in result.output

    def test_runtime_error(self):
        code = "def run(input: dict) -> dict:\n    return {'result': 1 / 0}"
        result = self.runner.run(code, {})
        assert not result.success
        assert result.error is not None
        assert "ZeroDivisionError" in result.error

    def test_key_error(self):
        code = "def run(input: dict) -> dict:\n    return {'result': input['missing']}"
        result = self.runner.run(code, {})
        assert not result.success
        assert result.error is not None
        assert "KeyError" in result.error

    def test_timeout(self):
        code = (
            "import time\n"
            "def run(input: dict) -> dict:\n"
            "    time.sleep(20)\n"
            "    return {'result': 'done'}"
        )
        runner = ToolRunner(timeout=2)
        result = runner.run(code, {})
        assert not result.success
        assert result.error is not None
        assert "시간 초과" in result.error

    def test_import_in_tool(self):
        code = (
            "import json\n"
            "def run(input: dict) -> dict:\n"
            "    data = json.loads(input['json_str'])\n"
            "    return {'result': data}"
        )
        result = self.runner.run(code, {"json_str": '{"a": 1}'})
        assert result.success

    def test_large_valid_json_preserves_parsed_output(self):
        """30KB 를 초과하는 valid JSON 출력은 parsed_output 으로 보존되어야 함.

        이전 구현은 truncation 을 json.loads 이전에 적용해, 큰 JSON 결과가
        parsed_output=None 으로 silent 하게 손실됐다. 본 테스트는 그 경로를
        가드한다.
        """
        code = (
            "def run(input: dict) -> dict:\n"
            "    return {'result': 'x' * 50000, 'count': 1}"
        )
        result = self.runner.run(code, {})
        assert result.success
        assert result.parsed_output is not None
        assert result.parsed_output["count"] == 1
        assert result.parsed_output["result"] == "x" * 50000

    def test_suggested_file_preserved_when_output_huge(self):
        """30KB 초과 결과 안에 suggested_file 이 있어도 parsed_output 으로
        보존되어야 다운스트림 (core._extract_suggested_files) 이 동작한다."""
        code = (
            "def run(input: dict) -> dict:\n"
            "    big = 'data ' * 8000  # ~40KB\n"
            "    return {\n"
            "        'result': {'rows': big.split()},\n"
            "        'suggested_file': {'path': 'out.txt', 'content': big},\n"
            "    }"
        )
        result = self.runner.run(code, {})
        assert result.success
        assert result.parsed_output is not None
        assert result.parsed_output.get("suggested_file", {}).get("path") == "out.txt"

    def test_huge_non_json_output_truncated(self):
        """Stdout 이 valid JSON 이 아니고 30KB 를 초과하면 display 가 truncate 되어야 함."""
        code = (
            "def run(input: dict) -> dict:\n"
            "    print('x' * 50000)\n"
            "    print('not-json-output')\n"
            "    return {'ok': True}"
        )
        # 위 도구는 stdout 에 'xxxx...not-json-output\n{\"ok\":true}' 을 찍는다.
        # 마지막 라인이 valid JSON 이 아니므로 전체 stdout 이 JSON 파싱 실패.
        result = self.runner.run(code, {})
        assert result.success
        assert result.output is not None
        assert "출력" in result.output and "자만 표시" in result.output
        assert len(result.output) < 35000  # display truncated
