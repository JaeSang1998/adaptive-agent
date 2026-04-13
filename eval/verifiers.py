"""검증 함수: 시나리오 결과를 다양한 기준으로 확인."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from eval.harness import HarnessResult


@dataclass
class VerifyOutcome:
    passed: bool
    detail: str = ""


def verify_response_contains(result: HarnessResult, values: list[str], **_kw: Any) -> VerifyOutcome:
    """응답 텍스트에 특정 문자열 포함 여부 확인."""
    combined = " ".join(r or "" for r in result.responses).lower()
    found = [v for v in values if v.lower() in combined]
    return VerifyOutcome(
        passed=len(found) > 0,
        detail=f"Found {len(found)}/{len(values)}: {found}",
    )


def verify_response_not_contains(result: HarnessResult, values: list[str], **_kw: Any) -> VerifyOutcome:
    """응답 텍스트에 특정 문자열이 없는지 확인."""
    combined = " ".join(r or "" for r in result.responses).lower()
    found = [v for v in values if v.lower() in combined]
    return VerifyOutcome(
        passed=len(found) == 0,
        detail=f"Unwanted found: {found}" if found else "Clean",
    )


def verify_file_exists(result: HarnessResult, paths: list[str], match: str = "any", **_kw: Any) -> VerifyOutcome:
    """파일 존재 확인. glob 패턴 지원."""
    found: list[str] = []
    for p in paths:
        matches = list(result.work_dir.glob(p))
        if matches:
            found.append(p)
    if match == "any":
        passed = len(found) > 0
    else:
        passed = len(found) == len(paths)
    return VerifyOutcome(passed=passed, detail=f"Found: {found}")


def verify_file_content_contains(
    result: HarnessResult, path: str, values: list[str],
    match: str = "any", **_kw: Any,
) -> VerifyOutcome:
    """파일 내용에 특정 문자열 포함 확인."""
    matches = list(result.work_dir.glob(path))
    if not matches:
        return VerifyOutcome(passed=False, detail=f"No file matching {path}")

    content = ""
    for m in matches:
        content += m.read_text(encoding="utf-8", errors="replace")

    found = [v for v in values if v.lower() in content.lower()]
    if match == "any":
        passed = len(found) > 0
    else:
        passed = len(found) == len(values)
    return VerifyOutcome(passed=passed, detail=f"Found {len(found)}/{len(values)}")


def verify_event_occurred(
    result: HarnessResult,
    event_type: str,
    min_count: int = 1,
    tool_name: str | list[str] | None = None,
    any_of: list[str] | None = None,
    **_kw: Any,
) -> VerifyOutcome:
    """특정 이벤트 발생 횟수 확인. tool_name 필터로 특정 도구만 카운트."""
    matching = [e for e in result.events if e["type"] == event_type]
    if tool_name is not None:
        names = [tool_name] if isinstance(tool_name, str) else list(tool_name)
        matching = [
            e for e in matching
            if e.get("data", {}).get("tool_name") in names
        ]
    if any_of is not None:
        matching = [
            e for e in matching
            if e.get("data", {}).get("tool_name") in any_of
        ]
    count = len(matching)
    suffix = ""
    if tool_name is not None:
        suffix = f" tool={tool_name}"
    elif any_of is not None:
        suffix = f" any_of={any_of}"
    return VerifyOutcome(
        passed=count >= min_count,
        detail=f"{event_type}{suffix}: {count} (min: {min_count})",
    )


def _tool_call_names(result: HarnessResult) -> list[str]:
    """using_tool 이벤트에서 호출된 도구 이름 리스트."""
    return [
        str(e.get("data", {}).get("tool_name", ""))
        for e in result.events
        if e.get("type") == "using_tool"
    ]


def verify_tool_called(
    result: HarnessResult,
    tool_name: str | None = None,
    any_of: list[str] | None = None,
    min_count: int = 1,
    **_kw: Any,
) -> VerifyOutcome:
    """using_tool 이벤트에서 특정 도구 호출 횟수 확인 (event_occurred 의 고수준 wrapper)."""
    calls = _tool_call_names(result)
    if tool_name is not None:
        matching = [c for c in calls if c == tool_name]
    elif any_of is not None:
        matching = [c for c in calls if c in any_of]
    else:
        matching = calls
    passed = len(matching) >= min_count
    target = tool_name or any_of or "any"
    return VerifyOutcome(
        passed=passed,
        detail=f"tool_called {target}: {len(matching)} calls (min: {min_count})",
    )


def verify_tool_not_called(
    result: HarnessResult,
    tool_name: str | None = None,
    any_of: list[str] | None = None,
    **_kw: Any,
) -> VerifyOutcome:
    """특정 도구가 호출되지 않았는지 확인 (relevance detection).

    tool_name 또는 any_of 둘 중 하나만 사용. 둘 다 없으면 아무 도구도 호출 안 함 검증.
    """
    calls = _tool_call_names(result)
    if tool_name is not None:
        bad = [c for c in calls if c == tool_name]
    elif any_of is not None:
        bad = [c for c in calls if c in any_of]
    else:
        bad = calls
    passed = len(bad) == 0
    target = tool_name or any_of or "any"
    return VerifyOutcome(
        passed=passed,
        detail=f"tool_not_called {target}: found {bad}" if bad else f"tool_not_called {target}: clean",
    )


_DECLARE_PATTERNS = (
    "잠시만 기다",
    "찾아드리겠",
    "확인해 드리겠",
    "해보겠습니다",
    "알려드리겠",
    "검색해 드리",
    "찾아드릴",
    "가져와 드리겠",
)


def verify_no_declare_only(
    result: HarnessResult,
    extra: list[str] | None = None,
    **_kw: Any,
) -> VerifyOutcome:
    """선언 문구만 있고 실제 도구 호출이 없는 패턴 차단.

    선언 문구가 있으면 반드시 using_tool 동반해야 pass. 선언 문구가 없으면 무조건 pass.
    """
    combined = " ".join(r or "" for r in result.responses)
    patterns = list(_DECLARE_PATTERNS) + list(extra or [])
    found_declare = [p for p in patterns if p in combined]
    has_tool_call = any(e.get("type") == "using_tool" for e in result.events)
    passed = (not found_declare) or has_tool_call
    if not found_declare:
        return VerifyOutcome(passed=True, detail="no declare-only patterns")
    if has_tool_call:
        return VerifyOutcome(passed=True, detail=f"declare+tool ok: {found_declare}")
    return VerifyOutcome(passed=False, detail=f"declare-only (no tool): {found_declare}")


def verify_plan_progress(result: HarnessResult, **_kw: Any) -> VerifyOutcome:
    """plan 을 emit 했다면 반드시 실제 (non-meta) 도구 호출 동반.

    plan_updated 이벤트는 있는데 generate_code/read_file/write_file 등 실행 도구가
    한 번도 안 호출되면 "plan 만 emit 하고 종료" 패턴 → FAIL.
    """
    plan_events = [e for e in result.events if e.get("type") == "plan_updated"]
    non_meta_tools = [
        e for e in result.events
        if e.get("type") == "using_tool"
        and e.get("data", {}).get("tool_name") not in (
            "think", "ask_user", "update_plan", "repair_tool"
        )
    ]
    if plan_events and not non_meta_tools:
        return VerifyOutcome(
            passed=False,
            detail=f"plan emitted ({len(plan_events)}) but no execution tool called",
        )
    return VerifyOutcome(
        passed=True,
        detail=f"plan={len(plan_events)} exec_tools={len(non_meta_tools)}",
    )


def verify_json_schema(
    result: HarnessResult,
    path: str,
    required_keys: list[str] | None = None,
    types: dict[str, str] | None = None,
    **_kw: Any,
) -> VerifyOutcome:
    """파일의 JSON 이 required_keys 모두 있고 types 일치하는지 확인.

    types 값: "string" | "number" | "array" | "object" | "boolean" | "null"
    """
    import json as _json

    matches = list(result.work_dir.glob(path))
    if not matches:
        return VerifyOutcome(passed=False, detail=f"No file matching {path}")

    try:
        data = _json.loads(matches[0].read_text(encoding="utf-8"))
    except Exception as e:
        return VerifyOutcome(passed=False, detail=f"JSON parse failed: {e}")

    if not isinstance(data, dict):
        return VerifyOutcome(passed=False, detail=f"Root is {type(data).__name__}, expected object")

    missing = [k for k in (required_keys or []) if k not in data]
    if missing:
        return VerifyOutcome(passed=False, detail=f"Missing keys: {missing}")

    type_map = {
        "string": str,
        "number": (int, float),
        "array": list,
        "object": dict,
        "boolean": bool,
        "null": type(None),
    }
    bad_types: list[str] = []
    for key, expected in (types or {}).items():
        if key not in data:
            continue
        py_type = type_map.get(expected)
        if py_type is None:
            continue
        if not isinstance(data[key], py_type):
            bad_types.append(f"{key}: got {type(data[key]).__name__}, expected {expected}")
    if bad_types:
        return VerifyOutcome(passed=False, detail="; ".join(bad_types))

    return VerifyOutcome(passed=True, detail=f"schema ok: {list(data.keys())}")


def verify_final_success(result: HarnessResult, value: bool = True, **_kw: Any) -> VerifyOutcome:
    """최종 응답이 있는지 확인."""
    last = result.responses[-1] if result.responses else None
    has_response = last is not None and len(last.strip()) > 0
    return VerifyOutcome(
        passed=has_response == value,
        detail=f"Has response: {has_response}",
    )


VERIFIERS: dict[str, Callable[..., VerifyOutcome]] = {
    "response_contains": verify_response_contains,
    "response_not_contains": verify_response_not_contains,
    "file_exists": verify_file_exists,
    "file_content_contains": verify_file_content_contains,
    "event_occurred": verify_event_occurred,
    "final_success": verify_final_success,
    "tool_called": verify_tool_called,
    "tool_not_called": verify_tool_not_called,
    "no_declare_only": verify_no_declare_only,
    "plan_progress": verify_plan_progress,
    "json_schema": verify_json_schema,
}
