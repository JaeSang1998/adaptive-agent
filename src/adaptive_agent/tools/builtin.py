"""내장 도구: read_file, write_file, list_directory, edit_file, glob_search, grep_search, run_bash, web_fetch."""

from __future__ import annotations

import base64
import re
import subprocess
from pathlib import Path
from typing import Any

from adaptive_agent.agent.session import ToolResult
from adaptive_agent.limits import GLOB_MAX_RESULTS, GREP_MAX_RESULTS, WEB_FETCH_MAX_BYTES, safe_int
from adaptive_agent.tools.errors import ErrorCode, format_error


def execute_builtin_tool(
    name: str,
    input_data: dict[str, Any],
) -> ToolResult:
    """built-in 도구 실행."""
    match name:
        case "read_file":
            return _read_file(input_data)
        case "write_file":
            return _write_file(input_data)
        case "list_directory":
            return _list_directory(input_data)
        case "edit_file":
            return _edit_file(input_data)
        case "glob_search":
            return _glob_search(input_data)
        case "grep_search":
            return _grep_search(input_data)
        case "run_bash":
            return _run_bash(input_data)
        case "web_fetch":
            return _web_fetch(input_data)
        case _:
            return ToolResult(tool_name=name, success=False, error=format_error(ErrorCode.NOT_FOUND, f"알 수 없는 내장 도구: {name}"))


def _read_file(input_data: dict[str, Any]) -> ToolResult:
    path_str = input_data.get("path", "")
    if not path_str:
        return ToolResult(tool_name="read_file", success=False, error=format_error(ErrorCode.MISSING_PARAM, "'path' 파라미터가 필요합니다."))

    path = Path(path_str)
    if not path.exists():
        return ToolResult(tool_name="read_file", success=False, error=format_error(ErrorCode.NOT_FOUND, f"파일을 찾을 수 없습니다: {path}"))
    if not path.is_file():
        return ToolResult(tool_name="read_file", success=False, error=format_error(ErrorCode.TYPE_MISMATCH, f"파일이 아닙니다: {path}"))

    offset = safe_int(input_data.get("offset"), 0)
    raw_limit = input_data.get("limit")
    limit: int | None = safe_int(raw_limit, 0) if raw_limit is not None else None

    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        total = len(lines)

        if limit is not None:
            selected = lines[offset:offset + limit]
        else:
            selected = lines[offset:]

        content = "".join(selected)
        # 전체 파일보다 적게 읽었으면 메타데이터 표시
        if offset > 0 or (limit is not None and offset + limit < total):
            header = f"[{total}줄 중 {offset+1}~{offset+len(selected)}줄]\n"
            content = header + content
        return ToolResult(tool_name="read_file", success=True, output=content)
    except Exception as e:
        return ToolResult(tool_name="read_file", success=False, error=format_error(ErrorCode.INTERNAL, "파일 읽기 실패", detail=str(e)))


def _write_file(input_data: dict[str, Any]) -> ToolResult:
    path_str = input_data.get("path", "")
    content = input_data.get("content", "")
    encoding = str(input_data.get("encoding", "utf-8")).lower()

    if not path_str:
        return ToolResult(tool_name="write_file", success=False, error=format_error(ErrorCode.MISSING_PARAM, "'path' 파라미터가 필요합니다."))

    path = Path(path_str)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if encoding == "base64":
            if not isinstance(content, str):
                return ToolResult(
                    tool_name="write_file",
                    success=False,
                    error=format_error(ErrorCode.VALIDATION_FAILED, "base64 인코딩 파일은 문자열 content가 필요합니다."),
                )
            try:
                path.write_bytes(base64.b64decode(content.encode("ascii")))
            except Exception as e:
                return ToolResult(
                    tool_name="write_file",
                    success=False,
                    error=format_error(ErrorCode.VALIDATION_FAILED, "base64 디코딩 실패", detail=str(e)),
                )
        elif encoding in {"utf-8", "utf8", "text"}:
            path.write_text(str(content), encoding="utf-8")
        else:
            return ToolResult(
                tool_name="write_file",
                success=False,
                error=format_error(ErrorCode.VALIDATION_FAILED, f"지원하지 않는 encoding: {encoding}"),
            )
        return ToolResult(tool_name="write_file", success=True, output=f"파일 저장 완료: {path}")
    except Exception as e:
        return ToolResult(tool_name="write_file", success=False, error=format_error(ErrorCode.INTERNAL, "파일 쓰기 실패", detail=str(e)))


def _list_directory(input_data: dict[str, Any]) -> ToolResult:
    path_str = input_data.get("path", ".")

    path = Path(path_str)
    if not path.exists():
        return ToolResult(tool_name="list_directory", success=False, error=format_error(ErrorCode.NOT_FOUND, f"디렉토리를 찾을 수 없습니다: {path}"))
    if not path.is_dir():
        return ToolResult(tool_name="list_directory", success=False, error=format_error(ErrorCode.TYPE_MISMATCH, f"디렉토리가 아닙니다: {path}"))

    try:
        entries = sorted(p.name for p in path.iterdir())
        return ToolResult(tool_name="list_directory", success=True, output="\n".join(entries))
    except Exception as e:
        return ToolResult(tool_name="list_directory", success=False, error=format_error(ErrorCode.INTERNAL, "디렉토리 목록 실패", detail=str(e)))


def _edit_file(input_data: dict[str, Any]) -> ToolResult:
    """파일의 특정 텍스트를 찾아 교체. old_text가 유니크해야 함."""
    path_str = input_data.get("path", "")
    old_text = input_data.get("old_text", "")
    new_text = input_data.get("new_text", "")

    if not path_str:
        return ToolResult(tool_name="edit_file", success=False, error=format_error(ErrorCode.MISSING_PARAM, "'path' 파라미터가 필요합니다."))
    if not old_text:
        return ToolResult(tool_name="edit_file", success=False, error=format_error(ErrorCode.MISSING_PARAM, "'old_text' 파라미터가 필요합니다."))

    path = Path(path_str)
    if not path.is_file():
        return ToolResult(tool_name="edit_file", success=False, error=format_error(ErrorCode.NOT_FOUND, f"파일을 찾을 수 없습니다: {path}"))

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return ToolResult(tool_name="edit_file", success=False, error=format_error(ErrorCode.INTERNAL, "파일 읽기 실패", detail=str(e)))

    count = content.count(old_text)
    if count == 0:
        return ToolResult(tool_name="edit_file", success=False, error=format_error(ErrorCode.VALIDATION_FAILED, "old_text를 파일에서 찾을 수 없습니다."))
    if count > 1:
        return ToolResult(tool_name="edit_file", success=False, error=format_error(ErrorCode.VALIDATION_FAILED, f"old_text가 {count}회 발견되어 유니크하지 않습니다. 더 구체적인 텍스트를 사용하세요."))

    new_content = content.replace(old_text, new_text, 1)
    try:
        path.write_text(new_content, encoding="utf-8")
        return ToolResult(tool_name="edit_file", success=True, output=f"파일 수정 완료: {path}")
    except Exception as e:
        return ToolResult(tool_name="edit_file", success=False, error=format_error(ErrorCode.INTERNAL, "파일 수정 실패", detail=str(e)))


def _glob_search(input_data: dict[str, Any]) -> ToolResult:
    """패턴 기반 파일 검색."""
    pattern = input_data.get("pattern", "")
    root = input_data.get("root", ".")

    if not pattern:
        return ToolResult(tool_name="glob_search", success=False, error=format_error(ErrorCode.MISSING_PARAM, "'pattern' 파라미터가 필요합니다."))

    # `..` 패턴은 root 밖으로 탈출 가능 (`**/../../etc/passwd` 등) → 차단.
    if ".." in Path(pattern).parts:
        return ToolResult(tool_name="glob_search", success=False, error=format_error(ErrorCode.VALIDATION_FAILED, "패턴에 '..' 사용 불가 (root 밖 탈출 방지)."))

    root_path = Path(root)
    if not root_path.is_dir():
        return ToolResult(tool_name="glob_search", success=False, error=format_error(ErrorCode.NOT_FOUND, f"디렉토리를 찾을 수 없습니다: {root}"))

    try:
        matches = sorted(str(p) for p in root_path.glob(pattern))
        if not matches:
            return ToolResult(tool_name="glob_search", success=True, output="일치하는 파일이 없습니다.")
        truncated = matches[:GLOB_MAX_RESULTS]
        result = "\n".join(truncated)
        if len(matches) > GLOB_MAX_RESULTS:
            result += f"\n... 외 {len(matches) - GLOB_MAX_RESULTS}개"
        return ToolResult(tool_name="glob_search", success=True, output=result)
    except Exception as e:
        return ToolResult(tool_name="glob_search", success=False, error=format_error(ErrorCode.INTERNAL, "파일 검색 실패", detail=str(e)))


def _collect_searchable_files(root: Path, file_glob: str) -> list[Path]:
    """검색 대상 파일 목록 수집. 숨김/내부 파일 제외."""
    if root.is_file():
        return [root]
    return [
        p for p in sorted(root.rglob(file_glob))
        if p.is_file() and not _should_skip_search_file(p, root)
    ]


def _search_in_files(
    files: list[Path],
    regex: re.Pattern[str],
    *,
    context_after: int,
    max_results: int,
) -> list[str]:
    """파일 목록에서 패턴 매칭. 데코레이터 → 심볼 정의 자동 추적."""
    results: list[str] = []
    seen: set[tuple[str, int]] = set()

    for file_path in files:
        try:
            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        for i, line in enumerate(lines, 1):
            if not regex.search(line):
                continue

            # 매칭 라인 추가
            _add_match(results, seen, file_path, lines, i)

            # 컨텍스트 라인 또는 데코레이터 → 심볼 정의
            if context_after > 0:
                for extra in range(1, context_after + 1):
                    _add_match(results, seen, file_path, lines, i + extra)
            elif _looks_like_decorator(line) and i < len(lines) and _looks_like_symbol_def(lines[i]):
                _add_match(results, seen, file_path, lines, i + 1)

            if len(results) >= max_results:
                return results
    return results


def _add_match(
    results: list[str],
    seen: set[tuple[str, int]],
    file_path: Path,
    lines: list[str],
    line_no: int,
) -> None:
    """중복 방지하며 매칭 결과 추가."""
    key = (str(file_path), line_no)
    if key in seen or not (1 <= line_no <= len(lines)):
        return
    seen.add(key)
    results.append(f"{file_path}:{line_no}: {lines[line_no - 1].rstrip()}")


def _grep_search(input_data: dict[str, Any]) -> ToolResult:
    """정규식 기반 파일 내용 검색. 라인 번호 포함."""
    pattern = input_data.get("pattern", "")
    path_str = input_data.get("path", ".")
    file_glob = input_data.get("file_glob", "*")
    context_after = safe_int(input_data.get("context_after"), 0)

    if not pattern:
        return ToolResult(tool_name="grep_search", success=False, error=format_error(ErrorCode.MISSING_PARAM, "'pattern' 파라미터가 필요합니다."))

    root = Path(path_str)
    if not root.exists():
        return ToolResult(tool_name="grep_search", success=False, error=format_error(ErrorCode.NOT_FOUND, f"경로를 찾을 수 없습니다: {path_str}"))

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return ToolResult(tool_name="grep_search", success=False, error=format_error(ErrorCode.VALIDATION_FAILED, f"잘못된 정규식: {e}"))

    files = _collect_searchable_files(root, file_glob)
    results = _search_in_files(files, regex, context_after=context_after, max_results=GREP_MAX_RESULTS)

    if not results:
        return ToolResult(tool_name="grep_search", success=True, output="일치하는 결과가 없습니다.")

    output = "\n".join(results)
    if len(results) >= GREP_MAX_RESULTS:
        output += f"\n... (최대 {GREP_MAX_RESULTS}개 표시)"
    return ToolResult(tool_name="grep_search", success=True, output=output)


_SEARCH_SKIP_PARTS = frozenset({
    "sessions",
    "_tools",
    "__pycache__",
    ".git",
    ".venv",
    "node_modules",
})

_SEARCH_SKIP_FILES = frozenset({
    "worker.log",
})


def _should_skip_search_file(file_path: Path, root: Path) -> bool:
    try:
        relative_parts = file_path.relative_to(root).parts
    except ValueError:
        relative_parts = file_path.parts

    for part in relative_parts:
        if part in _SEARCH_SKIP_PARTS or part.startswith("."):
            return True
    return file_path.name in _SEARCH_SKIP_FILES


def _looks_like_decorator(line: str) -> bool:
    return line.lstrip().startswith("@")


def _looks_like_symbol_def(line: str) -> bool:
    stripped = line.lstrip()
    return (
        stripped.startswith("def ")
        or stripped.startswith("async def ")
        or stripped.startswith("class ")
    )


# 3-tier 위험 분류: deny(자동 차단), warn(경고 후 사용자 판단), normal(일반 승인)
# primary defense는 approval callback. 패턴 매칭은 secondary defense.
#
# 우회 한계 (의도적으로 수용):
#   regex 기반 deny-list 는 obfuscation 우회 가능 — `$(echo rm) -rf /tmp`,
#   `RM=rm; $RM -rf /tmp`, base64 디코드 후 실행 등. 따라서 patten 매칭은
#   "사용자 실수 차단" 용이고, "악의적 사용자" 는 approval callback (사람
#   판단) 이 막는 layered 정책이다. 또한 warn 이상은 session cache 에서 제외해
#   매번 fresh approval 강제 (main.py:_on_approval).

_DENY_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\b\s+(-[rfRI]+\s+)*[/~]", "파일 시스템 삭제"),
    (r"\brm\b\s+(-[rfRI]+\s+)*\$", "변수/치환 인자로 rm"),
    (r"curl[^|]*\|\s*(bash|sh|zsh)\b", "원격 스크립트 실행"),
    (r"wget[^|]*\|\s*(bash|sh|zsh)\b", "원격 스크립트 실행"),
    (r">\s*/dev/", "디바이스 직접 쓰기"),
    (r"chmod\s+(-R\s+)?[0-7]*7[0-7]*7[0-7]*7", "전체 권한 부여"),
    (r"\bmkfs\.", "파일시스템 포맷"),
    (r"\bdd\s+if=", "블록 디바이스 조작"),
    (r":\(\)\s*\{[^}]*:\|:[^}]*\}", "fork bomb"),
    (r"base64\s+-d.*\|\s*(bash|sh)", "base64 디코드 후 실행"),
    (r"echo\s+[^|]*\|\s*base64\s+-d\s*\|\s*(bash|sh)", "base64 디코드 후 실행"),
]

_WARN_PATTERNS: list[tuple[str, str]] = [
    (r"\bpython[23]?\s+-c\s+", "인터프리터 코드 실행"),
    (r"\bperl\s+-e\s+", "인터프리터 코드 실행"),
    (r"\bruby\s+-e\s+", "인터프리터 코드 실행"),
    (r"\bnode\s+-e\s+", "인터프리터 코드 실행"),
    (r"\bbash\s+-c\s+", "셸 내 셸 실행"),
    (r"\bsh\s+-c\s+", "셸 내 셸 실행"),
    (r"\bsudo\b", "권한 상승"),
    (r">\s*/etc/", "시스템 설정 덮어쓰기"),
    (r"\beval\s+", "동적 명령 실행"),
    (r"\$\([^)]*\b(rm|chmod|chown|kill|dd|mkfs)\b", "치환 안에 위험 명령"),
    (r"\bkill(all)?\s+-9\b", "강제 프로세스 종료"),
    # rm 이 deny 패턴에 안 잡혔지만 (절대경로 아님) 그래도 위험 가능
    (r"\brm\s+(-[rfRI]+\s+)*\w", "파일 삭제 (상대경로)"),
]


def classify_command_risk(command: str) -> tuple[str, str | None]:
    """명령어 위험도 3-tier 분류.

    Returns:
        ("deny", "이유") — 자동 차단, 실행 불가
        ("warn", "이유") — 차단 안 하지만 approval 시 경고 + session 캐시 제외
        ("normal", None)  — 일반 승인 (한 번 a 누르면 세션 캐시 가능)
    """
    for pattern, reason in _DENY_PATTERNS:
        if re.search(pattern, command):
            return "deny", reason
    for pattern, reason in _WARN_PATTERNS:
        if re.search(pattern, command):
            return "warn", reason
    return "normal", None


def _run_bash(input_data: dict[str, Any]) -> ToolResult:
    """셸 명령 실행. 타임아웃 30초. 위험 명령은 거부."""
    command = input_data.get("command", "")
    if not command:
        return ToolResult(tool_name="run_bash", success=False, error=format_error(ErrorCode.MISSING_PARAM, "'command' 파라미터가 필요합니다."))

    risk, reason = classify_command_risk(command)
    if risk == "deny":
        return ToolResult(
            tool_name="run_bash", success=False,
            error=format_error(ErrorCode.PERMISSION_DENIED, f"위험한 명령이 감지되어 차단되었습니다: {reason}"),
        )

    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = proc.stdout
        if proc.stderr:
            output += f"\n[stderr]\n{proc.stderr}"

        if proc.returncode != 0:
            return ToolResult(
                tool_name="run_bash",
                success=False,
                error=format_error(ErrorCode.EXECUTION_FAILED, f"종료 코드 {proc.returncode}", detail=output),
            )
        return ToolResult(tool_name="run_bash", success=True, output=output)
    except subprocess.TimeoutExpired:
        return ToolResult(tool_name="run_bash", success=False, error=format_error(ErrorCode.TIMEOUT, "명령 실행 시간 초과 (30초)"))
    except Exception as e:
        return ToolResult(tool_name="run_bash", success=False, error=format_error(ErrorCode.INTERNAL, "명령 실행 실패", detail=str(e)))


def _is_private_ip(hostname: str) -> bool:
    """사설/내부 IP 대역 차단 (SSRF 방지)."""
    import ipaddress
    import socket
    try:
        addr = ipaddress.ip_address(socket.gethostbyname(hostname))
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except (socket.gaierror, ValueError):
        return False


def _web_fetch(input_data: dict[str, Any]) -> ToolResult:
    """URL의 내용을 가져옴. 최대 100KB. 사설 IP 차단. 실패 시 1회 retry."""
    import time
    import httpx
    from urllib.parse import urlparse, urlunparse, quote

    url = str(input_data.get("url", ""))
    if not url:
        return ToolResult(tool_name="web_fetch", success=False, error=format_error(ErrorCode.MISSING_PARAM, "'url' 파라미터가 필요합니다."))

    parsed = urlparse(url)
    hostname = parsed.hostname
    if hostname and _is_private_ip(hostname):
        return ToolResult(tool_name="web_fetch", success=False, error=format_error(ErrorCode.PERMISSION_DENIED, "사설/내부 네트워크 접근이 차단되었습니다."))

    # 비-ASCII 문자 (한글 등) 가 path/query 에 있으면 percent-encode
    # hostname 만 IDNA 인코딩, port 는 그대로 유지
    safe_netloc = parsed.netloc
    if parsed.hostname:
        try:
            idna_host = parsed.hostname.encode("idna").decode("ascii")
            if parsed.port:
                safe_netloc = f"{idna_host}:{parsed.port}"
            else:
                safe_netloc = idna_host
        except UnicodeError:
            safe_netloc = parsed.netloc
    safe_url = urlunparse((
        parsed.scheme,
        safe_netloc,
        quote(parsed.path, safe="/%"),
        quote(parsed.params, safe="=;%"),
        quote(parsed.query, safe="=&%"),
        quote(parsed.fragment, safe="%"),
    ))

    max_size = WEB_FETCH_MAX_BYTES
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    }

    last_err: Exception | None = None
    for attempt in range(2):
        try:
            with httpx.Client(http2=False, follow_redirects=True, timeout=15.0) as client:
                resp = client.get(safe_url, headers=headers)
                if resp.status_code >= 400:
                    return ToolResult(
                        tool_name="web_fetch", success=False,
                        error=format_error(ErrorCode.NETWORK_ERROR, f"HTTP {resp.status_code}: {resp.reason_phrase}"),
                    )
                text = resp.text[: max_size * 2]  # decoded text 기준
                if len(text.encode("utf-8")) > max_size:
                    text = text.encode("utf-8")[:max_size].decode("utf-8", errors="replace")
                return ToolResult(tool_name="web_fetch", success=True, output=text)
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
            last_err = e
            if attempt == 0:
                time.sleep(0.5)
                continue
        except Exception as e:
            return ToolResult(tool_name="web_fetch", success=False, error=format_error(ErrorCode.INTERNAL, "URL 가져오기 실패", detail=str(e)))

    return ToolResult(
        tool_name="web_fetch", success=False,
        error=format_error(ErrorCode.NETWORK_ERROR, "네트워크 일시 오류 (재시도 후 실패)", detail=str(last_err)),
    )
