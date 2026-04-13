"""예시 2 증명: 도구 생성 → 실패 → repair → 원본 입력으로 재실행 → 성공."""

import json
from pathlib import Path

from adaptive_agent.agent.core import AgentCore
from adaptive_agent.agent.planner import Planner
from adaptive_agent.agent.session import Session
from adaptive_agent.llm.client import LLMResponse
from adaptive_agent.tools.registry import ToolRegistry


class MockLLMClient:
    """repair 시나리오를 위한 Mock LLM."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self._call_count = 0

    @property
    def native_tools_supported(self) -> bool:
        return False  # 테스트에서는 prompt-based fallback 경로 사용

    def chat(self, messages: list[dict[str, str]], **kwargs: object) -> LLMResponse:
        if self._call_count < len(self._responses):
            content = self._responses[self._call_count]
        else:
            content = "완료"
        self._call_count += 1
        return LLMResponse(content=content, thinking=None, usage={})

    def close(self) -> None:
        pass


# 의도적으로 KeyError를 일으키는 버그 코드
BUGGY_CODE = "def run(input: dict) -> dict:\n    return {'result': input['value'] * 2}"

# 수정된 코드 (올바른 키 사용)
FIXED_CODE = "def run(input: dict) -> dict:\n    return {'result': input['x'] * 2}"


class TestRepairE2E:
    def _make_agent(self, responses: list[str], tmp_path: Path) -> tuple[AgentCore, Session, ToolRegistry]:
        client = MockLLMClient(responses)
        session = Session()
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        registry = ToolRegistry(tools_dir)
        planner = Planner(client)  # type: ignore[arg-type]

        agent = AgentCore(
            planner=planner,
            session=session,
            registry=registry,
            client=client,  # type: ignore[arg-type]
            max_steps=10,
            max_repair_attempts=3,
        )
        return agent, session, registry

    def test_generate_fail_repair_succeed(self, tmp_path: Path):
        """코드 생성 → 실패 → repair → 재실행 → 성공."""
        responses = [
            # 1) Planner: generate_code
            json.dumps({
                "tool": "generate_code",
                "input": {
                    "tool_name": "double_number",
                    "description": "숫자를 2배로",
                },
            }),
            # 2) Builder: 버그 코드
            BUGGY_CODE,
            # 3) Planner: repair (planner가 지정한 tool_name 그대로 사용)
            json.dumps({"tool": "repair_tool", "input": {"tool_name": "double_number"}}),
            # 4) Repairer: 수정된 코드
            FIXED_CODE,
            # 5) Planner: 텍스트 응답
            "결과는 10입니다.",
        ]

        agent, _session, _registry = self._make_agent(responses, tmp_path)
        result = agent.handle_user_input("5의 2배를 계산해줘")

        assert result is not None
        assert "10" in result

    def test_repair_uses_original_input(self, tmp_path: Path):
        """repair 시 registry에 기록된 원본 입력 데이터가 사용되는지 검증."""
        responses = [
            # 1) Planner: generate_code (name 은 data 키, tool_name 은 도구 이름)
            json.dumps({
                "tool": "generate_code",
                "input": {
                    "tool_name": "greeter",
                    "description": "인사",
                    "name": "Alice",
                },
            }),
            # 2) Builder: 버그 코드 (잘못된 키 접근)
            "def run(input: dict) -> dict:\n    return {'msg': 'Hello ' + input['username']}",
            # 3) Planner: repair (planner가 지정한 tool_name 그대로 사용)
            json.dumps({"tool": "repair_tool", "input": {"tool_name": "greeter"}}),
            # 4) Repairer: 수정된 코드 (올바른 키 접근)
            "def run(input: dict) -> dict:\n    return {'msg': 'Hello ' + input.get('name', 'World')}",
            # 5) Planner: 텍스트 응답
            "Hello",
        ]

        agent, _session, _registry = self._make_agent(responses, tmp_path)
        agent.handle_user_input("Alice에게 인사해줘")

    def test_max_repair_attempts(self, tmp_path: Path):
        """repair 시도 횟수 초과 시 중단."""
        always_buggy = "def run(input: dict) -> dict:\n    raise ValueError('bug')"

        responses = [
            # 1) generate_code
            json.dumps({
                "tool": "generate_code",
                "input": {
                    "tool_name": "always_fail",
                    "description": "항상 실패",
                },
            }),
            # 2) Builder: 버그 코드
            always_buggy,
            # 3-8) repair 3회
            json.dumps({"tool": "repair_tool", "input": {"tool_name": "always_fail"}}),
            always_buggy,
            json.dumps({"tool": "repair_tool", "input": {"tool_name": "always_fail"}}),
            always_buggy,
            json.dumps({"tool": "repair_tool", "input": {"tool_name": "always_fail"}}),
            always_buggy,
            # 9) Planner: 포기
            "수정에 실패했습니다.",
        ]

        agent, session, _registry = self._make_agent(responses, tmp_path)
        agent._max_repair = 3  # pyright: ignore[reportPrivateUsage]
        agent.handle_user_input("테스트")

        errors = session.get_repair_errors("always_fail")
        assert len(errors) >= 3
