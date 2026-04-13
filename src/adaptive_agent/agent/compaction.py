"""Observation Masking 기반 컨텍스트 압축.

단일 진입점 `compact(session, stage=...)`:
  - "planner": 매 step 시작 전. 메시지 수가 limit 초과면 오래된 observation 마스킹.
  - "normal":  token budget 초과면 mask → sliding window 순으로 적용.
  - "aggressive": planner timeout 등 강한 압박 시 1개 observation 만 남기고 마스킹.

LLM 호출 없음. JetBrains Research (2025) 실험에서 observation masking 이
LLM 요약 대비 solve rate +2.6%, 비용 -52% 인 결과를 따른다. Ollama 로컬 환경에서는
LLM 요약 지연/파싱 실패 위험이 있어 무비용 마스킹이 적합.
"""

from __future__ import annotations

from typing import Literal

from adaptive_agent.agent.session import Session

_TOOL_RESULT_PREFIX = "[도구 "
_MASKED_BODY = "[결과 생략]"
_FIRST_MSG_MAX_CHARS = 2000  # 첫 메시지 truncate 한계 (sliding window 시)
_PLANNER_MAX_MESSAGES = 10
_DEFAULT_TOKEN_BUDGET = 128_000

CompactStage = Literal["planner", "normal", "aggressive"]


def compact(
    session: Session,
    *,
    stage: CompactStage = "normal",
    token_budget: int = _DEFAULT_TOKEN_BUDGET,
) -> None:
    """단일 진입점 — stage 에 따라 압축 강도 조절."""
    if stage == "planner":
        if len(session.messages) > _PLANNER_MAX_MESSAGES:
            _mask_old_observations(session, keep_last=1)
        return

    if stage == "aggressive":
        _mask_old_observations(session, keep_last=1)
        return

    # stage == "normal"
    if _estimate_tokens(session) <= token_budget:
        return
    _mask_old_observations(session, keep_last=2)
    if _estimate_tokens(session) <= token_budget:
        return
    _sliding_window(session, keep_recent=6)


def _estimate_tokens(session: Session) -> int:
    """세션의 전체 토큰 수를 추정. 한국어 기준 글자수 // 3."""
    total = 0
    for msg in session.messages:
        total += len(msg.get("content", "")) // 3
    return total


def _mask_old_observations(session: Session, *, keep_last: int = 2) -> None:
    """오래된 도구 결과의 본문을 마스킹. 헤더(성공/실패)는 보존.

    Reasoning(assistant 메시지)과 사용자 메시지는 건드리지 않는다.
    최근 keep_last개의 도구 결과는 원문 보존.
    """
    tool_result_indices = [
        i for i, msg in enumerate(session.messages)
        if msg.get("content", "").startswith(_TOOL_RESULT_PREFIX)
        or msg.get("role") == "tool"
    ]

    if len(tool_result_indices) <= keep_last:
        return

    to_mask = tool_result_indices[:-keep_last] if keep_last > 0 else tool_result_indices

    for idx in to_mask:
        content = session.messages[idx]["content"]
        header = content.split("\n", 1)[0]
        session.messages[idx]["content"] = f"{header}\n{_MASKED_BODY}"


def _sliding_window(session: Session, *, keep_recent: int = 6) -> None:
    """최후 수단: 최근 메시지만 남기고 나머지 삭제.

    첫 메시지(원본 요청)는 보존하되, token budget 보장을 위해
    _FIRST_MSG_MAX_CHARS 이상이면 truncate.
    """
    if len(session.messages) <= keep_recent:
        return

    first = session.messages[0]
    recent = session.messages[-keep_recent:]

    if recent[0] is first:
        session.messages = recent
        return

    content = first.get("content", "")
    if len(content) > _FIRST_MSG_MAX_CHARS:
        truncated = content[:_FIRST_MSG_MAX_CHARS] + f"\n...[원본 {len(content)}자 중 {_FIRST_MSG_MAX_CHARS}자만 보존]..."
        first = {**first, "content": truncated}

    session.messages = [first] + recent
