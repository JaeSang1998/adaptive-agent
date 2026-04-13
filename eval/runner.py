"""Eval Runner: 시나리오 순회 → 실행 → 리포트."""

from __future__ import annotations

import logging
import multiprocessing as mp
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from eval.harness import AgentHarness
from eval.metrics import ScenarioMetrics, collect_metrics
from eval.report import EvalReport, generate_report
from eval.scenario import Scenario, load_all_scenarios
from eval.verifiers import VERIFIERS, VerifyOutcome


def _setup_worker_logging(work_dir: Path, level_name: str) -> None:
    """worker 프로세스용 파일 로깅 설정."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    level = getattr(logging, level_name.upper(), logging.INFO)
    log_path = work_dir / "worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
    ))

    root.setLevel(level)
    root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.INFO if level <= logging.INFO else logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _run_scenario_sync(scenario: Scenario, work_dir: Path) -> ScenarioMetrics:
    """단일 시나리오를 동기 실행하고 메트릭을 반환."""
    harness = AgentHarness(scenario, work_dir)
    result = harness.run()

    outcomes: list[VerifyOutcome] = []
    # scenario-level verify (최종 상태)
    for check in scenario.verify:
        verifier = VERIFIERS.get(check.type)
        if verifier is None:
            outcomes.append(VerifyOutcome(
                passed=False,
                detail=f"Unknown verifier: {check.type}",
            ))
            continue
        outcome = verifier(result, **check.params)
        outcomes.append(outcome)

    # per-turn verify 결과도 pass/fail 에 영향
    for turn_outcome in result.per_turn_outcomes:
        outcomes.append(VerifyOutcome(
            passed=bool(turn_outcome.get("passed")),
            detail=f"turn{turn_outcome.get('turn')} {turn_outcome.get('type')}: {turn_outcome.get('detail','')}",
        ))

    metrics = collect_metrics(result, scenario.category, outcomes)
    metrics.split = scenario.split
    return metrics


def _run_scenario_worker(
    scenario: Scenario,
    work_dir: str,
    log_level: str,
    conn: mp.connection.Connection,
) -> None:
    """별도 프로세스에서 시나리오 실행 후 결과를 부모에게 전송."""
    try:
        work_path = Path(work_dir)
        _setup_worker_logging(work_path, log_level)
        logging.getLogger(__name__).info(
            "Scenario worker start id=%s timeout=%ss work_dir=%s",
            scenario.id,
            scenario.timeout_seconds,
            work_path,
        )
        metrics = _run_scenario_sync(scenario, work_path)
        conn.send(("ok", metrics))
    except Exception as e:  # pragma: no cover - worker boundary
        conn.send(("error", str(e)))
    finally:
        conn.close()


def _safe_tail(path: Path, max_lines: int = 5) -> str | None:
    """파일 마지막 줄 일부를 안전하게 읽기."""
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return None
    tail = lines[-max_lines:]
    return " | ".join(line.strip() for line in tail if line.strip())[:500]


def _find_event_log(work_dir: Path) -> Path | None:
    matches = sorted(work_dir.glob("sessions/*/events.jsonl"))
    return matches[-1] if matches else None


def _merge_overrides(base: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in runtime.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _merge_overrides(merged[key], value)
        else:
            merged[key] = value
    return merged


def _timeout_metrics(scenario: Scenario, work_dir: Path) -> ScenarioMetrics:
    """시간 초과된 시나리오에 대한 실패 메트릭 생성."""
    event_log = _find_event_log(work_dir)
    worker_log = work_dir / "worker.log"
    event_tail = _safe_tail(event_log) if event_log else None
    worker_tail = _safe_tail(worker_log)

    detail = f"Timed out after {scenario.timeout_seconds}s"
    if event_tail:
        detail += f" | last_event={event_tail}"
    if worker_tail:
        detail += f" | last_log={worker_tail}"

    return ScenarioMetrics(
        scenario_id=scenario.id,
        category=scenario.category,
        passed=False,
        total_steps=0,
        tools_created=[],
        tools_reused=[],
        repair_attempts={},
        builtin_tools_used=[],
        latency_seconds=float(scenario.timeout_seconds),
        llm_calls=0,
        verification_results=[
            VerifyOutcome(
                passed=False,
                detail=detail,
            )
        ],
        debug_artifacts={
            "work_dir": str(work_dir),
            **({"event_log": str(event_log)} if event_log else {}),
            **({"worker_log": str(worker_log)} if worker_log.exists() else {}),
        },
        split=scenario.split,
    )


class EvalRunner:
    """전체 평가 실행기."""

    def __init__(
        self,
        scenarios_dir: Path,
        *,
        timeout_multiplier: float = 1.0,
        keep_workdir: bool = False,
        runtime_overrides: dict[str, Any] | None = None,
        log_level: str = "INFO",
        debug_dir: Path | None = None,
    ) -> None:
        self.scenarios = load_all_scenarios(scenarios_dir)
        self._timeout_multiplier = timeout_multiplier
        self._keep_workdir = keep_workdir
        self._runtime_overrides = runtime_overrides or {}
        self._log_level = log_level
        self._debug_dir = debug_dir

    def run_all(
        self,
        *,
        filter_ids: list[str] | None = None,
        filter_categories: list[str] | None = None,
        filter_split: str | None = None,
    ) -> EvalReport:
        """모든 시나리오 실행 → EvalReport 반환."""
        results: list[ScenarioMetrics] = []

        for scenario in self.scenarios:
            if filter_ids and scenario.id not in filter_ids:
                continue
            if filter_categories and scenario.category not in filter_categories:
                continue
            if filter_split and filter_split != "all" and scenario.split != filter_split:
                continue

            effective_timeout = max(1, int(round(scenario.timeout_seconds * self._timeout_multiplier)))
            print(f"Running: {scenario.id} ({scenario.name}) [timeout={effective_timeout}s]...")
            try:
                metrics = self._run_one(scenario)
                results.append(metrics)
                status = "PASS" if metrics.passed else "FAIL"
                print(f"  [{status}] {scenario.id} ({metrics.latency_seconds:.1f}s)")
                if not metrics.passed and metrics.debug_artifacts.get("work_dir"):
                    print(f"    debug: {metrics.debug_artifacts['work_dir']}")
            except Exception as e:
                print(f"  [ERROR] {scenario.id}: {e}")
                # 에러 발생 시 실패로 기록
                results.append(ScenarioMetrics(
                    scenario_id=scenario.id,
                    category=scenario.category,
                    passed=False,
                    total_steps=0,
                    tools_created=[],
                    tools_reused=[],
                    repair_attempts={},
                    builtin_tools_used=[],
                    latency_seconds=0,
                    llm_calls=0,
                    verification_results=[VerifyOutcome(passed=False, detail=str(e))],
                    debug_artifacts={},
                    split=scenario.split,
                ))

        return generate_report(results)

    def _run_one(self, scenario: Scenario) -> ScenarioMetrics:
        """단일 시나리오 실행. timeout_seconds를 넘기면 실패 처리."""
        effective_timeout = max(1, int(round(scenario.timeout_seconds * self._timeout_multiplier)))
        scenario = replace(
            scenario,
            timeout_seconds=effective_timeout,
            config_overrides=_merge_overrides(scenario.config_overrides, self._runtime_overrides),
        )

        if self._debug_dir is not None:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(
            prefix=f"eval_{scenario.id}_",
            dir=str(self._debug_dir) if self._debug_dir else None,
        ))
        ctx = mp.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe(duplex=False)
        proc = ctx.Process(
            target=_run_scenario_worker,
            args=(scenario, str(work_dir), self._log_level, child_conn),
        )
        proc.start()
        child_conn.close()

        proc.join(scenario.timeout_seconds)

        if proc.is_alive():
            proc.terminate()
            proc.join(5)
            if proc.is_alive():
                proc.kill()
                proc.join(5)
            parent_conn.close()
            return _timeout_metrics(scenario, work_dir)

        try:
            if parent_conn.poll():
                status, payload = parent_conn.recv()
                if status == "ok":
                    metrics = payload
                    if metrics.passed and not self._keep_workdir:
                        shutil.rmtree(work_dir, ignore_errors=True)
                    return metrics
                raise RuntimeError(f"{payload} (debug dir: {work_dir})")

            if proc.exitcode == 0:
                raise RuntimeError(f"Scenario process exited without returning metrics (debug dir: {work_dir})")
            raise RuntimeError(f"Scenario process exited with code {proc.exitcode} (debug dir: {work_dir})")
        finally:
            parent_conn.close()
