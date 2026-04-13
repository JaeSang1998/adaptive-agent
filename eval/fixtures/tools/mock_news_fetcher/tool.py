"""Mock 뉴스 수집 도구. 고정된 5개 뉴스 반환 (eval용)."""


def run(input: dict) -> dict:
    max_headlines = input.get("max_headlines", 5)
    # 항상 5개만 반환 (RSS 피드 제한 시뮬레이션)
    headlines = [
        "경제성장률 전망치 하향 조정 - 한국은행",
        "AI 스타트업 투자 역대 최고 기록 - 테크뉴스",
        "기후변화 대응 신재생에너지 확대 방안 발표 - 환경부",
        "프로야구 시즌 개막 앞두고 선수단 정비 마무리 - 스포츠투데이",
        "글로벌 반도체 공급망 재편 가속화 - 전자신문",
    ]
    return {"result": headlines[:max_headlines]}
