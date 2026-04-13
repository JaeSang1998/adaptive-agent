"""None 값 처리 누락: 일부 row 의 numeric 필드가 None 일 때 crash."""


def run(input: dict) -> dict:
    rows = input.get("rows", [])
    total = 0
    # BUG: row['value'] 가 None 일 수 있는데 그냥 더함 -> TypeError
    for row in rows:
        total += row["value"]
    return {"total": total, "count": len(rows)}
