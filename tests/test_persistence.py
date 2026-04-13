"""Persistence 단위 테스트."""

from pathlib import Path
from adaptive_agent.tools.persistence import ToolPersistence, infer_input_schema, parse_docstring_args


class TestToolPersistence:
    _tmp: Path
    persistence: ToolPersistence

    def setup_method(self, tmp_path: Path | None = None):
        self._tmp = Path("/tmp/test_adaptive_agent_tools")
        if self._tmp.exists():
            import shutil
            shutil.rmtree(self._tmp)
        self._tmp.mkdir(parents=True)
        self.persistence = ToolPersistence(self._tmp)

    def teardown_method(self):
        import shutil
        if self._tmp.exists():
            shutil.rmtree(self._tmp)

    def test_save_and_load(self):
        code = "def run(input: dict) -> dict:\n    return {'result': 42}"
        manifest: dict[str, object] = {"name": "test_tool", "description": "Test", "tags": ["test"]}

        self.persistence.save("test_tool", code, manifest)

        loaded = self.persistence.load("test_tool")
        assert loaded is not None
        assert loaded["code"] == code
        assert loaded["manifest"]["name"] == "test_tool"

    def test_load_nonexistent(self):
        loaded = self.persistence.load("nonexistent")
        assert loaded is None

    def test_save_with_success_data(self):
        code = "def run(input: dict) -> dict:\n    return {'result': 42}"
        manifest: dict[str, object] = {"name": "test", "description": "Test", "tags": []}

        self.persistence.save(
            "test",
            code,
            manifest,
            last_success_input={"x": 1},
            last_success_output={"result": 42},
        )

        loaded = self.persistence.load("test")
        assert loaded is not None
        assert loaded["manifest"]["last_success_input"] == {"x": 1}
        assert loaded["manifest"]["last_success_output"] == {"result": 42}


class TestInferInputSchema:
    def test_basic_types(self):
        schema = infer_input_schema({
            "name": "Orc",
            "hp": 150,
            "ratio": 0.5,
            "active": True,
            "items": [1, 2],
            "meta": {"k": "v"},
        })
        assert schema is not None
        props = schema["properties"]
        assert props["name"]["type"] == "string"
        assert props["hp"]["type"] == "integer"
        assert props["ratio"]["type"] == "number"
        assert props["active"]["type"] == "boolean"
        assert props["items"]["type"] == "array"
        assert props["meta"]["type"] == "object"
        assert set(schema["required"]) == {"name", "hp", "ratio", "active", "items", "meta"}

    def test_none_input_returns_none(self):
        assert infer_input_schema(None) is None

    def test_empty_dict_returns_none(self):
        assert infer_input_schema({}) is None

    def test_descriptions_merged(self):
        schema = infer_input_schema(
            {"count": 10, "query": "news"},
            arg_descriptions={"count": "가져올 개수", "query": "검색어"},
        )
        assert schema is not None
        assert schema["properties"]["count"]["description"] == "가져올 개수"
        assert schema["properties"]["query"]["description"] == "검색어"

    def test_partial_descriptions(self):
        """description이 일부 키에만 있어도 동작."""
        schema = infer_input_schema(
            {"a": 1, "b": "x"},
            arg_descriptions={"a": "숫자"},
        )
        assert schema is not None
        assert "description" in schema["properties"]["a"]
        assert "description" not in schema["properties"]["b"]


class TestParseDocstringArgs:
    def test_google_style(self):
        code = '''
def run(input: dict) -> dict:
    """hp 기준 필터링.

    Args:
        min_hp: 최소 hp 기준값
        monsters: 몬스터 데이터 리스트

    Returns:
        필터링 결과
    """
    return {"result": []}
'''
        args = parse_docstring_args(code)
        assert args == {"min_hp": "최소 hp 기준값", "monsters": "몬스터 데이터 리스트"}

    def test_no_args_section(self):
        code = '''
def run(input: dict) -> dict:
    """단순 도구."""
    return {"result": 42}
'''
        assert parse_docstring_args(code) == {}

    def test_no_docstring(self):
        code = "def run(input: dict) -> dict:\n    return {'result': 42}"
        assert parse_docstring_args(code) == {}

    def test_no_run_function(self):
        code = "def helper(): pass"
        assert parse_docstring_args(code) == {}

    def test_syntax_error(self):
        assert parse_docstring_args("def broken(") == {}

    def test_args_with_type_annotation(self):
        """Args에 (type) 표기가 있어도 description만 추출."""
        code = '''
def run(input: dict) -> dict:
    """도구.

    Args:
        count (int): 개수
        name (str): 이름
    """
    return {"result": None}
'''
        args = parse_docstring_args(code)
        assert args == {"count": "개수", "name": "이름"}
