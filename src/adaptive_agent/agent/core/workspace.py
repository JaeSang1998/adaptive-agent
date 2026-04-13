"""Planner grounding: 현재 디렉토리의 사용자 파일 스냅샷.

Agent 자체의 산출물 (sessions/, _tools/, worker.log, .git/, .venv/, __pycache__,
hidden 또는 underscore-prefixed) 은 자동 제외되어 planner 가 자기 자신의
로그를 사용자 데이터로 오인하는 것을 막는다.
"""

from __future__ import annotations

from pathlib import Path

from adaptive_agent.agent.session import Session

_HIDDEN_OR_INTERNAL = {
    "sessions",
    "_tools",
    "worker.log",
    ".git",
    ".venv",
    "__pycache__",
}


def is_visible_workspace_file(path: Path) -> bool:
    if path.name in _HIDDEN_OR_INTERNAL:
        return False
    if path.name.startswith(".") or path.name.startswith("_"):
        return False
    return path.is_file()


def candidate_workspace_files(suffixes: tuple[str, ...]) -> list[Path]:
    root = Path(".")
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    candidates: list[Path] = []
    for entry in entries:
        if not is_visible_workspace_file(entry):
            continue
        if suffixes and entry.suffix.lower() not in suffixes:
            continue
        candidates.append(entry)
    return candidates


def workspace_context(session: Session) -> str:
    """planner grounding을 위한 현재 디렉터리 파일 스냅샷 + 원본 요청."""
    parts: list[str] = []
    if session.original_request:
        parts.append(f"원본 요청: {session.original_request}")
    entries = candidate_workspace_files(())
    if entries:
        names = [entry.name for entry in entries[:20]]
        if len(entries) > 20:
            names.append(f"... 외 {len(entries) - 20}개")
        parts.append("\n".join(names))
    return "\n".join(parts) if parts else "(표시할 파일 없음)"
