"""평균 계산에서 빈 리스트 처리 누락 -> ZeroDivisionError."""


def run(input: dict) -> dict:
    nums = input.get("nums", [])
    # BUG: nums 가 빈 리스트면 len(nums) 가 0 -> crash
    avg = sum(nums) / len(nums)
    return {"avg": avg, "n": len(nums)}
