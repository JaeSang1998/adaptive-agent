"""시나리오 정의 + YAML 로더."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class VerifyCheck:
    type: str
    params: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())


@dataclass
class Turn:
    """Multi-turn 시나리오의 한 턴."""

    user: str
    expect: list[VerifyCheck] = field(default_factory=lambda: list[VerifyCheck]())
    ask_user_responses: list[str] = field(default_factory=lambda: list[str]())


@dataclass
class Scenario:
    id: str
    name: str
    category: str
    inputs: list[str]
    description: str = ""
    setup_files: list[dict[str, str]] = field(default_factory=lambda: list[dict[str, str]]())
    setup_tools: list[dict[str, str]] = field(default_factory=lambda: list[dict[str, str]]())
    ask_user_responses: list[str] = field(default_factory=lambda: list[str]())
    config_overrides: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())
    verify: list[VerifyCheck] = field(default_factory=lambda: list[VerifyCheck]())
    turns: list[Turn] = field(default_factory=lambda: list[Turn]())
    timeout_seconds: int = 120
    split: str = "train"
    difficulty: str = "medium"


def _parse_verify_list(raw_verify: list[dict[str, Any]] | None) -> list[VerifyCheck]:
    out: list[VerifyCheck] = []
    for v in raw_verify or []:
        v_copy = dict(v)
        vtype = v_copy.pop("type")
        out.append(VerifyCheck(type=vtype, params=v_copy))
    return out


def load_scenario(path: Path) -> Scenario:
    """YAML 파일에서 시나리오 로드."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    verify_checks = _parse_verify_list(raw.get("verify"))

    turns: list[Turn] = []
    for t in raw.get("turns", []) or []:
        turns.append(Turn(
            user=str(t.get("user", "")),
            expect=_parse_verify_list(t.get("expect")),
            ask_user_responses=t.get("ask_user_responses", []) or [],
        ))

    return Scenario(
        id=raw["id"],
        name=raw["name"],
        category=raw.get("category", "general"),
        description=raw.get("description", ""),
        inputs=raw.get("inputs", []),
        setup_files=raw.get("setup_files", []),
        setup_tools=raw.get("setup_tools", []),
        ask_user_responses=raw.get("ask_user_responses", []),
        config_overrides=raw.get("config_overrides", {}),
        verify=verify_checks,
        turns=turns,
        timeout_seconds=raw.get("timeout_seconds", 120),
        split=raw.get("split", "train"),
        difficulty=raw.get("difficulty", "medium"),
    )


def load_all_scenarios(directory: Path) -> list[Scenario]:
    """디렉토리의 모든 YAML 시나리오 로드."""
    scenarios: list[Scenario] = []
    for path in sorted(directory.glob("*.yaml")):
        scenarios.append(load_scenario(path))
    return scenarios
