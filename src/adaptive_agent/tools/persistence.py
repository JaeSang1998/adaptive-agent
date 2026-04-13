"""도구 영구 저장/로딩: ~/.adaptive-agent/tools/{name}/tool.py + manifest.json."""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


class ToolPersistence:
    def __init__(self, tools_dir: Path) -> None:
        self._tools_dir = tools_dir

    @staticmethod
    def sanitize_name(name: str) -> str:
        """도구 이름에서 파일시스템 안전하지 않은 문자를 제거."""
        return _SAFE_NAME_RE.sub("_", name).strip("_") or "unnamed"

    def save(
        self,
        name: str,
        code: str,
        manifest: dict[str, Any],
        *,
        last_success_input: dict[str, Any] | None = None,
        last_success_output: Any | None = None,
    ) -> Path:
        """도구를 디스크에 저장."""
        name = self.sanitize_name(name)
        tool_dir = self._tools_dir / name
        tool_dir.mkdir(parents=True, exist_ok=True)

        # tool.py 저장
        tool_path = tool_dir / "tool.py"
        tool_path.write_text(code, encoding="utf-8")

        # manifest 보강 (원본 dict를 변이시키지 않도록 복사)
        manifest = {**manifest}
        manifest.setdefault("name", name)
        manifest.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        if last_success_input is not None:
            manifest["last_success_input"] = last_success_input
        if last_success_output is not None:
            manifest["last_success_output"] = last_success_output

        # manifest.json 저장
        manifest_path = tool_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return tool_dir

    def load(self, name: str) -> dict[str, Any] | None:
        """디스크에서 도구 로드."""
        name = self.sanitize_name(name)
        tool_dir = self._tools_dir / name

        tool_path = tool_dir / "tool.py"
        manifest_path = tool_dir / "manifest.json"

        if not tool_path.exists() or not manifest_path.exists():
            return None

        code = tool_path.read_text(encoding="utf-8")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        return {"code": code, "manifest": manifest}

    def exists(self, name: str) -> bool:
        return (self._tools_dir / self.sanitize_name(name) / "manifest.json").exists()


def extract_manifest_from_code(name: str, code: str, description: str = "") -> dict[str, Any]:
    """코드의 run() 함수에서 docstring을 추출하여 manifest 생성."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {"name": name, "description": description, "tags": []}

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            docstring = ast.get_docstring(node) or ""
            return {
                "name": name,
                "description": docstring or description,
                "tags": [],
            }
    return {"name": name, "description": description, "tags": []}


# -- input_schema 추론 ----------------------------------------------------------

_PYTHON_TO_JSON_TYPE: tuple[tuple[type, str], ...] = (
    (bool, "boolean"),    # bool은 int 서브클래스이므로 반드시 먼저
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    (list, "array"),
    (dict, "object"),
)


def _json_type(value: Any) -> str:
    """Python 값의 타입에서 JSON Schema 타입을 추론."""
    for py_type, json_type in _PYTHON_TO_JSON_TYPE:
        if isinstance(value, py_type):
            return json_type
    return "string"


def infer_input_schema(
    last_input: dict[str, Any] | None,
    *,
    arg_descriptions: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """last_success_input의 키-값에서 JSON Schema 생성.

    Python 타입을 JSON Schema 타입으로 매핑.
    arg_descriptions가 있으면 각 property에 description 추가.

    Returns:
        JSON Schema dict, 또는 입력이 비어있으면 None.
    """
    if not last_input:
        return None

    descs = arg_descriptions or {}
    properties: dict[str, Any] = {}
    required: list[str] = []

    for key, value in last_input.items():
        prop: dict[str, str] = {"type": _json_type(value)}
        if key in descs:
            prop["description"] = descs[key]
        properties[key] = prop
        required.append(key)

    if not properties:
        return None

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


_ARGS_SECTION_RE = re.compile(r"^\s*Args:\s*$", re.MULTILINE)
_ARG_LINE_RE = re.compile(r"^\s{4,}(\w+)\s*(?:\([^)]*\))?\s*:\s*(.+)")


def parse_docstring_args(code: str) -> dict[str, str]:
    """run() 함수의 Google style docstring에서 Args 파라미터 설명 추출.

    Returns:
        {param_name: description} dict. Args 블록이 없으면 빈 dict.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            docstring = ast.get_docstring(node)
            if not docstring:
                return {}
            return _parse_args_block(docstring)
    return {}


def _parse_args_block(docstring: str) -> dict[str, str]:
    """Google style Args 블록 파싱."""
    match = _ARGS_SECTION_RE.search(docstring)
    if not match:
        return {}

    args_text = docstring[match.end():]
    result: dict[str, str] = {}

    for line in args_text.splitlines():
        # Args 블록 종료: 빈 줄 또는 새 섹션(Returns:, Raises: 등)
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith(":") and not stripped.startswith(" ") and stripped[0].isupper():
            break

        arg_match = _ARG_LINE_RE.match(line)
        if arg_match:
            result[arg_match.group(1)] = arg_match.group(2).strip()

    return result
