"""도구 실행기: subprocess로 격리 실행.

보안 계층 (defense-in-depth):
  Layer 1: AST 정적 검증 (validator.py — 이 단계 이전에 완료)
  Layer 2: 이 모듈 — subprocess 격리 + timeout + 출력 제한
  Layer 3: Built-in 도구 승인 (builtin.py — 생성 코드 외부 경로)

macOS 환경 참고:
  resource.setrlimit()은 Darwin에서 비결정적 (Python issue #34602).
  timeout이 CPU 고갈에 대해 더 신뢰 가능한 방어.
  Production 환경: Docker cgroup 또는 Linux seccomp 권장.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adaptive_agent.limits import RUNNER_OUTPUT_BYTES, RUNNER_OUTPUT_HEAD, RUNNER_OUTPUT_TAIL
from adaptive_agent.tools.errors import ErrorCode, format_error


@dataclass(frozen=True, slots=True)
class RunResult:
    success: bool
    output: str | None = None
    error: str | None = None
    parsed_output: Any | None = None


# 도구를 실행하는 래퍼 스크립트
_WRAPPER_TEMPLATE = """\
import json
import sys
import importlib.util

# 도구 코드 로드
tool_path = sys.argv[1]
spec = importlib.util.spec_from_file_location("tool", tool_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# stdin에서 입력 읽기
data = json.loads(sys.stdin.read())
result = mod.run(data)
print(json.dumps(result, ensure_ascii=False))
"""


def _classify_runtime_error(stderr: str) -> str:
    """런타임 에러를 분류해 Repairer prompt 의 traceback prefix 로 부착.

    카테고리 자체는 향후 Repairer 가 복구 전략 분기에 활용할 수 있도록 함
    """
    if "NameError" in stderr or "ImportError" in stderr:
        return "IMPORT_OR_NAME"
    if "KeyError" in stderr or "IndexError" in stderr:
        return "DATA_ACCESS"
    if "TypeError" in stderr:
        return "TYPE_ERROR"
    if "ZeroDivisionError" in stderr:
        return "ARITHMETIC"
    if "FileNotFoundError" in stderr:
        return "FILE_NOT_FOUND"
    return "RUNTIME"


class ToolRunner:
    """subprocess로 도구 코드를 격리 실행."""

    def __init__(self, *, timeout: int = 30) -> None:
        self._timeout = timeout

    def run(self, source_code: str, input_data: dict[str, Any]) -> RunResult:
        """도구 코드를 임시 파일에 쓰고 subprocess로 실행.

        KeyboardInterrupt 시 child 를 명시적으로 kill 후 re-raise — REPL 이
        중단을 잡아 정상 복귀할 수 있도록 한다. (subprocess.run 의 묵시적
        cleanup 에 의존하지 않고 명시적 신호 처리)
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tool_path = Path(tmpdir) / "tool.py"
            wrapper_path = Path(tmpdir) / "wrapper.py"

            tool_path.write_text(source_code, encoding="utf-8")
            wrapper_path.write_text(_WRAPPER_TEMPLATE, encoding="utf-8")

            popen = subprocess.Popen(
                [sys.executable, str(wrapper_path), str(tool_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=tmpdir,
            )
            try:
                stdout_raw, stderr_raw = popen.communicate(
                    input=json.dumps(input_data, ensure_ascii=False),
                    timeout=self._timeout,
                )
            except subprocess.TimeoutExpired:
                popen.kill()
                popen.wait()
                return RunResult(
                    success=False,
                    error=format_error(ErrorCode.TIMEOUT, f"실행 시간 초과 ({self._timeout}초)"),
                )
            except KeyboardInterrupt:
                popen.kill()
                popen.wait()
                raise

            returncode = popen.returncode
            if returncode != 0:
                stderr_clean = (stderr_raw or "").strip()
                classified = _classify_runtime_error(stderr_clean)
                return RunResult(
                    success=False,
                    error=f"[{classified}] {stderr_clean}" if stderr_clean else f"종료 코드: {returncode}",
                )

            stdout = (stdout_raw or "").strip()
            if not stdout:
                return RunResult(success=True, output="{}", parsed_output={})

            # 1) 원본 stdout 으로 먼저 JSON 파싱 시도. truncation 이 파싱을 깨면 안 됨.
            parsed: Any = None
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                pass

            # 2) display/log 용 truncation 은 별도 변수로 (context rot 방지)
            display_output = stdout
            if len(display_output) > RUNNER_OUTPUT_BYTES:
                total = len(display_output)
                display_output = (
                    display_output[:RUNNER_OUTPUT_HEAD]
                    + f"\n...[출력 {total}자 중 {RUNNER_OUTPUT_BYTES}자만 표시]...\n"
                    + display_output[-RUNNER_OUTPUT_TAIL:]
                )

            if parsed is not None:
                return RunResult(
                    success=True,
                    output=json.dumps(parsed, ensure_ascii=False, indent=2),
                    parsed_output=parsed,
                )
            # JSON 파싱 실패해도 stdout (truncated) 원문 반환
            return RunResult(success=True, output=display_output)
