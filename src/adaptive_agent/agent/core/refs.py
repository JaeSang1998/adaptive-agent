"""$ref dehydration: planner 가 `{"$ref": "<path>"}` 로 데이터 참조한 것을
session.observations 에서 lookup 해 실제 값으로 치환.

JSON / CSV 는 자동 parse — builder 가 즉시 구조화 데이터로 받는다.
실패 시 errors 에 누적되며 원본 ref dict 는 그대로 유지 (caller 가 처리).
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, cast

from adaptive_agent.agent.session import Session


def _maybe_parse_structured(ref_path: str, raw: Any) -> Any:
    """$ref 로 가져온 raw output 을 path 확장자 기반으로 parse.

    JSON/CSV 는 builder 가 즉시 구조화 데이터로 받게 한다. 파싱 실패 시 raw string 반환.
    """
    if not isinstance(raw, str):
        return raw
    suffix = Path(ref_path).suffix.lower()
    try:
        if suffix == ".json":
            return json.loads(raw)
        if suffix == ".csv":
            return list(csv.DictReader(io.StringIO(raw)))
    except (json.JSONDecodeError, csv.Error):
        return raw
    return raw


def resolve_refs(
    value: Any,
    session: Session,
    errors: list[str],
) -> Any:
    """input_data 안의 {"$ref": "<path>"} 를 session.observations 에서 lookup 해 dehydrate.

    재귀적으로 dict/list 안쪽까지 처리. JSON/CSV 는 자동 parse.
    Resolve 실패 시 errors 에 누적, 원본 ref dict 그대로 둠 (caller 가 에러 처리).
    """
    if isinstance(value, dict):
        value_dict = cast(dict[str, Any], value)
        if set(value_dict.keys()) == {"$ref"} and isinstance(value_dict["$ref"], str):
            ref_path: str = value_dict["$ref"]
            obs = session.get_observation_by_path(ref_path)
            if obs is None:
                errors.append(
                    f"$ref 를 찾을 수 없습니다: '{ref_path}'. "
                    f"먼저 read_file로 해당 파일을 읽으세요."
                )
                return value_dict
            return _maybe_parse_structured(ref_path, obs.get("output", ""))
        return {k: resolve_refs(v, session, errors) for k, v in value_dict.items()}
    if isinstance(value, list):
        value_list = cast(list[Any], value)
        return [resolve_refs(item, session, errors) for item in value_list]
    return value
