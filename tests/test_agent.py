"""Agent 핵심 루프 E2E 테스트 (Mock LLM)."""
import json
from pathlib import Path
from typing import Any

import pytest

from adaptive_agent.agent.core import AgentCore
from adaptive_agent.agent.planner import Planner
from adaptive_agent.agent.session import Session
from adaptive_agent.llm.client import LLMResponse
from adaptive_agent.tools.registry import ToolRegistry


class MockLLMClient:
    """테스트용 Mock LLM. 미리 정한 응답을 순서대로 반환."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self._call_count = 0

    @property
    def native_tools_supported(self) -> bool:
        return False  # 테스트에서는 prompt-based fallback 경로 사용

    def chat(self, messages: list[dict[str, Any]], **kwargs: object) -> LLMResponse:
        if self._call_count < len(self._responses):
            content = self._responses[self._call_count]
        else:
            content = "완료"
        self._call_count += 1
        return LLMResponse(content=content, thinking=None, usage={})

    def close(self) -> None:
        pass


class MockNativeLLMClient:
    """native tool calling 경로를 위한 Mock LLM."""

    def __init__(self, read_path: str):
        self._read_path = read_path
        self._call_count = 0
        self.messages_seen: list[list[dict[str, Any]]] = []

    @property
    def native_tools_supported(self) -> bool:
        return True

    def chat(self, messages: list[dict[str, Any]], **kwargs: object) -> LLMResponse:
        self.messages_seen.append(messages)
        if self._call_count == 0:
            self._call_count += 1
            return LLMResponse(
                content="",
                thinking=None,
                usage={},
                tool_calls=[{"tool": "read_file", "input": {"path": self._read_path}}],
            )

        self._call_count += 1
        return LLMResponse(content="파일 내용을 확인했습니다.", thinking=None, usage={})

    def close(self) -> None:
        pass


class TestAgentCore:
    def _make_agent(
        self,
        responses: list[str] | None = None,
        *,
        client: Any | None = None,
        status_callback: Any | None = None,
        ask_user_callback: Any | None = None,
    ) -> tuple[AgentCore, Session]:
        llm_client = client or MockLLMClient(responses or [])
        session = Session()
        tools_dir = Path("/tmp/test_agent_tools")
        tools_dir.mkdir(parents=True, exist_ok=True)
        registry = ToolRegistry(tools_dir)
        planner = Planner(llm_client)  # type: ignore[arg-type]

        agent = AgentCore(
            planner=planner,
            session=session,
            registry=registry,
            client=llm_client,  # type: ignore[arg-type]
            max_steps=10,
            max_repair_attempts=3,
            status_callback=status_callback,
            ask_user_callback=ask_user_callback,
        )
        return agent, session

    def test_text_response(self):
        """Planner가 일반 텍스트를 반환하면 바로 응답."""
        responses = ["안녕하세요!"]
        agent, _session = self._make_agent(responses)
        result = agent.handle_user_input("안녕")
        assert result == "안녕하세요!"

    def test_tool_call_then_respond(self):
        """tool call 후 텍스트 응답."""
        responses = [
            json.dumps({"tool": "read_file", "input": {"path": "nonexistent.txt"}}),
            "파일을 찾을 수 없습니다.",
        ]
        agent, _session = self._make_agent(responses)
        result = agent.handle_user_input("파일 읽어줘")
        assert result is not None
        assert "찾을 수 없" in result

    def test_generate_code_and_respond(self):
        """generate_code → 실행 성공 → 텍스트 응답."""
        responses = [
            # Planner: generate_code
            json.dumps({
                "tool": "generate_code",
                "input": {
                    "tool_name": "double_number",
                    "description": "숫자를 2배로",
                },
            }),
            # Builder: code
            "def run(input: dict) -> dict:\n    return {'result': 5 * 2}",
            # Planner: 텍스트 응답
            "결과는 10입니다.",
        ]
        agent, _session = self._make_agent(responses)
        result = agent.handle_user_input("5의 2배는?")
        assert result is not None
        assert "10" in result

    def test_max_steps_guard(self):
        """step 상한 초과 시 중단."""
        responses = [
            json.dumps({"tool": f"tool_{i}", "input": {}})
            for i in range(20)
        ]
        agent, _session = self._make_agent(responses)
        agent._max_steps = 3  # pyright: ignore[reportPrivateUsage]
        result = agent.handle_user_input("테스트")
        assert result is not None
        assert "최대" in result or "단계" in result

    def test_multi_turn_context(self):
        """멀티턴 대화에서 컨텍스트가 유지되는지 확인."""
        responses = [
            # 1번째 입력: read_file → 실패 → 텍스트 응답
            json.dumps({"tool": "read_file", "input": {"path": "nonexistent.csv"}}),
            "파일을 찾을 수 없습니다.",
            # 2번째 입력: 이전 대화를 기반으로 텍스트 응답
            "이전에 파일을 찾을 수 없었습니다. 경로를 확인해주세요.",
        ]
        agent, session = self._make_agent(responses)
        agent.handle_user_input("test.csv 읽어줘")
        result = agent.handle_user_input("왜 실패했어?")
        # 세션 히스토리에 두 턴의 메시지가 모두 있는지 확인
        user_msgs = [m for m in session.messages if m["role"] == "user" and not m["content"].startswith("[도구")]
        assert len(user_msgs) == 2
        assert result is not None

    def test_native_tool_roundtrip_records_tool_messages(self, tmp_path: Path):
        file_path = tmp_path / "hello.txt"
        file_path.write_text("hello", encoding="utf-8")

        client = MockNativeLLMClient(str(file_path))
        agent, session = self._make_agent(client=client)
        result = agent.handle_user_input("파일을 읽어줘")

        assert result == "파일 내용을 확인했습니다."
        assert session.native_tools is True
        assert any("tool_calls" in msg for msg in client.messages_seen[1])
        assert any(msg.get("role") == "tool" for msg in client.messages_seen[1])

    def test_suggested_file_result_triggers_write_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        events: list[dict[str, Any]] = []
        responses = [
            json.dumps({
                "tool": "generate_code",
                "input": {
                    "tool_name": "top5_products_writer",
                    "description": "상위 5개 제품을 계산해서 top5.json으로 저장",
                },
            }),
            (
                "import json\n"
                "def run(input: dict) -> dict:\n"
                "    rows = [{'product': '노트북', 'sales': 56400000}]\n"
                "    content = json.dumps(rows, ensure_ascii=False, indent=2)\n"
                "    return {\n"
                "        'result': rows,\n"
                "        'suggested_file': {'path': 'top5.json', 'content': content},\n"
                "    }"
            ),
            "top5.json 저장 완료",
        ]
        agent, _session = self._make_agent(
            responses,
            status_callback=lambda event, data: events.append({"type": event, "data": data}),
        )

        result = agent.handle_user_input("CSV 상위 5개를 저장해줘")

        assert result == "top5.json 저장 완료"
        assert (tmp_path / "top5.json").exists()
        assert "노트북" in (tmp_path / "top5.json").read_text(encoding="utf-8")
        assert any(
            e["type"] == "using_tool" and e["data"].get("tool_name") == "write_file"
            for e in events
        )

    def test_generate_code_dehydrates_ref_from_observation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Planner 가 명시한 $ref 를 core 가 observation 에서 dehydrate + JSON parse."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "users.json").write_text('[{"id": 1, "age": 28}]', encoding="utf-8")
        events: list[dict[str, Any]] = []
        responses = [
            json.dumps({"tool": "read_file", "input": {"path": "users.json"}}),
            json.dumps({
                "tool": "generate_code",
                "input": {
                    "tool_name": "users_passthrough",
                    "description": "JSON 사용자 데이터를 그대로 반환",
                    "users": {"$ref": "users.json"},
                },
            }),
            (
                "def run(input: dict) -> dict:\n"
                "    return {'result': input['users']}"
            ),
            "완료",
        ]
        agent, _session = self._make_agent(
            responses,
            status_callback=lambda event, data: events.append({"type": event, "data": data}),
        )

        result = agent.handle_user_input("users.json을 처리해줘")

        assert result == "완료"
        creating = next(e for e in events if e["type"] == "creating_tool")
        # JSON 파일은 _resolve_refs 가 자동 parse → list 로 dehydrate
        assert creating["data"]["input"]["users"] == [{"id": 1, "age": 28}]
        assert "_data" not in creating["data"]["input"]
        assert "_source_path" not in creating["data"]["input"]

    def test_suggested_files_result_triggers_multiple_writes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        responses = [
            json.dumps({
                "tool": "generate_code",
                "input": {
                    "tool_name": "user_status_splitter",
                    "description": "active와 inactive 두 파일로 나눠 저장",
                },
            }),
            (
                "def run(input: dict) -> dict:\n"
                "    return {\n"
                "        'result': 'ok',\n"
                "        'suggested_files': [\n"
                "            {'path': 'active_users.json', 'content': '[1]'},\n"
                "            {'path': 'inactive_users.json', 'content': '[2]'},\n"
                "        ],\n"
                "    }"
            ),
            "두 파일 저장 완료",
        ]
        agent, _session = self._make_agent(responses)

        result = agent.handle_user_input("분리해줘")

        assert result == "두 파일 저장 완료"
        assert (tmp_path / "active_users.json").read_text(encoding="utf-8") == "[1]"
        assert (tmp_path / "inactive_users.json").read_text(encoding="utf-8") == "[2]"

    def test_stats_suggested_file_preserves_builder_keys(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Builder가 사용자 언어에 맞는 키를 생성하면 그대로 저장된다."""
        monkeypatch.chdir(tmp_path)
        responses = [
            json.dumps({
                "tool": "generate_code",
                "input": {
                    "tool_name": "stats_calculator",
                    "description": "평균, 중앙값, 표준편차를 계산해서 stats.json으로 저장",
                },
            }),
            (
                "import json\n"
                "def run(input: dict) -> dict:\n"
                "    payload = {'평균': 1.0, '중앙값': 2.0, '표준편차': 3.0}\n"
                "    return {\n"
                "        'result': payload,\n"
                "        'suggested_file': {\n"
                "            'path': 'stats.json',\n"
                "            'content': json.dumps(payload, ensure_ascii=False, indent=2),\n"
                "        },\n"
                "    }"
            ),
            "stats 저장 완료",
        ]
        agent, _session = self._make_agent(responses)

        result = agent.handle_user_input("stats 저장해줘")

        assert result == "stats 저장 완료"
        content = (tmp_path / "stats.json").read_text(encoding="utf-8")
        assert "평균" in content
        assert "표준편차" in content

    def test_empty_input_returns_prompt(self):
        agent, _session = self._make_agent([])

        result = agent.handle_user_input("")

        assert result is not None
        assert "도와드릴까요" in result

class TestStuckDetection:
    def test_nested_dict_input(self):
        """nested dict input_data에서도 stuck detection이 크래시하지 않음."""
        from adaptive_agent.agent.session import Session
        session = Session()
        # nested dict — 이전 hash(sorted(items())) 방식은 여기서 실패했음
        result = session.record_action("generate_code", {"data": {"nested": [1, 2, 3]}, "key": "value"})
        assert result is False  # 첫 호출이므로 False

    def test_repeated_nested_triggers(self):
        """같은 nested dict 3회 반복 시 True 반환."""
        from adaptive_agent.agent.session import Session
        session = Session()
        data = {"config": {"mode": "fast"}, "count": 5}
        session.record_action("generate_code", data)
        session.record_action("generate_code", data)
        result = session.record_action("generate_code", data)
        assert result is True


class TestResolveToolName:
    """_resolve_tool_name 단위 테스트."""

    def test_no_collision(self):
        agent, _session = TestAgentCore()._make_agent([])
        assert agent._resolve_tool_name("brand_new") == "brand_new"

    def test_collision_appends_suffix(self):
        agent, _session = TestAgentCore()._make_agent([])
        agent._registry.register_session_tool("existing", "code", {})
        assert agent._resolve_tool_name("existing") == "existing_2"


class TestClassifyRuntimeError:
    """_classify_runtime_error 단위 테스트."""

    def test_name_error(self):
        from adaptive_agent.tools.runner import _classify_runtime_error
        assert _classify_runtime_error("NameError: name 'x' is not defined") == "IMPORT_OR_NAME"

    def test_key_error(self):
        from adaptive_agent.tools.runner import _classify_runtime_error
        assert _classify_runtime_error("KeyError: 'missing_key'") == "DATA_ACCESS"

    def test_type_error(self):
        from adaptive_agent.tools.runner import _classify_runtime_error
        assert _classify_runtime_error("TypeError: unsupported operand") == "TYPE_ERROR"

    def test_generic(self):
        from adaptive_agent.tools.runner import _classify_runtime_error
        assert _classify_runtime_error("SomeRandomError") == "RUNTIME"


class TestExtractManifestFromCode:
    """extract_manifest_from_code 단위 테스트."""

    def test_with_docstring(self):
        from adaptive_agent.tools.persistence import extract_manifest_from_code
        code = 'def run(input: dict) -> dict:\n    """HP 필터링 도구."""\n    return {}'
        result = extract_manifest_from_code("hp_filter", code)
        assert result["name"] == "hp_filter"
        assert result["description"] == "HP 필터링 도구."

    def test_without_docstring(self):
        from adaptive_agent.tools.persistence import extract_manifest_from_code
        code = "def run(input: dict) -> dict:\n    return {}"
        result = extract_manifest_from_code("tool", code, "fallback desc")
        assert result["description"] == "fallback desc"

    def test_syntax_error(self):
        from adaptive_agent.tools.persistence import extract_manifest_from_code
        result = extract_manifest_from_code("bad", "def run(", "desc")
        assert result["name"] == "bad"
        assert result["description"] == "desc"
