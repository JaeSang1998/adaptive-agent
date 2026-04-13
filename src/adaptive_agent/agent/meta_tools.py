"""Meta tool 정의 단일 source.

Agent 가 노출하는 meta tool 의 단일 source of truth.
- `META_TOOL_NAMES`: emit 정책 분기 (core 가 사용)
- `META_TOOL_SCHEMAS`: native tool calling 등록 (planner 가 사용)
- `is_registry_overlap`: registry builtin 과 겹치는지 (think/ask_user/update_plan 은 builtin)

신규 meta tool 추가 시 이 파일만 수정하면 core / planner 양쪽이 자동 동기화.
"""

from __future__ import annotations

from typing import Any


# (name, registers_in_registry, ollama_schema_or_None)
# - registers_in_registry=True 이면 builtin descriptor 가 registry 에 있으므로
#   ollama tools list 에 별도 추가하지 않음. core 의 emit 분기에는 여전히 포함.
# - schema=None 이면 native tool calling 등록 대상 아님 (= registry overlap).
_META_TOOLS: list[dict[str, Any]] = [
    {
        "name": "think",
        "registry_overlap": True,
        "schema": None,
    },
    {
        "name": "ask_user",
        "registry_overlap": True,
        "schema": None,
    },
    {
        "name": "update_plan",
        "registry_overlap": True,
        "schema": None,
    },
    {
        "name": "generate_code",
        "registry_overlap": False,
        "schema": {
            "type": "function",
            "function": {
                "name": "generate_code",
                "description": (
                    "Python 코드를 생성하고 실행합니다. 데이터 처리, 계산, 변환 등 모든 작업에 사용. "
                    "데이터는 explicit input key 로 전달하세요: 파일은 {\"키\": {\"$ref\": \"<path>\"}}, "
                    "inline 데이터는 literal 값. tool_name 은 의미있는 snake_case 로 반드시 지정."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tool_name": {
                            "type": "string",
                            "description": (
                                "생성할 도구의 snake_case 이름. 작업 목적이 드러나도록 작성. "
                                "예: sales_top5_extractor, csv_revenue_by_region."
                            ),
                        },
                        "description": {"type": "string", "description": "생성할 코드의 목적"},
                    },
                    "required": ["tool_name", "description"],
                    "additionalProperties": True,
                },
            },
        },
    },
    {
        "name": "repair_tool",
        "registry_overlap": False,
        "schema": {
            "type": "function",
            "function": {
                "name": "repair_tool",
                "description": "실행에 실패한 도구 코드를 수정합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tool_name": {"type": "string", "description": "수정할 도구 이름"},
                    },
                    "required": ["tool_name"],
                },
            },
        },
    },
]


META_TOOL_NAMES: frozenset[str] = frozenset(t["name"] for t in _META_TOOLS)
"""모든 meta tool 의 이름 집합 (core 의 `using_tool` emit 분기용)."""


META_TOOL_SCHEMAS: list[dict[str, Any]] = [
    t["schema"] for t in _META_TOOLS if t["schema"] is not None
]
"""Native tool calling 등록용 스키마 리스트 (planner 가 ollama tools 파라미터로 전달).
registry 에 이미 있는 think/ask_user/update_plan 은 제외됨."""
