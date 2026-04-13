"""Built-in 도구 테스트."""

import base64
from pathlib import Path

import pytest

from adaptive_agent.tools.builtin import execute_builtin_tool


class TestGrepSearch:
    def test_skips_eval_artifacts_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "auth.py").write_text("@deprecated\ndef real_function():\n    pass\n", encoding="utf-8")

        sessions = tmp_path / "sessions" / "abc"
        sessions.mkdir(parents=True)
        (sessions / "events.jsonl").write_text("deprecated noise\n", encoding="utf-8")
        (tmp_path / "worker.log").write_text("deprecated noise\n", encoding="utf-8")

        result = execute_builtin_tool("grep_search", {"pattern": "deprecated"})

        assert result.success
        assert "src/auth.py" in result.output
        assert "events.jsonl" not in result.output
        assert "worker.log" not in result.output

    def test_includes_function_definition_after_decorator_match(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "utils.py").write_text(
            "@deprecated\ndef parse_legacy_config():\n    return {}\n",
            encoding="utf-8",
        )

        result = execute_builtin_tool(
            "grep_search",
            {"pattern": "deprecated", "path": "src", "file_glob": "*.py"},
        )

        assert result.success
        assert "@deprecated" in result.output
        assert "def parse_legacy_config" in result.output


class TestWriteFile:
    def test_writes_base64_binary_content(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        payload = base64.b64encode(b"sqlite-bytes").decode("ascii")

        result = execute_builtin_tool(
            "write_file",
            {"path": "users.db", "content": payload, "encoding": "base64"},
        )

        assert result.success
        assert (tmp_path / "users.db").read_bytes() == b"sqlite-bytes"
