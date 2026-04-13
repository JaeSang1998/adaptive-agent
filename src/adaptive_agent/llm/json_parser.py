"""LLM 응답에서 JSON 추출 + healing + validation.

이 모듈은 native tool calling의 fallback 경로에서 사용됩니다.
Ollama v0.20.3+ 환경에서는 LLM이 tool_calls로 직접 도구를 호출하므로
이 파서를 거치지 않습니다. 구버전 Ollama 또는 native tool calling을
지원하지 않는 모델에서는 LLM이 텍스트로 JSON을 출력하고,
이 파서가 strict 추출 → json_repair healing 순으로 복구합니다.

json_repair 외부 의존성은 이 fallback 경로의 안정성을 위해 존재합니다.
"""

from __future__ import annotations

import json
from typing import Any, cast

import json_repair
from pydantic import BaseModel, ValidationError


def extract_and_heal_json(
    text: str,
    model: type[BaseModel] | None = None,
) -> dict[str, Any] | None:
    """LLM 응답에서 JSON 추출 + healing + Pydantic validation.

    1. strict json.loads 와 depth-tracking 으로 첫 번째 valid object 추출
    2. 실패 시 json_repair 로 healing (전체 텍스트 → 첫 `{` 이후 substring)
    3. model 주어지면 Pydantic validation
    """
    text = text.strip()
    if not text:
        return None

    result = _extract_strict(text) or _heal_with_repair(text)
    if result is None:
        return None

    if model is not None:
        try:
            model.model_validate(result)
        except ValidationError:
            return None

    return result


def _extract_strict(text: str) -> dict[str, Any] | None:
    """Strict json.loads. 전체 또는 depth-tracking 으로 첫 번째 valid object 추출.

    fenced ```json ... ``` 블록의 내부도 depth tracker 가 자연스럽게 잡는다.
    """
    try:
        whole = json.loads(text)
        if isinstance(whole, dict):
            return cast(dict[str, Any], whole)
    except json.JSONDecodeError:
        pass

    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    parsed = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    start = -1
                    continue
                if isinstance(parsed, dict):
                    return cast(dict[str, Any], parsed)
                start = -1
    return None


def _heal_with_repair(text: str) -> dict[str, Any] | None:
    """json_repair 로 malformed JSON healing.

    1. 전체 텍스트로 시도 (single quote, trailing comma, fenced 블록 등 처리)
    2. 첫 `{` 이후 substring 으로 재시도 (앞쪽 garbage 제거)
    """
    try:
        result = json_repair.loads(text)
        if isinstance(result, dict):
            return result
    except Exception:
        pass

    idx = text.find("{")
    if idx > 0:
        try:
            result = json_repair.loads(text[idx:])
            if isinstance(result, dict):
                return result
        except Exception:
            pass

    return None
