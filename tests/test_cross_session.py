"""예시 4 증명: 도구 저장 → 새 세션에서 로드 → 재사용."""

import json
from pathlib import Path

from adaptive_agent.agent.session import Session
from adaptive_agent.tools.persistence import ToolPersistence
from adaptive_agent.tools.registry import ToolRegistry
from adaptive_agent.tools.runner import ToolRunner


TOOL_CODE = """\
def run(input: dict) -> dict:
    data = input['data']
    min_hp = input.get('min_hp', 100)
    filtered = [m for m in data if m['hp'] >= min_hp]
    avg = sum(m['hp'] for m in filtered) / len(filtered) if filtered else 0
    return {'monsters': [m['name'] for m in filtered], 'average_hp': avg}
"""

TOOL_MANIFEST: dict[str, object] = {
    "name": "filter_monsters",
    "description": "HP 기준으로 몬스터 필터링",
    "tags": ["data", "filter"],
}


class TestCrossSession:
    def test_save_and_reload(self, tmp_path: Path):
        """세션 1에서 도구 저장 → 세션 2에서 새 Registry로 로드 → 실행 성공."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()

        # --- 세션 1: 도구 저장 ---
        persistence = ToolPersistence(tools_dir)
        persistence.save("filter_monsters", TOOL_CODE, TOOL_MANIFEST)

        registry1 = ToolRegistry(tools_dir)
        tool_info = registry1.get_tool("filter_monsters")
        assert tool_info is not None
        descs1, _notices1 = registry1.get_tool_descriptions()
        assert "filter_monsters" in [t["name"] for t in descs1]

        # --- 세션 2: 새 Registry에서 로드 ---
        registry2 = ToolRegistry(tools_dir)

        # persistent 도구가 자동 로드됐는지 확인
        tool_info2 = registry2.get_tool("filter_monsters")
        assert tool_info2 is not None
        assert tool_info2["code"] == TOOL_CODE

        # 로드된 도구 실행
        runner = ToolRunner()
        input_data: dict[str, object] = {
            "data": [
                {"name": "Goblin", "hp": 80},
                {"name": "Orc", "hp": 150},
                {"name": "Dragon", "hp": 300},
            ],
            "min_hp": 100,
        }
        result = runner.run(tool_info2["code"], input_data)

        assert result.success
        assert result.output is not None
        parsed = json.loads(result.output)
        assert "Orc" in parsed["monsters"]
        assert "Dragon" in parsed["monsters"]
        assert "Goblin" not in parsed["monsters"]
        assert parsed["average_hp"] == 225.0

    def test_saved_tool_appears_in_descriptions(self, tmp_path: Path):
        """저장된 도구가 get_tool_descriptions()에 나타나는지 확인."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()

        persistence = ToolPersistence(tools_dir)
        persistence.save("my_tool", TOOL_CODE, {"name": "my_tool", "description": "test", "tags": ["test"]})

        registry = ToolRegistry(tools_dir)
        descs, _notices = registry.get_tool_descriptions()
        names = [t["name"] for t in descs]
        assert "my_tool" in names

    def test_tool_not_saved_without_success(self, tmp_path: Path):
        """성공하지 않은 도구는 저장 제안 대상이 아님을 검증."""
        session = Session()
        session.temp_tools["failed_tool"] = {
            "code": "def run(input): raise ValueError('bug')",
            "manifest": {"name": "failed_tool"},
        }
        # successful_tools에 없으면 저장 제안 안 함
        assert "failed_tool" not in session.successful_tools


class TestSaveTiming:
    def test_save_only_after_success(self, tmp_path: Path):
        """도구가 성공적으로 실행된 후에만 저장 제안됨을 검증."""
        session = Session()

        # 도구 생성 (아직 실행 안 됨)
        session.temp_tools["my_tool"] = {
            "code": TOOL_CODE,
            "manifest": TOOL_MANIFEST,
        }

        # 성공 표시 전: 저장 대상 아님
        assert "my_tool" not in session.successful_tools

        # 성공 표시
        session.mark_tool_success("my_tool")
        assert "my_tool" in session.successful_tools
