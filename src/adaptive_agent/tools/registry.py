"""도구 레지스트리: built-in + session + persistent 3계층 관리."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adaptive_agent.agent.session import ToolResult


MAX_PERSISTENT_IN_CONTEXT = 20
MAX_SESSION_TOOLS_WARN = 20


class ToolRegistry:
    """도구 등록·검색·목록 관리."""

    def __init__(self, tools_dir: Path) -> None:
        self._tools_dir = tools_dir
        self._builtin: dict[str, dict[str, Any]] = _make_builtin_descriptors()
        self._session: dict[str, dict[str, Any]] = {}
        self._persistent: dict[str, dict[str, Any]] = {}
        self._last_inputs: dict[str, dict[str, Any]] = {}  # 도구별 마지막 입력
        self._last_outputs: dict[str, Any] = {}  # 도구별 마지막 성공 출력

        self._load_persistent()

    def get_tool_descriptions(self) -> tuple[list[dict[str, Any]], list[str]]:
        """Planner에 전달할 (도구 목록, 부가 안내 문구) 튜플.

        name은 반드시 registry key를 사용. manifest name과 다를 수 있으므로
        Planner가 use_tool할 때 registry key로 호출할 수 있도록.

        Context windowing: persistent 도구는 최대 MAX_PERSISTENT_IN_CONTEXT개만
        full description을 노출. 초과/세션 cap 등 메타 안내는 별도 notices
        리스트로 반환되어 tool list 와 섞이지 않음.
        """
        descs: list[dict[str, Any]] = []
        notices: list[str] = []

        # built-in: 항상 전부 노출
        for info in self._builtin.values():
            descs.append({"name": info["name"], "description": info["description"], "tags": info.get("tags", [])})

        # persistent: windowing 적용
        persistent_items = list(self._persistent.items())
        shown = persistent_items[:MAX_PERSISTENT_IN_CONTEXT]
        overflow = len(persistent_items) - len(shown)

        for key, info in shown:
            m = info.get("manifest", {})
            entry: dict[str, Any] = {"name": key, "description": m.get("description", ""), "tags": m.get("tags", [])}
            schema = m.get("input_schema")
            if schema and isinstance(schema, dict):
                entry["input_schema"] = schema
            descs.append(entry)

        if overflow > 0:
            notices.append(
                f"외 {overflow}개의 저장된 도구가 더 있습니다. list_tools 로 검색하세요."
            )

        # session: 항상 전부 노출 + soft cap 경고
        session_count = 0
        for key, info in self._session.items():
            m = info.get("manifest", {})
            entry = {"name": key, "description": m.get("description", ""), "tags": m.get("tags", [])}
            schema = m.get("input_schema")
            if schema and isinstance(schema, dict):
                entry["input_schema"] = schema
            descs.append(entry)
            session_count += 1

        if session_count > MAX_SESSION_TOOLS_WARN:
            notices.append(
                f"[주의] 세션 도구가 {session_count}개입니다. 기존 도구 재사용을 우선 검토하세요."
            )

        return descs, notices

    def get_tool(self, name: str) -> dict[str, Any] | None:
        """이름으로 도구 조회. session > persistent 순."""
        if name in self._session:
            return self._session[name]
        if name in self._persistent:
            return self._persistent[name]
        return None

    def register_session_tool(
        self, name: str, code: str, manifest: dict[str, Any],
    ) -> None:
        self._session[name] = {"code": code, "manifest": manifest}

    def update_session_tool(self, name: str, code: str) -> None:
        if name in self._session:
            self._session[name]["code"] = code

    def register_persistent_tool(
        self, name: str, code: str, manifest: dict[str, Any],
    ) -> None:
        self._persistent[name] = {"code": code, "manifest": manifest}

    def record_last_input(self, tool_name: str, input_data: dict[str, Any]) -> None:
        self._last_inputs[tool_name] = input_data

    def get_last_input(self, tool_name: str) -> dict[str, Any] | None:
        return self._last_inputs.get(tool_name)

    def record_last_output(self, tool_name: str, output: Any) -> None:
        self._last_outputs[tool_name] = output

    def get_last_output(self, tool_name: str) -> Any | None:
        return self._last_outputs.get(tool_name)

    def get_persistent_catalog(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
        query: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        """저장된 도구 카탈로그 조회. list_tools built-in에서 사용.

        Returns:
            (매칭된 도구 리스트, 전체 개수)
        """
        items = list(self._persistent.items())

        if query:
            q = query.lower()
            items = [
                (k, v) for k, v in items
                if q in k.lower()
                or q in (v.get("manifest", {}).get("description", "")).lower()
                or any(q in t.lower() for t in v.get("manifest", {}).get("tags", []))
            ]

        total = len(items)
        page = items[offset:offset + limit]

        results: list[dict[str, Any]] = []
        for key, info in page:
            m = info.get("manifest", {})
            results.append({
                "name": key,
                "description": str(m.get("description", "")),
                "tags": list(m.get("tags", [])),
            })
        return results, total

    def is_builtin(self, name: str) -> bool:
        return name in self._builtin

    def is_observation_producer(self, name: str) -> bool:
        """`observations` 에 결과를 누적할 read-only 도구인지.

        $ref dehydration 시 lookup 대상이 되며, 결과 데이터가 builder 의 input 으로
        재사용될 수 있는 도구만 여기 해당. builtin descriptor 의 `observation` flag.
        """
        info = self._builtin.get(name)
        return bool(info and info.get("observation", False))

    def requires_approval(self, name: str) -> bool:
        """도구가 사용자 승인을 필요로 하는지 확인.

        승인이 필요한 경우: 파일 쓰기, 셸 명령, 네트워크 접근 등 부작용이 있는 built-in 도구.
        생성/persistent 도구는 subprocess 격리 실행이므로 사전 승인 불필요.
        """
        if name in self._builtin:
            return self._builtin[name].get("requires_approval", False)
        return False

    def build_approval_data(
        self, name: str, input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """승인 콜백에 전달할 dict 를 builtin metadata 로 enrich.

        run_bash 같은 위험도 분류가 있는 도구는 risk/reason 을 추가.
        core 는 어떤 tool 이 어떤 enrichment 를 필요로 하는지 알 필요 없음.
        """
        approval_data: dict[str, Any] = dict(input_data)
        if name == "run_bash":
            from adaptive_agent.tools.builtin import classify_command_risk
            risk, reason = classify_command_risk(input_data.get("command", ""))
            if risk != "normal":
                approval_data["_risk"] = risk
                approval_data["_risk_reason"] = reason
        return approval_data

    def execute_builtin(
        self, name: str, input_data: dict[str, Any],
    ) -> ToolResult | None:
        """built-in 도구 실행. built-in이 아니면 None 반환."""
        if name not in self._builtin:
            return None

        # list_tools는 registry 접근이 필요하므로 여기서 직접 처리
        if name == "list_tools":
            return self._execute_list_tools(input_data)

        from adaptive_agent.tools.builtin import execute_builtin_tool
        return execute_builtin_tool(name, input_data)

    def _execute_list_tools(self, input_data: dict[str, Any]) -> ToolResult:
        """저장된 도구 카탈로그 조회. read_file과 동일한 pagination 패턴."""
        offset = int(input_data.get("offset", 0))
        limit = int(input_data.get("limit", 20))
        query = input_data.get("query", "")

        results, total = self.get_persistent_catalog(
            offset=offset, limit=limit, query=query,
        )

        if not results:
            if query:
                output = f"'{query}'에 일치하는 저장된 도구가 없습니다. (전체 {total}개)"
            else:
                output = "저장된 도구가 없습니다."
            return ToolResult(tool_name="list_tools", success=True, output=output)

        lines: list[str] = []
        header = f"[{total}개 중 {offset + 1}~{offset + len(results)}번]"
        if query:
            header += f" (검색: '{query}')"
        lines.append(header)

        for i, tool in enumerate(results, offset + 1):
            tags_list: list[str] = tool.get("tags", [])
            tags = ", ".join(tags_list)
            line = f"{i}. {tool['name']}: {tool['description']}"
            if tags:
                line += f" [tags: {tags}]"
            lines.append(line)

        return ToolResult(tool_name="list_tools", success=True, output="\n".join(lines))

    def _load_persistent(self) -> None:
        """~/.adaptive-agent/tools/ 에서 저장된 도구 로드."""
        if not self._tools_dir.exists():
            return

        from adaptive_agent.tools.persistence import ToolPersistence
        persistence = ToolPersistence(self._tools_dir)

        for tool_dir in self._tools_dir.iterdir():
            if not tool_dir.is_dir():
                continue
            loaded = persistence.load(tool_dir.name)
            if loaded:
                self._persistent[tool_dir.name] = loaded


def _make_builtin_descriptors() -> dict[str, dict[str, Any]]:
    return {
        "read_file": {
            "name": "read_file",
            "description": "파일의 내용을 읽어 반환합니다. offset(시작 줄, 0부터)과 limit(줄 수)로 부분 읽기 가능.",
            "tags": ["file", "read"],
            "requires_approval": False,
            "observation": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "파일 경로"},
                    "offset": {"type": "integer", "description": "시작 줄 번호 (0부터)"},
                    "limit": {"type": "integer", "description": "읽을 줄 수"},
                },
                "required": ["path"],
            },
        },
        "write_file": {
            "name": "write_file",
            "description": "파일에 내용을 씁니다. 기본은 UTF-8 텍스트이며, binary 파일은 encoding='base64'로 저장할 수 있습니다.",
            "tags": ["file", "write"],
            "requires_approval": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "파일 경로"},
                    "content": {"type": "string", "description": "파일에 쓸 내용"},
                    "encoding": {"type": "string", "description": "utf-8(기본) 또는 base64"},
                },
                "required": ["path", "content"],
            },
        },
        "list_directory": {
            "name": "list_directory",
            "description": "디렉토리의 파일/폴더 목록을 반환합니다.",
            "tags": ["file", "directory"],
            "requires_approval": False,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "디렉토리 경로 (기본: 현재 디렉토리)"},
                },
            },
        },
        "edit_file": {
            "name": "edit_file",
            "description": "파일의 특정 텍스트를 찾아 교체합니다. old_text는 유니크해야 합니다.",
            "tags": ["file", "edit"],
            "requires_approval": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "파일 경로"},
                    "old_text": {"type": "string", "description": "찾을 텍스트 (유니크해야 함)"},
                    "new_text": {"type": "string", "description": "교체할 텍스트"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
        "glob_search": {
            "name": "glob_search",
            "description": "패턴(예: '**/*.py')으로 파일을 검색합니다.",
            "tags": ["search", "file"],
            "requires_approval": False,
            "observation": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "글로브 패턴"},
                    "root": {"type": "string", "description": "검색 루트 디렉토리 (기본: .)"},
                },
                "required": ["pattern"],
            },
        },
        "grep_search": {
            "name": "grep_search",
            "description": "정규식으로 파일 내용을 검색합니다. 가능하면 path와 file_glob를 함께 지정하세요. context_after=N으로 일치 줄 뒤 N줄도 함께 반환합니다. decorator/annotation 같은 marker를 찾을 때는 def/class 패턴보다 marker 자체(예: @name)를 검색하고 context_after=1을 함께 쓰세요.",
            "tags": ["search", "content"],
            "requires_approval": False,
            "observation": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "정규식 패턴"},
                    "path": {"type": "string", "description": "검색 경로 (기본: .)"},
                    "file_glob": {"type": "string", "description": "파일 필터 글로브 (기본: *)"},
                    "context_after": {"type": "integer", "description": "일치 라인 뒤에 함께 반환할 줄 수"},
                },
                "required": ["pattern"],
            },
        },
        "run_bash": {
            "name": "run_bash",
            "description": "셸 명령을 실행합니다. 환경 탐색, 빌드, 테스트 등에 사용합니다.",
            "tags": ["shell", "execute"],
            "requires_approval": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "실행할 셸 명령"},
                },
                "required": ["command"],
            },
        },
        "web_fetch": {
            "name": "web_fetch",
            "description": "URL의 내용을 가져옵니다. (최대 100KB)",
            "tags": ["web", "fetch"],
            "requires_approval": True,
            "observation": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "가져올 URL"},
                },
                "required": ["url"],
            },
        },
        "list_tools": {
            "name": "list_tools",
            "description": "저장된 도구를 검색합니다. offset/limit로 페이지네이션, query로 키워드 검색. 도구 목록에 없는 저장된 도구를 찾을 때 사용.",
            "tags": ["meta", "discovery"],
            "requires_approval": False,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "검색 키워드"},
                    "offset": {"type": "integer", "description": "시작 위치 (기본: 0)"},
                    "limit": {"type": "integer", "description": "최대 결과 수 (기본: 20)"},
                },
            },
        },
        "think": {
            "name": "think",
            "description": "복잡한 문제를 단계별로 분석합니다. reasoning에 추론 과정을 작성하세요.",
            "tags": ["meta", "reasoning"],
            "requires_approval": False,
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {"type": "string", "description": "추론 과정"},
                },
                "required": ["reasoning"],
            },
        },
        "ask_user": {
            "name": "ask_user",
            "description": "사용자에게 질문합니다. choices로 선택지를 제공할 수 있습니다.",
            "tags": ["meta", "interaction"],
            "requires_approval": False,
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "질문 내용"},
                    "choices": {"type": "array", "items": {"type": "string"}, "description": "선택지 목록 (선택)"},
                },
                "required": ["question"],
            },
        },
        "update_plan": {
            "name": "update_plan",
            "description": "작업 계획을 생성하거나 갱신합니다. steps로 새 계획을 세우고, completed/in_progress로 진행 상태를 관리합니다.",
            "tags": ["meta", "planning"],
            "requires_approval": False,
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {"type": "array", "items": {"type": "string"}, "description": "계획 단계 목록"},
                    "completed": {"type": "array", "items": {"type": "integer"}, "description": "완료된 단계 인덱스"},
                    "in_progress": {"type": "integer", "description": "진행 중인 단계 인덱스"},
                },
            },
        },
    }


# 외부 모듈 (eval/metrics.py 등) 이 built-in 도구 이름을 알 필요가 있을 때 import.
# _make_builtin_descriptors() 의 키 집합과 항상 동일하다.
BUILTIN_NAMES: frozenset[str] = frozenset(_make_builtin_descriptors().keys())
