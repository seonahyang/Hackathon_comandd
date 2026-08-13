"""핵심기능 3 — '카공 가능' 판정.

판정 근거는 단 하나, **실제로 다녀온 사람이 리뷰에서 누른 투표**다.

  카공 가능(cagong_yes) > 카공 불가(cagong_no)  →  '카공 가능' 매장
  그 외(동률 포함, 투표 0건)                     →  판정 보류 (필터에서 제외)

왜 브랜드명 추측을 버렸나
------------------------
이전 버전은 가게 이름에 '스타벅스'가 들어가면 콘센트·와이파이·노트북 허용을
전부 true 로 채웠다. 근거가 '브랜드가 그러니까'였다. 특히 판단 불가일 때조차
has_wifi=True 를 넣어서, 719건 전부가 '와이파이 있음'으로 저장돼 있었다.

이 서비스의 출발점은 "공공데이터에 카공 정보가 0건이다"라는 사실이다.
그런데 우리가 근거 없는 값을 채워 넣으면 그 주장 자체가 무너진다.
그래서 추측은 전부 제거했고, 모르는 값은 None(모름)으로 남긴다.
'모름'과 '아님'을 구분하는 것이 이 도메인에서 가장 중요한 설계 결정이다.

두 갈래의 정보
-------------
  · cagong_yes / cagong_no  — 리뷰 투표. **필터 판정에 쓰는 유일한 값**
  · laptop_ok / has_power / has_wifi / quiet / seat_count
      — 항목별 제보(POST /api/reports). 상세 화면 부가 정보이고 필터를 좌우하지 않는다.
        "왜 가능한가"를 설명하는 역할.
"""

# --- 매장 넓이 -----------------------------------------------------------
# 좌석수를 숫자로 물으면 아무도 정확히 모른다. 체감 3단계가 응답률이 훨씬 높고,
# 카공 목적에는 "자리 잡을 수 있나"만 알면 충분하다.
SIZE_CHOICES = ("small", "medium", "large")
SIZE_LABEL = {
    "small": "협소 (10석 내외)",
    "medium": "보통 (20~40석)",
    "large": "넓음 (40석 이상)",
}
SIZE_SHORT = {"small": "협소", "medium": "보통", "large": "넓음"}


def is_cagong_ok(cafe) -> bool:
    """'카공 가능' 퀵필터 통과 여부.

    단순 다수결이다. 동률이면 통과시키지 않는다. 추천을 믿고 갔다가 노트북을
    못 펴는 게 이 서비스에서 가장 치명적인 실패라, 애매하면 빼는 쪽이 맞다.
    """
    return (cafe.cagong_yes or 0) > (cafe.cagong_no or 0)


def decide_size(cafe) -> str | None:
    """넓이 투표의 최빈값. 투표가 없으면 None(모름)."""
    counts = {
        "small": cafe.size_small or 0,
        "medium": cafe.size_medium or 0,
        "large": cafe.size_large or 0,
    }
    if not any(counts.values()):
        return None
    top = max(counts.values())
    winners = [k for k, v in counts.items() if v == top]
    if len(winners) > 1:
        # 동률이면 가운데 값으로 수렴시킨다. 협소/넓음이 갈리면 '보통'이 가장
        # 덜 틀린 답이고, 유저를 헛걸음시킬 위험이 제일 작다.
        return "medium" if "medium" in winners else "medium"
    return winners[0]


def recount(db, cafe) -> dict:
    """리뷰 테이블에서 투표를 다시 세어 카페에 반영한다.

    증감(+1/-1)이 아니라 매번 전수 재집계하는 이유: 리뷰가 수정·삭제돼도
    카운터가 어긋나지 않는다. 719건 규모에서 카페당 리뷰는 수십 건이라
    비용도 무시할 만하다. 정합성 > 미세 최적화.
    """
    from sqlalchemy import func, select

    from ..models import Review

    yes, no = db.execute(
        select(
            func.count().filter(Review.cagong_vote.is_(True)),
            func.count().filter(Review.cagong_vote.is_(False)),
        ).where(Review.cafe_id == cafe.id)
    ).one()

    sizes = dict(
        db.execute(
            select(Review.size_vote, func.count())
            .where(Review.cafe_id == cafe.id, Review.size_vote.is_not(None))
            .group_by(Review.size_vote)
        ).all()
    )

    cafe.cagong_yes = int(yes or 0)
    cafe.cagong_no = int(no or 0)
    cafe.size_small = int(sizes.get("small", 0))
    cafe.size_medium = int(sizes.get("medium", 0))
    cafe.size_large = int(sizes.get("large", 0))

    cafe.cagong_ok = is_cagong_ok(cafe)
    cafe.size_label = decide_size(cafe)

    if cafe.cagong_yes or cafe.cagong_no:
        # 판정 근거가 리뷰 투표로 바뀌었음을 응답에 남긴다. 심사에서 "이 값
        # 어디서 났냐"는 질문에 이 필드 하나로 답이 된다.
        if cafe.cagong_source not in ("owner",):
            cafe.cagong_source = "review"

    return verdict(cafe)


def verdict(cafe) -> dict:
    """프론트가 뱃지·문구를 그리는 데 필요한 판정 결과 일체."""
    yes = cafe.cagong_yes or 0
    no = cafe.cagong_no or 0
    total = yes + no

    if total == 0:
        state, message = "unknown", "아직 카공 정보가 없어요. 첫 리뷰를 남겨주세요"
    elif yes > no:
        state, message = "ok", f"방문자 {total}명 중 {yes}명이 카공 가능이라고 했어요"
    elif no > yes:
        state, message = "no", f"방문자 {total}명 중 {no}명이 카공 어렵다고 했어요"
    else:
        state, message = "tie", f"의견이 {yes}:{no}로 갈려요. 한 명만 더 알려주세요"

    return {
        "state": state,                 # ok / no / tie / unknown
        "ok": state == "ok",
        "yes": yes,
        "no": no,
        "total_votes": total,
        "message": message,
        "size": cafe.size_label,
        "size_label": SIZE_SHORT.get(cafe.size_label or "", None),
        "size_votes": {
            "small": cafe.size_small or 0,
            "medium": cafe.size_medium or 0,
            "large": cafe.size_large or 0,
        },
        "source": cafe.cagong_source,
    }


def cagong_score(cafe) -> int:
    """0~100. 카드 정렬·뱃지용.

    리뷰 투표를 주(60점)로 두고, 항목별 제보는 보조(40점)로 얹는다.
    투표가 없으면 제보가 아무리 많아도 필터를 통과하지 못하지만,
    정렬에서는 정보가 많은 쪽이 위로 오는 게 사용자에게 유리하다.
    """
    yes = cafe.cagong_yes or 0
    no = cafe.cagong_no or 0
    total = yes + no

    score = 0
    if total:
        ratio = yes / total
        # 표본이 적을 때 100% 가 과대평가되지 않도록 표본 수로 눌러준다
        confidence = min(1.0, total / 3)
        score += int(60 * ratio * confidence)

    if cafe.laptop_ok:
        score += 12
    if cafe.has_power:
        score += 10
    if cafe.has_wifi:
        score += 6
    if cafe.quiet:
        score += 6
    if cafe.size_label == "large":
        score += 6
    elif cafe.size_label == "medium":
        score += 3

    if cafe.cagong_source == "owner":
        score += 5

    return max(0, min(100, score))
