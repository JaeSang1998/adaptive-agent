"""LLM 응답에서 Python 도구 코드 추출.

전략:
  1. Markdown code fence (```python ... ```) 제거
  2. 전체 텍스트가 코드인 경우 (fence 없이 LLM이 직접 출력)
검증: AST 파싱 + def run() 존재 확인.
"""

from __future__ import annotations

import ast
import re


def extract_code(text: str) -> str:
    """LLM 응답에서 Python 도구 코드 추출. fence/본문 후보를 모두 시도."""
    if not text or not text.strip():
        return ""
    for candidate in _candidate_code_blocks(text):
        if _is_valid_tool_code(candidate):
            return candidate
    return ""


def _strip_fences(text: str) -> str:
    """첫 번째 Markdown code fence를 제거. fence가 없으면 원문 반환."""
    stripped = text.strip()
    match = re.search(r"```(?:python)?\s*\n(.*?)```", stripped, re.DOTALL)
    return match.group(1).strip() if match else stripped


def _candidate_code_blocks(text: str) -> list[str]:
    stripped = text.strip()
    candidates: list[str] = []

    fenced_blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", stripped, re.DOTALL)
    candidates.extend(block.strip() for block in fenced_blocks if block.strip())

    candidates.append(_strip_fences(stripped))
    candidates.append(stripped)

    run_index = stripped.find("def run(")
    if run_index >= 0:
        candidates.append(stripped[run_index:].strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def _is_valid_tool_code(code: str) -> bool:
    """AST 파싱 + def run() 존재 여부로 유효한 도구 코드인지 검증."""
    if "def run(" not in code:
        return False
    try:
        tree = ast.parse(code)
        return any(
            isinstance(node, ast.FunctionDef) and node.name == "run"
            for node in ast.walk(tree)
        )
    except SyntaxError:
        return False
