"""Adaptive AI Agent CLI 진입점."""

from __future__ import annotations

import argparse
import json
import logging
import readline  # noqa: F401  # pyright: ignore[reportUnusedImport] — side-effect: input() 줄 편집 활성화
from typing import Any

from rich.console import Console
from rich.panel import Panel

from adaptive_agent.agent.core import AgentCore
from adaptive_agent.agent.events import EventLogger
from adaptive_agent.agent.planner import Planner
from adaptive_agent.agent.session import Session
from adaptive_agent.config import Config
from adaptive_agent.llm.client import OllamaClient
from adaptive_agent.tools.registry import ToolRegistry

console = Console()
_event_logger: EventLogger | None = None


def _to_str(obj: Any) -> str:
    """객체를 읽기 좋은 문자열로 변환."""
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(obj)


def _print_detail(label: str, obj: Any, max_lines: int = 12) -> None:
    """입력/출력을 indented dim 블록으로 표시."""
    text = _to_str(obj)
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines}줄 더)"]
    indented = "\n".join(f"    {line}" for line in lines)
    console.print(f"    [dim]{label}:[/dim]")
    console.print(f"[dim]{indented}[/dim]")


_session_approved: set[str] = set()


def _on_approval(tool_name: str, input_data: dict[str, Any]) -> bool:
    """사용자 승인 콜백. 위험한 built-in 도구 실행 전에만 호출."""
    risk = input_data.get("_risk", "normal") if tool_name == "run_bash" else "normal"

    # warn-level run_bash 는 session 캐시 우회 — 매번 fresh approval.
    if tool_name in _session_approved and risk == "normal":
        return True

    detail = ""
    if tool_name == "run_bash":
        command = input_data.get("command", "")
        reason = input_data.get("_risk_reason")
        if risk == "warn":
            detail = f" → ⚠️ 주의({reason}): {command}"
        else:
            detail = f" → {command}"
    elif tool_name == "write_file":
        detail = f" → {input_data.get('path', '')}"
    elif tool_name == "edit_file":
        detail = f" → {input_data.get('path', '')}"
    elif tool_name == "web_fetch":
        detail = f" → {input_data.get('url', '')}"

    try:
        answer = input(f"  ⚠ '{tool_name}'{detail} 실행을 허용할까요? (y/n/a=항상허용): ").strip().lower()
    except EOFError:
        return False

    if answer in ("a", "always"):
        # warn-level 은 'a' 입력해도 캐시 안 함 — 매번 확인.
        if risk == "warn":
            console.print("    [yellow]⚠ warn-level 명령은 세션 캐시 제외 — 매번 확인합니다.[/yellow]")
            return True
        _session_approved.add(tool_name)
        console.print(f"    [dim]'{tool_name}' 이 세션 동안 자동 승인됩니다.[/dim]")
        return True
    return answer in ("y", "yes", "")


def _on_ask_user(question: str, choices: list[str] | None) -> str:
    """사용자에게 질문. Rich 포맷팅 적용."""
    console.print()
    # multiline question 도 모든 줄을 들여쓰기 통일
    indented_question = "\n".join(f"  [bold]{line}[/bold]" for line in question.splitlines())
    console.print(indented_question)
    if choices:
        for i, choice in enumerate(choices, 1):
            console.print(f"    [cyan]{i}.[/cyan] {choice}")
    try:
        answer = input("  > ").strip()
    except EOFError:
        answer = ""
    if choices and answer.isdigit() and 1 <= int(answer) <= len(choices):
        answer = choices[int(answer) - 1]
    return answer or "(응답 없음)"


def _on_status(event: str, data: dict[str, Any]) -> None:
    """에이전트 실행 중 상태 표시 + 이벤트 로깅 콜백."""
    if _event_logger:
        _event_logger.emit(event, data)

    match event:
        case "thinking":
            reasoning = data.get("reasoning", "")
            console.print(f"  [dim]💭 {reasoning}[/dim]")
        case "generating_code":
            desc = data.get("description", "?")
            console.print(f"  [bold cyan]📝 코드 생성 중: {desc}[/bold cyan]")
        case "build_progress":
            msg = data.get("message", "")
            console.print(f"    [dim]⏳ {msg}[/dim]")
        case "creating_tool":
            name = data.get("tool_name", "?")
            console.print(f"  [bold cyan]🔧 도구 생성 중: {name}[/bold cyan]")
            inp = data.get("input")
            if inp:
                _print_detail("입력", inp)
        case "using_tool":
            name = data.get("tool_name", "?")
            console.print(f"  [bold yellow]▶ 도구 실행 중: {name}[/bold yellow]")
            inp = data.get("input")
            if inp:
                _print_detail("입력", inp)
        case "repairing_tool":
            name = data.get("tool_name", "?")
            attempt = data.get("attempt", "?")
            max_a = data.get("max_attempts", "?")
            console.print(f"  [bold magenta]🔄 도구 수정 중: {name} (시도 {attempt}/{max_a})[/bold magenta]")
        case "plan_updated":
            action = data.get("action", "updated")
            steps = data.get("steps", [])
            if action == "created" and steps:
                console.print(f"  [bold blue]📋 계획 수립 ({len(steps)}단계)[/bold blue]")
        case "tool_result":
            name = data.get("tool_name", "?")
            if data.get("success"):
                console.print(f"  [green]✓ {name} 완료[/green]")
                out = data.get("output")
                if out:
                    _print_detail("출력", out)
            else:
                err = data.get("error", "")
                if err and len(err) > 200:
                    console.print(f"  [red]✗ {name} 실패:[/red]")
                    console.print(f"  [dim]{err}[/dim]")
                else:
                    console.print(f"  [red]✗ {name} 실패: {err or '알 수 없는 오류'}[/red]")
        case "suggested_file_detected":
            path = data.get("path", "?")
            console.print(f"  [dim]📄 파일 출력 제안 감지: {path}[/dim]")
        case _:
            pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Adaptive AI Agent CLI")
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="상세 로그 출력 (LLM raw 응답 등)",
    )
    return parser.parse_args()


def main() -> None:
    global _event_logger

    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="  [%(name)s] %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.INFO if args.verbose else logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    config = Config.load()
    client = OllamaClient(config)
    session = Session()
    registry = ToolRegistry(config.tools_dir)
    planner = Planner(client)

    base_dir = config.tools_dir.parent
    _event_logger = EventLogger(base_dir)

    agent = AgentCore(
        planner=planner,
        session=session,
        registry=registry,
        client=client,
        max_steps=config.execution.max_steps,
        max_repair_attempts=config.execution.max_repair_attempts,
        status_callback=_on_status,
        approval_callback=_on_approval,
        ask_user_callback=_on_ask_user,
    )

    console.print(Panel(
        "[bold]Adaptive AI Agent[/bold]\n"
        f"모델: {config.llm.model} | native tools: {'on' if config.llm.enable_native_tools else 'off'} | "
        f"세션: {_event_logger.session_id} | 종료: Ctrl+C 또는 'exit'",
        border_style="blue",
    ))

    try:
        _repl(agent, session, registry, client, config)
    except KeyboardInterrupt:
        console.print("\n종료합니다.")
    finally:
        client.close()


_PROMPT = "> "


def _repl(
    agent: AgentCore,
    session: Session,
    registry: ToolRegistry,
    client: OllamaClient,
    config: Config,
) -> None:
    while True:
        try:
            user_input = input(_PROMPT).strip()
        except EOFError:
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            break

        if user_input.lower() == "/tools":
            _show_tools(registry)
            continue

        console.print()
        response = agent.handle_user_input(user_input)

        if response:
            console.print(f"\n{response}\n")

        _offer_save(session, registry, config)


def _show_tools(registry: ToolRegistry) -> None:
    descs, notices = registry.get_tool_descriptions()
    if not descs:
        console.print("[dim]등록된 도구가 없습니다.[/dim]")
        return
    for t in descs:
        tags = ", ".join(t.get("tags", []))
        console.print(f"  [bold]{t['name']}[/bold]: {t['description']}")
        if tags:
            console.print(f"    태그: {tags}")
    for n in notices:
        console.print(f"  [dim]{n}[/dim]")


def _offer_save(session: Session, registry: ToolRegistry, config: Config) -> None:
    """성공적으로 실행된 도구만 저장 여부를 물음."""
    from adaptive_agent.tools.persistence import ToolPersistence, extract_manifest_from_code, infer_input_schema, parse_docstring_args

    persistence = ToolPersistence(config.tools_dir)

    for name, info in list(session.temp_tools.items()):
        if info.get("offered_save"):
            continue

        if name not in session.successful_tools:
            info["offered_save"] = True
            continue

        desc = info.get("manifest", {}).get("description", "")
        console.print(f"  도구 '{name}'을 저장하면 다음 세션에서도 사용할 수 있습니다.")
        if desc:
            console.print(f"  설명: {desc}")

        try:
            answer = input("  저장할까요? (y/이름 입력/n): ").strip()
        except EOFError:
            answer = "n"

        if answer.lower() in ("n", "no", ""):
            info["offered_save"] = True
            continue
        elif answer.lower() in ("y", "yes"):
            save_name = name
        else:
            save_name = answer

        if persistence.exists(save_name):
            try:
                overwrite = input(f"  ⚠ '{save_name}' 도구가 이미 존재합니다. 덮어쓸까요? (y/n): ").strip().lower()
            except EOFError:
                overwrite = "n"
            if overwrite not in ("y", "yes"):
                info["offered_save"] = True
                continue

        # temp_tools manifest를 기본으로, 코드에서 docstring 보강
        base_manifest: dict[str, Any] = info.get("manifest", {})
        code_manifest = extract_manifest_from_code(save_name, info["code"], desc)
        manifest = {**code_manifest, **{k: v for k, v in base_manifest.items() if v}}
        last_input = registry.get_last_input(save_name) or registry.get_last_input(name)
        last_output = registry.get_last_output(name)

        # input_schema 자동 추론: last_success_input 타입 + docstring Args description
        arg_descs = parse_docstring_args(info["code"])
        schema = infer_input_schema(last_input, arg_descriptions=arg_descs)
        if schema is not None:
            manifest["input_schema"] = schema
        persistence.save(
            save_name, info["code"], manifest,
            last_success_input=last_input,
            last_success_output=last_output,
        )
        registry.register_persistent_tool(save_name, info["code"], manifest)
        console.print(f"  [green]도구 '{save_name}' 저장 완료.[/green]")
        info["offered_save"] = True


if __name__ == "__main__":
    main()
