"""설정 로딩: config.yaml + 환경변수 + CLI 인자."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_DEFAULT_CONFIG_PATH = Path.home() / ".adaptive-agent" / "config.yaml"
_DEFAULT_TOOLS_DIR = Path.home() / ".adaptive-agent" / "tools"


@dataclass(frozen=True, slots=True)
class LLMConfig:
    base_url: str = "http://localhost:11434"
    model: str = "gemma4:26b"
    temperature: float = 0.0
    seed: int = 42
    max_tokens: int = 8192
    # gemma4:26b 256K context 활용. 권장 메모리 ≥ 48GB.
    num_ctx: int = 131072
    # gemma4:26b 의 native tool calling 이 multi-turn + 긴 tool result + save action
    # 패턴에서 empty content 반환하는 model-side 제약 관측. 다른 모델 또는 후속 버전
    # 에서 해결되면 True 로 복귀. CLI override / env 로 즉시 enable 가능.
    enable_native_tools: bool = False
    request_timeout_seconds: float = 120.0
    planner_max_tokens: int = 4096
    planner_timeout_seconds: float = 90.0
    code_max_tokens: int = 8192
    code_timeout_seconds: float = 120.0
    repair_max_tokens: int = 8192
    repair_timeout_seconds: float = 120.0


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    max_steps: int = 15
    max_repair_attempts: int = 3


@dataclass(frozen=True, slots=True)
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    tools_dir: Path = _DEFAULT_TOOLS_DIR

    @staticmethod
    def load(
        config_path: Path | None = None,
        *,
        overrides: dict[str, Any] | None = None,
    ) -> Config:
        """config.yaml → 환경변수 → overrides 순으로 머지하여 Config 반환."""
        raw: dict[str, Any] = {}

        # 1. YAML 파일
        path = config_path or _DEFAULT_CONFIG_PATH
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        # 2. 환경변수 오버라이드
        if (base_url := os.environ.get("AGENT_LLM_BASE_URL")):
            raw.setdefault("llm", {})["base_url"] = base_url
        if (model := os.environ.get("AGENT_LLM_MODEL")):
            raw.setdefault("llm", {})["model"] = model
        if (enable_native_tools := os.environ.get("AGENT_LLM_ENABLE_NATIVE_TOOLS")):
            raw.setdefault("llm", {})["enable_native_tools"] = enable_native_tools.lower() in (
                "1", "true", "yes", "on",
            )

        # 3. CLI 오버라이드
        if overrides:
            for key, value in overrides.items():
                _deep_set(raw, key, value)

        return _build_config(raw)


def _deep_set(d: dict[str, Any], dotted_key: str, value: Any) -> None:
    """'llm.model' 같은 dotted key를 nested dict에 설정."""
    keys = dotted_key.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _build_config(raw: dict[str, Any]) -> Config:
    llm_raw = raw.get("llm", {})
    exec_raw = raw.get("execution", {})
    tools_dir = raw.get("tools_dir")

    # 기본값 인스턴스
    llm_defaults = LLMConfig()
    exec_defaults = ExecutionConfig()

    return Config(
        llm=LLMConfig(
            base_url=str(llm_raw.get("base_url", llm_defaults.base_url)),
            model=str(llm_raw.get("model", llm_defaults.model)),
            temperature=float(llm_raw.get("temperature", llm_defaults.temperature)),
            seed=int(llm_raw.get("seed", llm_defaults.seed)),
            max_tokens=int(llm_raw.get("max_tokens", llm_defaults.max_tokens)),
            num_ctx=int(llm_raw.get("num_ctx", llm_defaults.num_ctx)),
            enable_native_tools=bool(llm_raw.get(
                "enable_native_tools", llm_defaults.enable_native_tools,
            )),
            request_timeout_seconds=float(llm_raw.get(
                "request_timeout_seconds", llm_defaults.request_timeout_seconds,
            )),
            planner_max_tokens=int(llm_raw.get(
                "planner_max_tokens", llm_defaults.planner_max_tokens,
            )),
            planner_timeout_seconds=float(llm_raw.get(
                "planner_timeout_seconds", llm_defaults.planner_timeout_seconds,
            )),
            code_max_tokens=int(llm_raw.get(
                "code_max_tokens", llm_defaults.code_max_tokens,
            )),
            code_timeout_seconds=float(llm_raw.get(
                "code_timeout_seconds", llm_defaults.code_timeout_seconds,
            )),
            repair_max_tokens=int(llm_raw.get(
                "repair_max_tokens", llm_defaults.repair_max_tokens,
            )),
            repair_timeout_seconds=float(llm_raw.get(
                "repair_timeout_seconds", llm_defaults.repair_timeout_seconds,
            )),
        ),
        execution=ExecutionConfig(
            max_steps=int(exec_raw.get("max_steps", exec_defaults.max_steps)),
            max_repair_attempts=int(exec_raw.get("max_repair_attempts", exec_defaults.max_repair_attempts)),
        ),
        tools_dir=Path(tools_dir).expanduser() if tools_dir else _DEFAULT_TOOLS_DIR,
    )
