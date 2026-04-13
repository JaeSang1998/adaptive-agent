"""top_n 계산 버그: 한 개 적게 반환됨 (off-by-one)."""


def run(input: dict) -> dict:
    items = input.get("items", [])
    n = int(input.get("n", 3))
    sorted_items = sorted(items, key=lambda x: x.get("score", 0), reverse=True)
    # BUG: n-1 이 맞는데 n-2 로 slice
    top = sorted_items[: n - 2]
    return {"top": top, "count": len(top)}
