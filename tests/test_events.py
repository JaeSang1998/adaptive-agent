"""EventLogger 테스트."""

import json
from pathlib import Path

from adaptive_agent.agent.events import EventLogger


class TestEventLogger:
    def test_emit_creates_jsonl(self, tmp_path: Path):
        """emit()이 events.jsonl 파일을 생성하고 JSONL로 기록."""
        logger = EventLogger(tmp_path, session_id="test123")
        logger.emit("planning", {"step": 1})
        logger.emit("tool_result", {"tool_name": "doubler", "success": True})

        assert logger.log_path.exists()
        lines = logger.log_path.read_text().strip().split("\n")
        assert len(lines) == 2

        entry1 = json.loads(lines[0])
        assert entry1["type"] == "planning"
        assert entry1["session"] == "test123"
        assert entry1["data"]["step"] == 1

        entry2 = json.loads(lines[1])
        assert entry2["type"] == "tool_result"
        assert entry2["data"]["tool_name"] == "doubler"

    def test_session_directory_structure(self, tmp_path: Path):
        """세션별 디렉토리가 올바르게 생성."""
        logger = EventLogger(tmp_path, session_id="abc")
        logger.emit("test", {})
        assert (tmp_path / "sessions" / "abc" / "events.jsonl").exists()

    def test_safe_serialize(self, tmp_path: Path):
        """직렬화 불가능한 값도 처리."""
        logger = EventLogger(tmp_path, session_id="serial")
        logger.emit("test", {"path": Path("/tmp/test"), "normal": 42})

        lines = logger.log_path.read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["data"]["normal"] == 42
        assert isinstance(entry["data"]["path"], str)
