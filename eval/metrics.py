"""이벤트 기반 메트릭 수집."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from eval.harness import HarnessResult
from eval.verifiers import VerifyOutcome


@dataclass
class ScenarioMetrics:
    scenario_id: str
    category: str
    passed: bool
    total_steps: int
    tools_created: list[str]
    tools_reused: list[str]
    repair_attempts: dict[str, int]
    builtin_tools_used: list[str]
    latency_seconds: float
    llm_calls: int
    verification_results: list[VerifyOutcome] = field(default_factory=lambda: list[VerifyOutcome]())
    planner_trace: list[str] = field(default_factory=lambda: list[str]())
    builder_errors: list[str] = field(default_factory=lambda: list[str]())
    repair_history: list[dict[str, str]] = field(default_factory=lambda: list[dict[str, str]]())
    failure_attribution: str = ""
    debug_artifacts: dict[str, str] = field(default_factory=dict)
    split: str = "train"


def collect_metrics(
    result: HarnessResult,
    category: str,
    verify_outcomes: list[VerifyOutcome],
) -> ScenarioMetrics:
    """HarnessResult에서 메트릭 추출."""
    tools_created = [
        e["data"].get("tool_name", "")
        for e in result.events
        if e["type"] == "tool_created"
    ]

    tools_reused = [
        e["data"].get("tool_name", "")
        for e in result.events
        if e["type"] == "using_tool" and e["data"].get("tool_name", "") not in _BUILTIN_NAMES
    ]

    repair_attempts: dict[str, int] = {}
    for e in result.events:
        if e["type"] == "repairing_tool":
            name = e["data"].get("tool_name", "")
            repair_attempts[name] = repair_attempts.get(name, 0) + 1

    builtin_used = [
        e["data"].get("tool_name", "")
        for e in result.events
        if e["type"] == "using_tool" and e["data"].get("tool_name", "") in _BUILTIN_NAMES
    ]

    llm_calls = sum(
        1 for e in result.events
        if e["type"] in ("generating_code", "build_progress", "repairing_tool")
    )

    total_steps = sum(1 for e in result.events if e["type"] == "using_tool")

    planner_trace, builder_errors, repair_history = _extract_trace(result.events)
    passed = all(v.passed for v in verify_outcomes)
    failure_attribution = _attribute_failure(passed, repair_attempts, builder_errors)

    return ScenarioMetrics(
        scenario_id=result.scenario_id,
        category=category,
        passed=passed,
        total_steps=total_steps,
        tools_created=tools_created,
        tools_reused=tools_reused,
        repair_attempts=repair_attempts,
        builtin_tools_used=builtin_used,
        latency_seconds=result.elapsed_seconds,
        llm_calls=llm_calls,
        verification_results=verify_outcomes,
        planner_trace=planner_trace,
        builder_errors=builder_errors,
        repair_history=repair_history,
        failure_attribution=failure_attribution,
        debug_artifacts={
            "work_dir": str(result.work_dir),
            **({"event_log": str(result.event_log_path)} if result.event_log_path and result.event_log_path.exists() else {}),
            **({"worker_log": str(result.worker_log_path)} if result.worker_log_path and result.worker_log_path.exists() else {}),
        },
    )


def _extract_trace(
    events: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    """이벤트 리스트에서 planner trace, builder errors, repair history 추출."""
    planner_trace: list[str] = []
    builder_errors: list[str] = []
    repair_history: list[dict[str, str]] = []

    # 가장 최근에 생성/실행 시작된 도구 이름 (builder 에러 귀인용)
    last_created_tool = ""

    for e in events:
        etype = e["type"]
        data: dict[str, Any] = e.get("data", {})

        if etype == "using_tool":
            planner_trace.append(data.get("tool_name", ""))

        elif etype == "tool_created":
            last_created_tool = data.get("tool_name", "")

        elif etype == "tool_result" and not data.get("success", True):
            error_msg = data.get("error", "")
            tool_name = data.get("tool_name", "")
            # 방금 생성된 도구의 첫 실행 실패 → builder error
            if tool_name and tool_name == last_created_tool:
                builder_errors.append(f"{tool_name}: {error_msg}")

        elif etype == "repairing_tool":
            repair_history.append({
                "tool_name": data.get("tool_name", ""),
                "attempt": str(data.get("attempt", "")),
            })

    return planner_trace, builder_errors, repair_history


def _attribute_failure(
    passed: bool,
    repair_attempts: dict[str, int],
    builder_errors: list[str],
) -> str:
    """실패 원인을 프롬프트 타입에 귀인.

    우선순위:
      1. builder_errors (방금 생성된 도구의 첫 실행 실패 = 망가진 코드)
         → 후속 repair 가 있었더라도 root cause 는 builder.
      2. repair_attempts > 0 (첫 실행은 성공했지만 이후 repair 가 모두 실패)
         → repairer 가 복구 못함.
      3. 그 외 → planner (잘못된 tool 선택, stuck loop, step 초과 등).
    """
    if passed:
        return ""

    if builder_errors:
        return "builder"

    if repair_attempts and sum(repair_attempts.values()) > 0:
        return "repairer"

    return "planner"


# Single source of truth: registry 의 BUILTIN_NAMES 그대로 사용.
# registry 가 새 built-in 을 추가하면 metrics 분류도 자동 동기화된다.
from adaptive_agent.tools.registry import BUILTIN_NAMES as _BUILTIN_NAMES  # noqa: E402
