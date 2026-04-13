"""경량 eval CLI 진입점.

Usage:
    python -m eval                              # 전체 시나리오
    python -m eval --category data_pipeline     # 카테고리 필터
    python -m eval --filter csv_analysis        # ID 필터
    python -m eval --output report.json         # JSON 리포트 저장
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

# adaptive-agent/src를 import path에 추가
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from eval.runner import EvalRunner


def _deep_set(target: dict[str, Any], dotted_key: str, value: Any) -> None:
    current = target
    keys = dotted_key.split(".")
    for key in keys[:-1]:
        current = current.setdefault(key, {})
    current[keys[-1]] = value


def _parse_overrides(items: list[str] | None) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"override must be KEY=VALUE: {item}")
        key, raw = item.split("=", 1)
        _deep_set(overrides, key, yaml.safe_load(raw))
    return overrides


def main() -> None:
    parser = argparse.ArgumentParser(description="Adaptive Agent Evaluation")
    parser.add_argument(
        "--scenarios", default=str(Path(__file__).parent / "scenarios"),
        help="시나리오 디렉토리 경로",
    )
    parser.add_argument("--filter", nargs="*", help="실행할 시나리오 ID")
    parser.add_argument("--category", nargs="*", help="실행할 카테고리")
    parser.add_argument(
        "--split",
        choices=["train", "test", "all"],
        default="all",
        help="실행할 split (train / test / all). 기본: all",
    )
    parser.add_argument("--output", help="JSON 리포트 출력 경로")
    parser.add_argument(
        "--timeout-multiplier",
        type=float,
        default=1.0,
        help="시나리오 timeout_seconds에 곱할 배수",
    )
    parser.add_argument(
        "--override",
        action="append",
        default=[],
        help="모든 시나리오에 적용할 config override (예: llm.code_timeout_seconds=120)",
    )
    parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="시나리오 workdir를 항상 보존",
    )
    parser.add_argument(
        "--debug-dir",
        help="workdir를 생성할 디렉토리",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="worker.log에 DEBUG 레벨까지 기록",
    )
    args = parser.parse_args()

    runner = EvalRunner(
        Path(args.scenarios),
        timeout_multiplier=args.timeout_multiplier,
        keep_workdir=args.keep_workdir,
        runtime_overrides=_parse_overrides(args.override),
        log_level="DEBUG" if args.verbose else "INFO",
        debug_dir=Path(args.debug_dir) if args.debug_dir else None,
    )

    if not runner.scenarios:
        print(f"No scenarios found in {args.scenarios}")
        sys.exit(1)

    print(f"Found {len(runner.scenarios)} scenario(s)")
    report = runner.run_all(
        filter_ids=args.filter,
        filter_categories=args.category,
        filter_split=args.split,
    )
    report.print_summary()

    if args.output:
        report.save_json(args.output)
        print(f"Report saved to {args.output}")


if __name__ == "__main__":
    main()
