"""평가 결과 요약 리포트."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from eval.metrics import ScenarioMetrics


@dataclass
class CategorySummary:
    total: int
    passed: int
    pass_rate: float


@dataclass
class EvalReport:
    timestamp: str
    total: int
    passed: int
    failed: int
    pass_rate: float
    avg_latency_seconds: float
    total_tools_created: int
    total_repairs: int
    by_category: dict[str, CategorySummary]
    scenarios: list[ScenarioMetrics]
    split_counts: dict[str, int] = field(default_factory=dict)

    def print_summary(self) -> None:
        """stdout에 요약 출력."""
        print(f"\n{'='*60}")
        print(f"  Evaluation Report  ({self.timestamp})")
        print(f"{'='*60}")
        print(f"  Total: {self.total}  Passed: {self.passed}  Failed: {self.failed}")
        print(f"  Pass Rate: {self.pass_rate:.1%}")
        print(f"  Avg Latency: {self.avg_latency_seconds:.1f}s")
        print(f"  Tools Created: {self.total_tools_created}")
        print(f"  Repair Attempts: {self.total_repairs}")
        print()

        if self.by_category:
            print("  By Category:")
            for cat, summary in sorted(self.by_category.items()):
                print(f"    {cat}: {summary.passed}/{summary.total} ({summary.pass_rate:.0%})")
            print()

        print("  Scenario Details:")
        for s in self.scenarios:
            status = "PASS" if s.passed else "FAIL"
            print(f"    [{status}] {s.scenario_id} ({s.latency_seconds:.1f}s)")
            for v in s.verification_results:
                mark = "V" if v.passed else "X"
                print(f"      [{mark}] {v.detail}")
            if not s.passed and s.debug_artifacts.get("work_dir"):
                print(f"      [i] debug: {s.debug_artifacts['work_dir']}")
        print(f"{'='*60}\n")

    def save_json(self, path: Path | str) -> None:
        """JSON 파일로 저장."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        failed_scenarios = [s for s in self.scenarios if not s.passed]
        data: dict[str, object] = {
            "timestamp": self.timestamp,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": self.pass_rate,
            "avg_latency_seconds": self.avg_latency_seconds,
            "avg_llm_calls": (
                sum(s.llm_calls for s in self.scenarios) / self.total
                if self.total else 0
            ),
            "total_tools_created": self.total_tools_created,
            "total_repairs": self.total_repairs,
            "split_counts": self.split_counts,
            "split_summary": {
                sp: {
                    "total": sum(1 for s in self.scenarios if s.split == sp),
                    "passed": sum(1 for s in self.scenarios if s.split == sp and s.passed),
                }
                for sp in ("train", "test")
                if any(s.split == sp for s in self.scenarios)
            },
            "failure_by_prompt": {
                "planner": sum(
                    1 for s in failed_scenarios if s.failure_attribution == "planner"
                ),
                "builder": sum(
                    1 for s in failed_scenarios if s.failure_attribution == "builder"
                ),
                "repairer": sum(
                    1 for s in failed_scenarios if s.failure_attribution == "repairer"
                ),
            },
            "by_category": {
                k: {"total": v.total, "passed": v.passed, "pass_rate": v.pass_rate}
                for k, v in self.by_category.items()
            },
            "scenarios": [
                {
                    "id": s.scenario_id,
                    "category": s.category,
                    "split": s.split,
                    "passed": s.passed,
                    "failure_attribution": s.failure_attribution,
                    "total_steps": s.total_steps,
                    "llm_calls": s.llm_calls,
                    "latency": s.latency_seconds,
                    "planner_trace": s.planner_trace,
                    "tools_created": s.tools_created,
                    "tools_reused": s.tools_reused,
                    "builtin_tools_used": s.builtin_tools_used,
                    "repair_attempts": s.repair_attempts,
                    "builder_errors": s.builder_errors,
                    "repair_history": s.repair_history,
                    "debug_artifacts": s.debug_artifacts,
                    "verifications": [
                        {"passed": v.passed, "detail": v.detail}
                        for v in s.verification_results
                    ],
                }
                for s in self.scenarios
            ],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def generate_report(results: list[ScenarioMetrics]) -> EvalReport:
    """ScenarioMetrics 리스트에서 EvalReport 생성."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)

    by_category: dict[str, list[ScenarioMetrics]] = defaultdict(list)
    for r in results:
        by_category[r.category].append(r)

    cat_summaries: dict[str, CategorySummary] = {}
    for cat, items in by_category.items():
        cat_passed = sum(1 for i in items if i.passed)
        cat_summaries[cat] = CategorySummary(
            total=len(items),
            passed=cat_passed,
            pass_rate=cat_passed / len(items) if items else 0,
        )

    avg_latency = sum(r.latency_seconds for r in results) / total if total else 0
    total_tools = sum(len(r.tools_created) for r in results)
    total_repairs = sum(sum(r.repair_attempts.values()) for r in results)

    split_counts: dict[str, int] = defaultdict(int)
    for r in results:
        split_counts[r.split] += 1

    return EvalReport(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        total=total,
        passed=passed,
        failed=total - passed,
        pass_rate=passed / total if total else 0,
        avg_latency_seconds=avg_latency,
        total_tools_created=total_tools,
        total_repairs=total_repairs,
        by_category=cat_summaries,
        scenarios=results,
        split_counts=dict(split_counts),
    )
