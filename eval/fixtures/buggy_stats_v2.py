"""통계 도구 (의도적 KeyError 유발용)."""

def run(input: dict) -> dict:
    data = input["numbers"]  # 정상
    total = sum(data)
    avg = total / len(data)
    # 버그: 'maximum' 대신 'max_val' 키로 접근
    result = {"total": total, "average": avg}
    result["max_value"] = result["max_val"]  # KeyError!
    return result
