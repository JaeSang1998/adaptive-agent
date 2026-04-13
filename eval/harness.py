"""AgentCore를 non-interactive로 구동하는 하네스."""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adaptive_agent.agent.core import AgentCore
from adaptive_agent.agent.events import EventLogger
from adaptive_agent.agent.planner import Planner
from adaptive_agent.agent.session import Session
from adaptive_agent.config import Config
from adaptive_agent.llm.client import OllamaClient
from adaptive_agent.tools.registry import ToolRegistry

from eval.scenario import Scenario, Turn


@dataclass
class HarnessResult:
    scenario_id: str
    responses: list[str | None]
    events: list[dict[str, Any]]
    registry: ToolRegistry
    work_dir: Path
    elapsed_seconds: float
    event_log_path: Path | None
    worker_log_path: Path | None
    per_turn_outcomes: list[dict[str, Any]] = field(default_factory=list)


class AgentHarness:
    """시나리오를 기반으로 AgentCore를 프로그래밍 방식으로 실행."""

    def __init__(self, scenario: Scenario, work_dir: Path) -> None:
        self.scenario = scenario
        self.work_dir = work_dir
        self._ask_idx = 0
        self._events: list[dict[str, Any]] = []
        self._event_logger: EventLogger | None = None
        self._current_turn_idx: int = 0
        self._turn_ask_responses: list[list[str]] = []
        self._turn_ask_idx: list[int] = []

    def run(self) -> HarnessResult:
        """시나리오 실행 → HarnessResult 반환."""
        self._setup_files()
        self._event_logger = EventLogger(self.work_dir)

        config = Config.load(overrides=self.scenario.config_overrides)

        tools_dir = self.work_dir / "_tools"
        tools_dir.mkdir(exist_ok=True)

        client = OllamaClient(config)
        session = Session()
        registry = ToolRegistry(tools_dir)
        self._setup_tools(registry, tools_dir)
        planner = Planner(client)

        agent = AgentCore(
            planner=planner,
            session=session,
            registry=registry,
            client=client,
            max_steps=config.execution.max_steps,
            max_repair_attempts=config.execution.max_repair_attempts,
            status_callback=self._on_status,
            approval_callback=lambda _n, _d: True,
            ask_user_callback=self._scripted_ask_user,
        )

        start = time.time()
        responses: list[str | None] = []
        per_turn_outcomes: list[dict[str, Any]] = []

        # turns 가 명시되면 그대로 사용, 없으면 inputs 를 single-turn 리스트로 변환
        if self.scenario.turns:
            turns = self.scenario.turns
            self._turn_ask_responses = [list(t.ask_user_responses) for t in turns]
        else:
            turns = [Turn(user=u) for u in self.scenario.inputs]
            self._turn_ask_responses = [[] for _ in turns]

        original_cwd = os.getcwd()
        os.chdir(self.work_dir)
        try:
            for turn_idx, turn in enumerate(turns):
                self._current_turn_idx = turn_idx
                response = agent.handle_user_input(turn.user)
                responses.append(response)

                # per-turn verify — 현재까지 누적된 상태(snapshot)로 평가
                if turn.expect:
                    snapshot = HarnessResult(
                        scenario_id=self.scenario.id,
                        responses=list(responses),
                        events=list(self._events),
                        registry=registry,
                        work_dir=self.work_dir,
                        elapsed_seconds=time.time() - start,
                        event_log_path=None,
                        worker_log_path=None,
                    )
                    from eval.verifiers import VERIFIERS
                    for check in turn.expect:
                        verifier = VERIFIERS.get(check.type)
                        if verifier is None:
                            per_turn_outcomes.append({
                                "turn": turn_idx,
                                "type": check.type,
                                "passed": False,
                                "detail": f"Unknown verifier: {check.type}",
                            })
                            continue
                        outcome = verifier(snapshot, **check.params)
                        per_turn_outcomes.append({
                            "turn": turn_idx,
                            "type": check.type,
                            "passed": outcome.passed,
                            "detail": outcome.detail,
                        })
        finally:
            os.chdir(original_cwd)
            client.close()

        elapsed = time.time() - start

        return HarnessResult(
            scenario_id=self.scenario.id,
            responses=responses,
            events=self._events,
            registry=registry,
            work_dir=self.work_dir,
            elapsed_seconds=elapsed,
            event_log_path=self._event_logger.log_path if self._event_logger else None,
            worker_log_path=self.work_dir / "worker.log",
            per_turn_outcomes=per_turn_outcomes,
        )

    def _setup_files(self) -> None:
        """시나리오의 setup_files를 work_dir에 복사."""
        fixtures_dir = Path(__file__).parent / "fixtures"
        for entry in self.scenario.setup_files:
            src = fixtures_dir / entry["src"]
            dst = self.work_dir / entry["dst"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)

    def _setup_tools(self, registry: ToolRegistry, tools_dir: Path) -> None:
        """시나리오의 setup_tools를 registry에 persistent로 등록.

        setup_tools 각 항목은 fixtures/tools/{name}/ 디렉토리를 가리킴.
        해당 디렉토리에 tool.py + manifest.json이 존재해야 함.
        """
        fixtures_dir = Path(__file__).parent / "fixtures" / "tools"
        for entry in self.scenario.setup_tools:
            name = entry["name"]
            tool_fixture = fixtures_dir / name
            if not tool_fixture.is_dir():
                continue

            code_path = tool_fixture / "tool.py"
            manifest_path = tool_fixture / "manifest.json"
            if not code_path.exists() or not manifest_path.exists():
                continue

            code = code_path.read_text(encoding="utf-8")
            manifest: dict[str, Any] = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )

            # tools_dir에도 복사 (registry._load_persistent 경로 호환)
            dest_dir = tools_dir / name
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(code_path, dest_dir / "tool.py")
            shutil.copy2(manifest_path, dest_dir / "manifest.json")

            registry.register_persistent_tool(name, code, manifest)

    def _on_status(self, event: str, data: dict[str, Any]) -> None:
        self._events.append({"type": event, "data": data})
        if self._event_logger is not None:
            self._event_logger.emit(event, data)

    def _scripted_ask_user(self, question: str, choices: list[str] | None) -> str:
        """사전 정의된 응답 반환. turn-level → scenario-level → workspace 추론 → 기본."""
        # turn-level (multi-turn 시나리오) 먼저 소비
        if (
            self._turn_ask_responses
            and self._current_turn_idx < len(self._turn_ask_responses)
        ):
            while self._current_turn_idx >= len(self._turn_ask_idx):
                self._turn_ask_idx.append(0)
            idx = self._turn_ask_idx[self._current_turn_idx]
            turn_responses = self._turn_ask_responses[self._current_turn_idx]
            if idx < len(turn_responses):
                answer = turn_responses[idx]
                self._turn_ask_idx[self._current_turn_idx] = idx + 1
                return answer

        if self._ask_idx < len(self.scenario.ask_user_responses):
            answer = self.scenario.ask_user_responses[self._ask_idx]
            self._ask_idx += 1
            return answer

        # workspace에 해당 확장자 파일이 하나뿐이면 자동 응답
        auto = self._infer_file_from_workspace(question)
        if auto is not None:
            return auto

        if choices:
            return choices[0]
        return "네, 진행해주세요."

    def _infer_file_from_workspace(self, question: str) -> str | None:
        """eval 전용: 파일 경로를 묻는 질문에 workspace 파일로 자동 응답."""
        hint_markers = ("파일 경로", "경로를 알려", "파일을 알려", "데이터를 제공", "붙여넣어", "provide")
        if not any(m in question for m in hint_markers):
            return None

        text = question.lower()
        suffixes: tuple[str, ...] = ()
        if "json" in text:
            suffixes = (".json",)
        elif "csv" in text:
            suffixes = (".csv",)
        elif "sqlite" in text or ".db" in text:
            suffixes = (".json", ".csv")
        if not suffixes:
            return None

        candidates = [
            p for p in sorted(self.work_dir.iterdir())
            if p.is_file() and p.suffix.lower() in suffixes and not p.name.startswith((".", "_"))
        ]
        if len(candidates) == 1:
            return candidates[0].name
        return None
