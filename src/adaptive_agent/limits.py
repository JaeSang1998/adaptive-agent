"""Output / context truncation limits + small parsing helpers — single source of truth.

여러 layer 가 동일한 결과 데이터를 다르게 자르는 inconsistency 를 막기 위해
모든 truncation 상수를 한 곳에서 관리한다. 변경 시 호출처를 모두 자동 갱신.

호출 layer 별 책임:
  - tools/runner.py    : subprocess stdout 전체 cap (RUNNER_OUTPUT_BYTES)
  - agent/session.py   : 메시지 히스토리에 포함될 도구 결과 cap (SESSION_RESULT_CHARS)
  - agent/compaction.py: sliding window 시 첫 메시지 보존 한계 (FIRST_MSG_PRESERVE_CHARS)
  - tools/builtin.py   : grep 결과 라인 수 cap (GREP_MAX_RESULTS), glob 파일 수 cap

원칙: 안쪽 (runner) 가 가장 크고, 바깥쪽 (session, compaction) 으로 갈수록
좁아진다. 사용자가 보는 console 출력은 다시 main.py 가 따로 자른다.
"""

from __future__ import annotations

# ── Runner / subprocess output ─────────────────────────────────────────
# subprocess stdout 의 raw cap. 30KB. context rot 방지 + JSON 파싱은 truncation
# 전에 한 번 시도해서 parsed_output 손실 방지.
RUNNER_OUTPUT_BYTES = 30_000
RUNNER_OUTPUT_HEAD = 20_000  # truncate 시 앞쪽 보존
RUNNER_OUTPUT_TAIL = 10_000  # truncate 시 뒤쪽 보존

# ── Session message history ───────────────────────────────────────────
# 도구 결과 메시지 1건의 cap. session.add_tool_result 가 사용. 4KB.
# 큰 결과는 head 2K + ellipsis + tail 1K 형태로 보존.
SESSION_RESULT_CHARS = 4_000
SESSION_RESULT_HEAD = 2_000
SESSION_RESULT_TAIL = 1_000

# ── Compaction sliding window ─────────────────────────────────────────
# sliding window stage 에서 첫 메시지 (original_request) 보존 한계.
FIRST_MSG_PRESERVE_CHARS = 2_000

# ── Builtin tool result caps ──────────────────────────────────────────
GREP_MAX_RESULTS = 200          # grep_search line cap
GLOB_MAX_RESULTS = 100          # glob_search file cap
WEB_FETCH_MAX_BYTES = 100_000   # web_fetch response cap


# ── Helpers ───────────────────────────────────────────────────────────

def safe_int(value: object, default: int) -> int:
    """LLM input dict 에서 int 추출 시 안전한 변환.

    `int(input_data.get("offset"))` 패턴은 None / "abc" / [] 등이 들어오면
    크래시. 이 helper 는 None 이면 default, 변환 실패해도 default 반환.
    """
    if value is None:
        return default
    if isinstance(value, bool):  # bool 은 int 의 subclass — True 가 1로 통과되는 것 방지
        return default
    if isinstance(value, (int, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default
