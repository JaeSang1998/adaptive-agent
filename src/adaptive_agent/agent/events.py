"""구조화된 이벤트 로깅: sessions/{session_id}/events.jsonl."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast


class EventLogger:
    """에이전트 실행 이벤트를 JSONL 파일로 기록."""

    def __init__(self, base_dir: Path, *, session_id: str | None = None) -> None:
        self._session_id = session_id or uuid.uuid4().hex[:12]
        self._session_dir = base_dir / "sessions" / self._session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._session_dir / "events.jsonl"

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def log_path(self) -> Path:
        return self._log_path

    def emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """이벤트를 JSONL 파일에 append."""
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": self._session_id,
            "type": event_type,
        }
        if data:
            # 직렬화 불가능한 값 필터링
            entry["data"] = _safe_serialize(data)

        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _safe_serialize(obj: Any) -> Any:
    """JSON 직렬화 가능하도록 변환."""
    if isinstance(obj, dict):
        d = cast(dict[str, Any], obj)
        return {str(k): _safe_serialize(v) for k, v in d.items()}
    if isinstance(obj, (list, tuple)):
        items = cast(list[Any], obj)
        return [_safe_serialize(v) for v in items]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)
