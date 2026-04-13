"""EvalRunner 회귀 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from eval.harness import AgentHarness
from eval.runner import EvalRunner
from eval.scenario import Scenario


def test_run_all_records_llm_timeout_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = EvalRunner(tmp_path)
    runner.scenarios = [
        Scenario(
            id="llm_timeout",
            name="LLM timeout",
            category="runtime",
            inputs=["테스트"],
            timeout_seconds=120,
        ),
    ]

    def raise_llm_timeout(_scenario: Scenario):
        raise RuntimeError("LLM API 호출 실패 (planner): ReadTimeout('slow response')")

    monkeypatch.setattr(runner, "_run_one", raise_llm_timeout)

    report = runner.run_all()

    assert report.total == 1
    assert report.failed == 1
    assert report.scenarios[0].verification_results[0].detail.startswith("LLM API 호출 실패")


def test_harness_infer_file_from_workspace(tmp_path: Path):
    """workspace에 JSON 파일이 하나뿐이면 파일 경로 질문에 자동 응답."""
    (tmp_path / "users.json").write_text("[]", encoding="utf-8")
    scenario = Scenario(id="test", name="test", category="test", inputs=[])
    harness = AgentHarness(scenario, tmp_path)

    answer = harness._infer_file_from_workspace(  # pyright: ignore[reportPrivateUsage]
        "변환할 JSON 데이터를 제공해 주세요. 파일 경로를 알려주시면 바로 작업을 시작하겠습니다.",
    )

    assert answer == "users.json"


def test_harness_infer_file_returns_none_when_multiple(tmp_path: Path):
    """후보 파일이 2개 이상이면 None 반환."""
    (tmp_path / "a.json").write_text("[]", encoding="utf-8")
    (tmp_path / "b.json").write_text("[]", encoding="utf-8")
    scenario = Scenario(id="test", name="test", category="test", inputs=[])
    harness = AgentHarness(scenario, tmp_path)

    answer = harness._infer_file_from_workspace(  # pyright: ignore[reportPrivateUsage]
        "JSON 파일 경로를 알려주세요.",
    )

    assert answer is None
