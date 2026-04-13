# pyright: reportPrivateUsage=false
"""도구 레지스트리 테스트: context windowing, list_tools, 증식 방지."""

import pytest
from pathlib import Path

from adaptive_agent.tools.registry import ToolRegistry, MAX_PERSISTENT_IN_CONTEXT, MAX_SESSION_TOOLS_WARN


@pytest.fixture
def registry(tmp_path: Path) -> ToolRegistry:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    return ToolRegistry(tools_dir)


def _register_persistent(registry: ToolRegistry, count: int) -> None:
    """persistent 도구 N개 등록."""
    for i in range(count):
        registry.register_persistent_tool(
            name=f"tool_{i:03d}",
            code=f"def run(input): return {{'idx': {i}}}",
            manifest={"name": f"tool_{i:03d}", "description": f"도구 {i}번", "tags": [f"tag{i % 3}"]},
        )


class TestContextWindowing:
    def test_under_limit_shows_all(self, registry: ToolRegistry):
        """persistent 20개 이하면 전부 노출."""
        _register_persistent(registry, 10)
        descs, notices = registry.get_tool_descriptions()
        persistent_descs = [d for d in descs if d["name"].startswith("tool_")]
        assert len(persistent_descs) == 10
        # overflow notice 없음
        assert not any("저장된 도구가 더" in n for n in notices)

    def test_over_limit_shows_windowed(self, registry: ToolRegistry):
        """persistent 20개 초과 시 MAX_PERSISTENT_IN_CONTEXT개만 노출 + overflow notice."""
        _register_persistent(registry, 30)
        descs, notices = registry.get_tool_descriptions()
        persistent_descs = [d for d in descs if d["name"].startswith("tool_")]
        assert len(persistent_descs) == MAX_PERSISTENT_IN_CONTEXT
        # tool list 에는 pseudo-entry 없음
        assert all(not d["name"].startswith("_") for d in descs)
        # notice 에 overflow 안내
        overflow_notices = [n for n in notices if "저장된 도구가 더" in n]
        assert len(overflow_notices) == 1
        assert "10개" in overflow_notices[0]  # 30 - 20 = 10개

    def test_session_always_shown(self, registry: ToolRegistry):
        """세션 도구는 항상 전부 노출."""
        _register_persistent(registry, 30)
        registry.register_session_tool("my_session_tool", "def run(input): pass", {"name": "my_session_tool"})
        descs, _notices = registry.get_tool_descriptions()
        assert any(d["name"] == "my_session_tool" for d in descs)

    def test_session_warning_over_cap(self, registry: ToolRegistry):
        """세션 도구가 MAX_SESSION_TOOLS_WARN 초과 시 notice."""
        for i in range(MAX_SESSION_TOOLS_WARN + 5):
            registry.register_session_tool(
                f"sess_{i}", "def run(input): pass", {"name": f"sess_{i}"},
            )
        descs, notices = registry.get_tool_descriptions()
        # tool list 에는 pseudo-entry 없음
        assert all(not d["name"].startswith("_") for d in descs)
        warning_notices = [n for n in notices if "세션 도구가" in n]
        assert len(warning_notices) == 1
        assert "25개" in warning_notices[0]

    def test_builtin_always_present(self, registry: ToolRegistry):
        """built-in 도구는 항상 포함 (list_tools 포함)."""
        descs, _notices = registry.get_tool_descriptions()
        names = [d["name"] for d in descs]
        assert "read_file" in names
        assert "list_tools" in names
        assert "think" in names


class TestListTools:
    def test_empty_catalog(self, registry: ToolRegistry):
        """저장된 도구 없으면 적절한 메시지."""
        result = registry._execute_list_tools({})
        assert result.success
        assert "없습니다" in result.output

    def test_pagination(self, registry: ToolRegistry):
        """offset/limit 페이지네이션."""
        _register_persistent(registry, 25)
        result = registry._execute_list_tools({"offset": 0, "limit": 10})
        assert result.success
        assert "25개 중 1~10번" in result.output

        result2 = registry._execute_list_tools({"offset": 20, "limit": 10})
        assert "25개 중 21~25번" in result2.output

    def test_query_search(self, registry: ToolRegistry):
        """query 키워드 검색."""
        registry.register_persistent_tool(
            "csv_analyzer", "def run(i): pass",
            {"name": "csv_analyzer", "description": "CSV 분석 도구", "tags": ["csv", "data"]},
        )
        registry.register_persistent_tool(
            "json_parser", "def run(i): pass",
            {"name": "json_parser", "description": "JSON 파싱", "tags": ["json"]},
        )
        result = registry._execute_list_tools({"query": "csv"})
        assert result.success
        assert "csv_analyzer" in result.output
        assert "json_parser" not in result.output

    def test_query_no_match(self, registry: ToolRegistry):
        """검색 결과 없으면 적절한 메시지."""
        _register_persistent(registry, 5)
        result = registry._execute_list_tools({"query": "nonexistent"})
        assert result.success
        assert "일치하는" in result.output


class TestGetPersistentCatalog:
    def test_returns_total(self, registry: ToolRegistry):
        """전체 개수 반환."""
        _register_persistent(registry, 15)
        results, total = registry.get_persistent_catalog(offset=0, limit=5)
        assert total == 15
        assert len(results) == 5

    def test_query_filters(self, registry: ToolRegistry):
        """query로 name/description/tags 필터링."""
        registry.register_persistent_tool(
            "alpha", "def run(i): pass",
            {"name": "alpha", "description": "알파 도구", "tags": ["math"]},
        )
        registry.register_persistent_tool(
            "beta", "def run(i): pass",
            {"name": "beta", "description": "베타 도구", "tags": ["text"]},
        )
        results, total = registry.get_persistent_catalog(query="math")
        assert total == 1
        assert results[0]["name"] == "alpha"
