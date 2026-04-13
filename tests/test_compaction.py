"""Observation masking 기반 컨텍스트 압축 테스트 (단일 compact API)."""

from adaptive_agent.agent.compaction import compact
from adaptive_agent.agent.session import Session


class TestCompactNormal:
    def test_no_op_when_under_budget(self):
        """예산 이하면 아무것도 안 함."""
        session = Session()
        session.add_user_message("짧은 메시지")

        compact(session, token_budget=128_000)

        assert session.messages[0]["content"] == "짧은 메시지"

    def test_masks_old_tool_results(self):
        """Stage 1: 오래된 도구 결과의 본문이 마스킹되는지 확인."""
        session = Session()
        session.add_user_message("요청")
        for i in range(3):
            session.messages.append({
                "role": "user",
                "content": f"[도구 tool_{i} 실행 성공]\n{'x' * 3000}",
            })
        session.messages.append({
            "role": "user",
            "content": "[도구 tool_latest 실행 성공]\n최근 결과",
        })

        compact(session, token_budget=500)

        # 오래된 도구 결과는 마스킹 (헤더 보존)
        assert "[결과 생략]" in session.messages[1]["content"]
        assert session.messages[1]["content"].startswith("[도구 tool_0 실행 성공]")
        # 사용자 요청은 보존
        assert session.messages[0]["content"] == "요청"
        # 메시지 수 유지 (삭제 아님, 마스킹)
        assert len(session.messages) == 5

    def test_stage2_sliding_window_fallback(self):
        """Stage 1 후에도 예산 초과 시 Stage 2(sliding window) 발동."""
        session = Session()
        for _ in range(20):
            session.add_user_message("x" * 1000)
            session.add_assistant_message("y" * 1000)

        compact(session, token_budget=100)

        # 첫 메시지 + 최근 6개 이하
        assert len(session.messages) <= 7

    def test_preserves_user_and_assistant_messages(self):
        """사용자/assistant 메시지는 마스킹 대상 아님."""
        session = Session()
        session.messages = [
            {"role": "user", "content": "긴 사용자 메시지" * 100},
            {"role": "assistant", "content": "긴 응답" * 100},
            {"role": "user", "content": "[도구 x 실행 성공]\n결과"},
            {"role": "user", "content": "[도구 y 실행 성공]\n결과2"},
            {"role": "user", "content": "[도구 z 실행 성공]\n최근결과"},
        ]

        compact(session, token_budget=10)

        assert "사용자 메시지" in session.messages[0]["content"]
        assert "응답" in session.messages[1]["content"]


class TestCompactPlanner:
    def test_no_op_when_few_messages(self):
        """planner stage 는 메시지 수 limit 이하면 동작 안 함."""
        session = Session()
        for i in range(5):
            session.add_user_message(f"msg_{i}")

        compact(session, stage="planner")

        # 5개 → limit(10) 이하 → 변경 없음
        assert len(session.messages) == 5
        assert session.messages[0]["content"] == "msg_0"

    def test_masks_when_over_message_limit(self):
        """planner stage 는 메시지가 limit 넘으면 오래된 도구 결과를 마스킹."""
        session = Session()
        session.add_user_message("요청")
        for i in range(15):
            session.messages.append({
                "role": "user",
                "content": f"[도구 t{i} 실행 성공]\n원문결과 {i}",
            })

        compact(session, stage="planner")

        # 마지막 1 개 보존, 나머지 14 개 마스킹
        masked_count = sum(
            1 for m in session.messages
            if "[결과 생략]" in m.get("content", "")
        )
        assert masked_count == 14
        assert "원문결과 14" in session.messages[-1]["content"]


class TestCompactAggressive:
    def test_aggressive_keeps_only_one_observation(self):
        session = Session()
        for i in range(5):
            session.messages.append({
                "role": "user",
                "content": f"[도구 t{i} 실행 성공]\n원문 {i}",
            })

        compact(session, stage="aggressive")

        # 4 개 마스킹, 1 개 원문
        masked = sum(1 for m in session.messages if "[결과 생략]" in m["content"])
        assert masked == 4
        assert "원문 4" in session.messages[-1]["content"]
