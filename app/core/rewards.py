"""핵심기능 1 — 외곽지/소외 매장 리뷰 적립금 계산 엔진.

정책 (심사에서 물어보면 이 표 그대로 설명하면 됨):

  기본 적립           500P
  + 외곽지 보너스     핫스팟에서 15km↑ 2,000P / 10km↑ 1,500P / 5km↑ 800P
  + 소외매장 보너스   리뷰 0개 1,200P / 5개 미만 700P / 15개 미만 300P
  + 사진/상세 리뷰    300P (content 40자 이상)
  ------------------------------------------------
  1회 최대 상한       4,000P

핫플 도심 카페(리뷰 200개짜리 애월 한담 카페)는 500P,
표선 외곽 리뷰 0개 로컬 카페는 3,700P → 동선이 자연스럽게 분산됨.
"""

BASE_POINT = 500
MAX_POINT = 4000

REMOTE_TIERS = [(15.0, 2000, "핫스팟에서 15km 이상 떨어진 오지 매장"),
                (10.0, 1500, "핫스팟에서 10km 이상 떨어진 외곽 매장"),
                (5.0, 800, "핫스팟에서 5km 이상 떨어진 비도심 매장")]

SCARCE_TIERS = [(1, 1200, "리뷰가 아직 하나도 없는 첫 리뷰"),
                (5, 700, "리뷰 5개 미만 소외 매장"),
                (15, 300, "리뷰 15개 미만 저노출 매장")]

DETAIL_BONUS = 300
DETAIL_MIN_LEN = 40


def calc_reward(dist_to_hotspot_km: float, review_count: int, content_len: int = 0) -> dict:
    items: list[dict] = [{"label": "기본 리뷰 적립", "point": BASE_POINT}]

    for threshold, point, label in REMOTE_TIERS:
        if dist_to_hotspot_km >= threshold:
            items.append({"label": label, "point": point})
            break

    for threshold, point, label in SCARCE_TIERS:
        if review_count < threshold:
            items.append({"label": label, "point": point})
            break

    if content_len >= DETAIL_MIN_LEN:
        items.append({"label": "상세 리뷰 작성", "point": DETAIL_BONUS})

    raw_total = sum(i["point"] for i in items)
    total = min(raw_total, MAX_POINT)
    if raw_total > MAX_POINT:
        items.append({"label": f"1회 상한 적용 ({MAX_POINT:,}P)", "point": total - raw_total})

    bonus = total - BASE_POINT
    return {
        "total": total,
        "base": BASE_POINT,
        "bonus": bonus,
        "multiplier": round(total / BASE_POINT, 1),
        "items": items,
        "headline": _headline(bonus, total),
    }


def _headline(bonus: int, total: int) -> str:
    if bonus <= 0:
        return f"리뷰 적립 {total:,}P"
    return f"소외 상권 보너스 +{bonus:,}P! 총 {total:,}P 적립"


def preview_reward(cafe) -> dict:
    """리뷰 쓰기 전에 지도 마커/카드에 '여기 쓰면 3,700P' 미리보기용."""
    r = calc_reward(cafe.dist_to_hotspot_km or 0.0, cafe.review_count or 0)
    return {
        "point": r["total"],
        "multiplier": r["multiplier"],
        "is_boosted": r["total"] > BASE_POINT,
        "badge": f"{r['multiplier']}x 적립" if r["total"] > BASE_POINT else None,
    }
