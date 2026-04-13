"""구조화된 에러 코드 + 포맷터.

에러 코드로 LLM이 복구 전략을 세우기 쉽게 하고,
사용자·개발자에게 일관된 에러 메시지를 제공합니다.

언어 방침:
  - 에러 코드(ErrorCode): 영어 (국제 표준)
  - user-facing 메시지: 한국어 (한국어 사용자 대상)
  - logger: 영어 (스택트레이스 일관성)
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    MISSING_PARAM = "MISSING_PARAM"
    NOT_FOUND = "NOT_FOUND"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    TIMEOUT = "TIMEOUT"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    NETWORK_ERROR = "NETWORK_ERROR"
    INTERNAL = "INTERNAL"


def format_error(code: ErrorCode, message: str, *, detail: str = "") -> str:
    """구조화된 에러 문자열.

    형태: [ERROR_CODE] 메시지\\n상세: ...
    LLM이 에러 코드를 보고 복구 전략(MISSING_PARAM→파라미터 추가,
    NOT_FOUND→경로 확인 등)을 세울 수 있음.
    """
    parts = [f"[{code}] {message}"]
    if detail:
        parts.append(f"상세: {detail}")
    return "\n".join(parts)
