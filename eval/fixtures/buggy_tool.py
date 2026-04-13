"""Statistical analysis tool with intentional bugs (run(input) 계약)."""

import math
from collections import Counter


def run(input: dict) -> dict:
    """Calculate basic statistics.

    Args:
        data: list of numbers
    """
    data = input.get("data", [])
    if not data:
        return {"result": {}}

    n = len(data)

    # Bug 1: sum via manual loop (works but non-idiomatic)
    total = 0
    for val in data:
        total += val
    mean = total / n

    # Bug 2: median calculation doesn't sort first
    sorted_data = data  # Should be sorted(data)
    if n % 2 == 0:
        median = (sorted_data[n // 2 - 1] + sorted_data[n // 2]) / 2
    else:
        median = sorted_data[n // 2]

    # Bug 3: mode uses most_common(2)[0] — wrong index semantics
    counts = Counter(data)
    mode = counts.most_common(2)[0][0]

    # Bug 4: population variance instead of sample (divides by n not n-1)
    variance = sum((x - mean) ** 2 for x in data) / n
    std_dev = math.sqrt(variance)

    stats = {
        "mean": mean,
        "median": median,
        "mode": mode,
        "std_dev": round(std_dev, 4),
        "min": min(data),
        "max": max(data),
        "sum": total,
        "count": n,
    }
    return {"result": stats}
