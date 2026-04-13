"""Validator 단위 테스트."""

from adaptive_agent.tools.validator import validate_tool_code


class TestValidateToolCode:
    def test_valid_code(self):
        code = "def run(input: dict) -> dict:\n    return {'result': 42}"
        result = validate_tool_code(code)
        assert result.valid

    def test_valid_with_allowed_import(self):
        code = "import json\ndef run(input: dict) -> dict:\n    return json.loads('{}')"
        result = validate_tool_code(code)
        assert result.valid

    def test_syntax_error(self):
        code = "def run(input: dict) -> dict\n    return 42"
        result = validate_tool_code(code)
        assert not result.valid
        assert "구문" in result.reason

    def test_missing_run_function(self):
        code = "def process(data): return data"
        result = validate_tool_code(code)
        assert not result.valid
        assert "run" in result.reason

    def test_forbidden_import_os(self):
        code = "import os\ndef run(input: dict) -> dict:\n    return {}"
        result = validate_tool_code(code)
        assert not result.valid
        assert "os" in result.reason

    def test_forbidden_import_subprocess(self):
        code = "import subprocess\ndef run(input: dict) -> dict:\n    return {}"
        result = validate_tool_code(code)
        assert not result.valid

    def test_forbidden_eval(self):
        code = "def run(input: dict) -> dict:\n    return {'result': eval('1+1')}"
        result = validate_tool_code(code)
        assert not result.valid
        assert "eval(" in result.reason

    def test_open_forbidden(self):
        """open()은 금지됨 — 파일 접근은 built-in 도구로만 허용."""
        code = "def run(input: dict) -> dict:\n    f = open('test.txt')\n    return {}"
        result = validate_tool_code(code)
        assert not result.valid
        assert "open(" in result.reason

    def test_multiple_allowed_imports(self):
        code = (
            "import json\nimport re\nimport math\n"
            "def run(input: dict) -> dict:\n    return {'result': math.pi}"
        )
        result = validate_tool_code(code)
        assert result.valid

    def test_eval_ast_detection(self):
        """eval()을 AST 레벨에서 탐지 (alias 우회 차단)."""
        code = "def run(input: dict) -> dict:\n    return {'result': eval(input['expr'])}"
        result = validate_tool_code(code)
        assert not result.valid
        assert "eval" in result.reason

    def test_exec_ast_detection(self):
        """exec()을 AST 레벨에서 탐지."""
        code = "def run(input: dict) -> dict:\n    exec('x=1')\n    return {}"
        result = validate_tool_code(code)
        assert not result.valid

    def test_compile_ast_detection(self):
        """compile()을 AST 레벨에서 탐지."""
        code = "def run(input: dict) -> dict:\n    compile('1+1', '<>', 'eval')\n    return {}"
        result = validate_tool_code(code)
        assert not result.valid

    def test_urllib_blocked(self):
        """urllib 전체 차단 — 네트워크 접근은 web_fetch built-in으로만 허용."""
        code = "import urllib.request\ndef run(input: dict) -> dict:\n    return {}"
        result = validate_tool_code(code)
        assert not result.valid
        assert "urllib" in result.reason

    def test_urllib_parse_blocked(self):
        """urllib.parse도 차단 — urllib 모듈 전체가 생성 코드에서 사용 불가."""
        code = "from urllib.parse import quote\ndef run(input: dict) -> dict:\n    return {}"
        result = validate_tool_code(code)
        assert not result.valid

    def test_class_definition_blocked(self):
        """class 정의 차단 — metaclass 공격 방지."""
        code = "class Evil(type):\n    pass\ndef run(input: dict) -> dict:\n    return {}"
        result = validate_tool_code(code)
        assert not result.valid
        assert "클래스" in result.reason

    def test_dunder_attribute_blocked(self):
        """dunder 속성 접근 차단 — __class__, __bases__ 등 sandbox escape 방지."""
        code = "def run(input: dict) -> dict:\n    return {'x': ().__class__.__bases__[0]}"
        result = validate_tool_code(code)
        assert not result.valid
        assert "dunder" in result.reason

    def test_subclasses_blocked(self):
        """__subclasses__() 접근 차단."""
        code = "def run(input: dict) -> dict:\n    return {'x': object.__subclasses__()}"
        result = validate_tool_code(code)
        assert not result.valid
        assert "__subclasses__" in result.reason

    def test_getattr_blocked_by_forbidden_pattern(self):
        """getattr()를 통한 간접 eval 호출이 문자열 패턴으로 차단됨."""
        code = 'def run(input: dict) -> dict:\n    f = getattr(__builtins__, "eval")\n    return {"r": f("1+1")}'
        result = validate_tool_code(code)
        assert not result.valid
        assert "getattr(" in result.reason

    def test_dunder_import_blocked(self):
        """__import__ 우회 시도가 금지 패턴으로 차단됨."""
        code = 'def run(input: dict) -> dict:\n    os = __import__("os")\n    return {"cwd": os.getcwd()}'
        result = validate_tool_code(code)
        assert not result.valid
        assert "__import__" in result.reason
