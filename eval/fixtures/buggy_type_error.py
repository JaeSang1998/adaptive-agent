"""문자열 처리 도구 (의도적 TypeError 유발용)."""

def run(input: dict) -> dict:
    text = input["text"]
    words = text.split()
    # 버그: len()에 정수를 더하려고 함
    count = len(words) + " words"  # TypeError: unsupported operand
    return {"word_count": count}
