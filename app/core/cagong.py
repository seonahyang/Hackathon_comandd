"""핵심기능 3 — '카공 가능' 판정.

카카오 로컬 API는 콘센트/와이파이/노트북 허용 정보를 주지 않는다.
그래서 2단계로 처리한다.

  1) 수집 시점: 브랜드/업종 휴리스틱으로 초기값 추정 (cagong_source='estimated')
  2) 운영 시점: 유저 제보 or 점주 인증 PATCH로 덮어쓰기 (source='user'/'owner')

발표 때 "데이터 어디서 났냐" 질문이 반드시 나오므로,
응답에 cagong_source를 같이 내려서 추정치임을 투명하게 표기한다.
"""

# 대형 프랜차이즈 = 좌석 많고 콘센트 있고 노트북 눈치 거의 없음
LAPTOP_FRIENDLY_BRANDS = [
    "스타벅스", "투썸", "할리스", "커피빈", "폴바셋", "파스쿠찌",
    "탐앤탐스", "엔제리너스", "카페베네", "드롭탑", "công", "블루보틀",
]
# 저가형 = 콘센트/와이파이는 있으나 좌석 회전 빠름
BUDGET_BRANDS = ["메가", "컴포즈", "빽다방", "더벤티", "이디야", "매머드", "감성커피", "百"]
# 노트북 비추 키워드 (오션뷰 관광카페 / 디저트 전문 / 베이커리 테이크아웃)
LAPTOP_UNFRIENDLY_KEYWORDS = ["로스터리", "베이커리", "디저트", "브런치", "루프탑", "오션뷰", "뷰맛집"]


def guess_cagong(name: str, category: str = "") -> dict:
    """이름/카테고리로 카공 환경 초기 추정."""
    text = f"{name} {category}"

    if any(b in text for b in LAPTOP_FRIENDLY_BRANDS):
        return {"laptop_ok": True, "has_power": True, "has_wifi": True,
                "quiet": True, "seat_count": 60, "cagong_source": "estimated"}

    if any(b in text for b in BUDGET_BRANDS):
        return {"laptop_ok": True, "has_power": True, "has_wifi": True,
                "quiet": False, "seat_count": 20, "cagong_source": "estimated"}

    if any(k in text for k in LAPTOP_UNFRIENDLY_KEYWORDS):
        return {"laptop_ok": False, "has_power": False, "has_wifi": True,
                "quiet": False, "seat_count": 25, "cagong_source": "estimated"}

    # 판단 불가 = 보수적으로 카공 필터에서 제외 (헛걸음 방지가 이 서비스의 핵심)
    return {"laptop_ok": False, "has_power": False, "has_wifi": True,
            "quiet": False, "seat_count": 20, "cagong_source": "estimated"}


def is_cagong_ok(cafe) -> bool:
    """'카공 가능' 퀵필터 통과 조건: 노트북 허용 + 콘센트 + 와이파이."""
    return bool(cafe.laptop_ok and cafe.has_power and cafe.has_wifi)


def cagong_score(cafe) -> int:
    """0~100. 카드 정렬/뱃지용."""
    score = 0
    score += 40 if cafe.laptop_ok else 0
    score += 25 if cafe.has_power else 0
    score += 15 if cafe.has_wifi else 0
    score += 10 if cafe.quiet else 0
    score += min(10, (cafe.seat_count or 0) // 6)
    if cafe.cagong_source in ("owner", "user"):
        score = min(100, score + 5)
    return min(100, score)
