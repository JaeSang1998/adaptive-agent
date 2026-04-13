"""정적 검증: AST 파싱, run() 존재, 허용 import, 금지 함수 호출.

방어 계층:
  1차: 이 모듈의 정적 검증 (AST 기반). 알려진 위험 패턴을 사전 차단.
  2차: subprocess 격리 (ToolRunner). 코드 크래시가 Agent를 죽이지 않음.
  정적 검증은 defense-in-depth이며, subprocess 격리 없이는 충분하지 않음.

Known limitations:
  - AST 기반이므로 변수에 금지 함수를 할당하는 간접 호출(f = getattr; f(...))은
    탐지 불가. subprocess 격리(ToolRunner)가 2차 방어선 역할.

설계 결정:
  - sys.modules 런타임 차단 대신 AST import 검사를 사용하는 이유:
    pathlib/tempfile이 내부적으로 os에 의존하므로, os를 sys.modules에서
    제거하면 허용 모듈이 연쇄 실패. AST 수준에서 import를 차단하면 이 문제 없음.
    (Python issue #31642, Checkmarx "The Glass Sandbox" 참고)
  - 문자열 패턴 매칭은 주석·docstring·문자열 안에서도 매칭되어 false positive
    위험이 있으므로 사용하지 않는다. 모든 금지 호출은 AST 노드로 검사한다.
    import 차단으로 이미 막히는 모듈 (`os`, `subprocess`, `importlib`, `shutil`)
    은 별도 패턴이 불필요하다.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


ALLOWED_IMPORTS: frozenset[str] = frozenset({
    "json", "re", "math", "datetime", "collections", "itertools",
    "csv", "statistics", "hashlib", "base64", "string", "operator",
    "functools", "decimal", "fractions", "io", "html",
    "sqlite3", "tempfile", "pathlib",
})

# AST 기반으로 탐지할 금지 함수 호출 (이름 단독 또는 모듈 속성 호출).
# 문자열 매칭이 아니라 ast.Call 노드를 검사하므로 docstring/주석 false positive 없음.
FORBIDDEN_CALLS: frozenset[str] = frozenset({
    "open", "eval", "exec", "compile",
    "__import__",
    "globals", "locals", "vars",
    "getattr", "setattr", "delattr",
})


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    reason: str = ""


def validate_tool_code(source_code: str) -> ValidationResult:
    """생성된 도구 코드를 정적 검증."""
    # 1. 구문 검사
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        return ValidationResult(valid=False, reason=f"구문 오류: {e}")

    # 2. run() 함수 존재 확인
    if not _has_run_function(tree):
        return ValidationResult(valid=False, reason="def run(input: dict) -> dict 함수가 없습니다.")

    # 3. import 검사
    bad_imports = _check_imports(tree)
    if bad_imports:
        return ValidationResult(valid=False, reason=f"허용되지 않은 import: {', '.join(bad_imports)}")

    # 4. 금지 함수 호출 검사 (AST 기반)
    bad_calls = _check_forbidden_calls(tree)
    if bad_calls:
        return ValidationResult(valid=False, reason=f"금지된 함수 호출: {', '.join(bad_calls)}")

    # 5. 구조 검사 (class 정의, dunder 속성 접근)
    structure_issue = _check_structure(tree)
    if structure_issue:
        return ValidationResult(valid=False, reason=structure_issue)

    return ValidationResult(valid=True)


def _has_run_function(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            return True
    return False


def _check_imports(tree: ast.Module) -> list[str]:
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split(".")[0]
                if module not in ALLOWED_IMPORTS:
                    bad.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module = node.module.split(".")[0]
                if module not in ALLOWED_IMPORTS:
                    bad.append(node.module)
    return bad


# 위험한 dunder 만 명시적으로 차단. 다른 dunder (`__init__`, `__iter__`,
# `__getitem__` 등) 는 정상 Python 사용에 필요하므로 허용. 차단 대상은
# metaclass / introspection 공격 경로:
_DANGEROUS_DUNDERS: frozenset[str] = frozenset({
    "__class__", "__bases__", "__subclasses__", "__mro__",
    "__globals__", "__builtins__", "__import__",
    "__getattribute__", "__reduce__", "__reduce_ex__",
    "__dict__",
})


def _check_structure(tree: ast.Module) -> str:
    """class 정의, dunder 속성 접근 등 구조적 위험 탐지.

    dunder 검사는 metaclass / introspection 공격 경로 (`x.__class__.__bases__`,
    `obj.__globals__`, `__subclasses__`) 만 차단한다. 일반 dunder 메서드 정의
    (`def __iter__(self):`) 는 ast.FunctionDef 이므로 이 검사를 통과한다.
    """
    for node in ast.walk(tree):
        # class 정의 차단: metaclass 공격 방지
        if isinstance(node, ast.ClassDef):
            return f"클래스 정의가 허용되지 않습니다: {node.name}"
        # 위험한 dunder 속성 접근만 차단
        if isinstance(node, ast.Attribute) and node.attr in _DANGEROUS_DUNDERS:
            return f"위험한 dunder 속성 접근이 차단되었습니다: {node.attr}"
    return ""


_SAFE_COMPILE_MODULES: frozenset[str] = frozenset({"re"})


def _check_forbidden_calls(tree: ast.Module) -> list[str]:
    """AST에서 금지된 함수 호출(open 등)을 탐지. 주석·문자열 내부는 무시."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # open(...)
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                found.append(f"{node.func.id}()")
            # builtins.open(...) — re.compile() 등 안전한 모듈 메서드는 허용
            elif isinstance(node.func, ast.Attribute) and node.func.attr in FORBIDDEN_CALLS:
                if isinstance(node.func.value, ast.Name) and node.func.value.id in _SAFE_COMPILE_MODULES:
                    continue
                found.append(f"{node.func.attr}()")
    return found
