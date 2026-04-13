"""Observation masking — 단일 context compaction primitive (ADR-001).

오래된 tool result body 를 `[결과 생략]` 로 교체, header 만 보존. LLM 호출 없음.
"""

from __future__ import annotations

from adaptive_agent.agent.session import Session

_TOOL_RESULT_PREFIX = "[도구 "
_MASKED_BODY = "[결과 생략]"
_KEEP_RECENT_FULL = 5


def compact(session: Session) -> None:
    """오래된 tool result 들을 masking. 최근 _KEEP_RECENT_FULL 개는 원문 유지.

    user / assistant / system / assistant.tool_calls 메시지는 건드리지 않음.
    idempotent: 이미 마스킹된 메시지는 그대로 둔다 (header 만 남아있어 split 결과 동일).
    """
    indices = [
        i for i, msg in enumerate(session.messages)
        if msg.get("role") == "tool"
        or msg.get("content", "").startswith(_TOOL_RESULT_PREFIX)
    ]
    if len(indices) <= _KEEP_RECENT_FULL:
        return

    for idx in indices[:-_KEEP_RECENT_FULL]:
        msg = session.messages[idx]
        content = msg.get("content", "")
        header = content.split("\n", 1)[0]
        session.messages[idx] = {**msg, "content": f"{header}\n{_MASKED_BODY}"}
