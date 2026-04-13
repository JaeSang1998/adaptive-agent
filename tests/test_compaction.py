"""Observation masking 단일 compact() 테스트.

ADR-001 의 multi-stage 가 single-stage 로 정리된 후의 동작 검증:
  - tool result body 만 마스킹, header 보존
  - 최근 _KEEP_RECENT_FULL(5) 개는 원문 유지
  - user/assistant/system 메시지는 건드리지 않음
  - idempotent: 이미 마스킹된 메시지 재호출 시 동일 결과
"""

from adaptive_agent.agent.compaction import _KEEP_RECENT_FULL, compact
from adaptive_agent.agent.session import Session


def _make_tool_msg(idx: int, body: str = "원문결과") -> dict[str, str]:
    return {"role": "user", "content": f"[도구 t{idx} 실행 성공]\n{body} {idx}"}


class TestCompact:
    def test_no_op_when_under_keep_threshold(self):
        """tool result 가 _KEEP_RECENT_FULL 개 이하면 변경 없음."""
        session = Session()
        for i in range(_KEEP_RECENT_FULL):
            session.messages.append(_make_tool_msg(i))

        compact(session)

        for i, msg in enumerate(session.messages):
            assert f"원문결과 {i}" in msg["content"]
            assert "[결과 생략]" not in msg["content"]

    def test_masks_old_tool_results(self):
        """오래된 tool result 본문은 마스킹, header 는 보존."""
        session = Session()
        for i in range(8):
            session.messages.append(_make_tool_msg(i))

        compact(session)

        # 처음 (8 - 5 = 3) 개 마스킹
        for i in range(3):
            content = session.messages[i]["content"]
            assert "[결과 생략]" in content
            assert content.startswith(f"[도구 t{i} 실행 성공]")
            assert "원문결과" not in content
        # 최근 5 개는 원문 유지
        for i in range(3, 8):
            assert f"원문결과 {i}" in session.messages[i]["content"]

    def test_preserves_user_and_assistant_messages(self):
        """user/assistant/system 메시지는 절대 건드리지 않음."""
        session = Session()
        session.messages = [
            {"role": "user", "content": "긴 사용자 메시지" * 100},
            {"role": "assistant", "content": "긴 응답" * 100},
            *[_make_tool_msg(i) for i in range(8)],
        ]

        compact(session)

        assert "사용자 메시지" in session.messages[0]["content"]
        assert "응답" in session.messages[1]["content"]
        # 첫 번째 도구 결과 (index 2) 는 가장 오래된 것 → 마스킹 대상
        assert "[결과 생략]" in session.messages[2]["content"]

    def test_native_tool_role_messages_also_masked(self):
        """role == 'tool' 형식 (native tool calling) 도 마스킹 대상."""
        session = Session()
        for i in range(8):
            session.messages.append({
                "role": "tool",
                "tool_name": f"t{i}",
                "content": f"[도구 t{i} 실행 성공]\n원문 {i}",
            })

        compact(session)

        # 첫 3 개 마스킹
        for i in range(3):
            assert "[결과 생략]" in session.messages[i]["content"]
        for i in range(3, 8):
            assert "원문" in session.messages[i]["content"]

    def test_idempotent(self):
        """이미 마스킹된 메시지에 compact 재호출 시 동일 결과 (header split 안전)."""
        session = Session()
        for i in range(8):
            session.messages.append(_make_tool_msg(i))

        compact(session)
        snapshot = [dict(m) for m in session.messages]
        compact(session)

        assert session.messages == snapshot


class TestObservationCap:
    """`session.observations` 의 hard cap (`_MAX_OBSERVATIONS=100`) 검증.

    $ref resolution fallback store 가 무한 grow 하지 않도록 oldest-evict.
    typical 시나리오 (≤30 step) 에선 절대 발동 안 함.
    """

    def test_observations_evict_oldest_beyond_cap(self):
        from adaptive_agent.agent.session import _MAX_OBSERVATIONS

        session = Session()
        # cap+5 개를 기록
        for i in range(_MAX_OBSERVATIONS + 5):
            session.record_observation(
                tool_name="read_file",
                input_data={"path": f"f{i}.txt"},
                output=f"content {i}",
            )

        assert len(session.observations) == _MAX_OBSERVATIONS
        # 가장 오래된 5개 (f0~f4) 는 evict 되어 lookup 실패
        for i in range(5):
            assert session.get_observation_by_path(f"f{i}.txt") is None
        # 최신 cap 개는 유지
        for i in range(5, _MAX_OBSERVATIONS + 5):
            assert session.get_observation_by_path(f"f{i}.txt") is not None
