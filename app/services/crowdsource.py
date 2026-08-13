"""핵심기능 3 — 카공 인프라 크라우드소싱 집계.

콘센트·와이파이·좌석 정보는 어떤 API도 주지 않는다. 유저가 채워야 한다.
문제는 "한 명이 잘못 제보하면 정보가 오염된다"는 것. 그래서 합의 규칙을 둔다.

  · 점주 제보(is_owner)      → 1건으로 즉시 확정, source='owner'
  · 일반 유저 제보           → 같은 항목 2건 이상 + 과반 동의 시 반영, source='user'
  · 합의 미달                → 기존 값 유지, pending 상태로 보관
  · 좌석수(seat_count)       → 중앙값 사용 (극단값 방어)

제보자에게는 적립금을 준다. 리뷰(500P~)보다 작지만, 지도를 열 때마다
한 항목씩 채우게 만드는 게 리텐션의 핵심이라 별도 보상 라인을 뒀다.
"""

import json
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Cafe, CagongReport, PointLedger, User

BOOL_FIELDS = ["laptop_ok", "has_power", "has_wifi", "quiet"]
INT_FIELDS = ["seat_count"]
ALL_FIELDS = BOOL_FIELDS + INT_FIELDS

MIN_VOTES = 2          # 일반 유저 제보 반영 최소 건수
REPORT_POINT = 100     # 제보 1건당 적립
FIRST_INFO_BONUS = 200 # 아직 아무도 안 채운 항목을 처음 채우면 추가

FIELD_LABEL = {
    "laptop_ok": "노트북 사용 가능",
    "has_power": "콘센트",
    "has_wifi": "와이파이",
    "quiet": "조용한 분위기",
    "seat_count": "좌석 수",
}


def _consensus_bool(votes: list[bool]) -> bool | None:
    """과반 동의. 동률이면 판단 보류(None)."""
    if not votes:
        return None
    yes = sum(1 for v in votes if v)
    no = len(votes) - yes
    if yes == no:
        return None
    return yes > no


def aggregate_field(db: Session, cafe: Cafe, field: str) -> dict:
    """한 항목의 제보들을 집계해 카페에 반영할지 결정."""
    reports = db.scalars(
        select(CagongReport).where(
            CagongReport.cafe_id == cafe.id, CagongReport.field == field
        )
    ).all()
    if not reports:
        return {"field": field, "applied": False, "reason": "no_reports"}

    owner = [r for r in reports if r.is_owner]
    if owner:
        latest = max(owner, key=lambda r: r.id)
        value = latest.value_int if field in INT_FIELDS else latest.value_bool
        setattr(cafe, field, value)
        cafe.cagong_source = "owner"
        for r in reports:
            r.applied = True
        return {"field": field, "applied": True, "value": value,
                "source": "owner", "votes": len(reports)}

    if field in INT_FIELDS:
        vals = [r.value_int for r in reports if r.value_int is not None]
        if len(vals) < MIN_VOTES:
            return {"field": field, "applied": False, "reason": "not_enough_votes",
                    "votes": len(vals), "need": MIN_VOTES}
        value = int(median(vals))
    else:
        vals = [r.value_bool for r in reports if r.value_bool is not None]
        if len(vals) < MIN_VOTES:
            return {"field": field, "applied": False, "reason": "not_enough_votes",
                    "votes": len(vals), "need": MIN_VOTES}
        value = _consensus_bool(vals)
        if value is None:
            return {"field": field, "applied": False, "reason": "tie", "votes": len(vals)}

    setattr(cafe, field, value)
    # 리뷰 투표(review)와 점주 인증(owner)이 제보(user)보다 우선한다.
    # 항목별 제보는 '왜 가능한지'를 설명하는 부가정보지 판정 근거가 아니다.
    if cafe.cagong_source == "unknown":
        cafe.cagong_source = "user"
    for r in reports:
        r.applied = True
    return {"field": field, "applied": True, "value": value,
            "source": "user", "votes": len(vals)}


def submit_report(
    db: Session, cafe: Cafe, user: User, field: str,
    value_bool: bool | None = None, value_int: int | None = None,
    is_owner: bool = False,
) -> dict:
    if field not in ALL_FIELDS:
        raise ValueError(f"지원하지 않는 항목: {field} (가능: {', '.join(ALL_FIELDS)})")

    prior = db.scalars(
        select(CagongReport).where(
            CagongReport.cafe_id == cafe.id, CagongReport.field == field
        )
    ).all()
    first_ever = len(prior) == 0
    already_mine = any(r.user_id == user.id for r in prior)

    report = CagongReport(
        cafe_id=cafe.id, user_id=user.id, field=field,
        value_bool=value_bool, value_int=value_int, is_owner=is_owner,
    )
    db.add(report)
    db.flush()

    result = aggregate_field(db, cafe, field)

    # 적립 (같은 항목 재제보는 포인트 없음 — 어뷰징 방지)
    earned = 0
    breakdown = []
    if not already_mine:
        earned = REPORT_POINT
        breakdown.append({"label": f"{FIELD_LABEL[field]} 정보 제보", "point": REPORT_POINT})
        if first_ever:
            earned += FIRST_INFO_BONUS
            breakdown.append({"label": "빈 정보를 처음 채운 보상", "point": FIRST_INFO_BONUS})

        user.point_balance = (user.point_balance or 0) + earned
        db.add(PointLedger(
            user_id=user.id, cafe_id=cafe.id, amount=earned,
            reason=f"{cafe.name} {FIELD_LABEL[field]} 제보",
            breakdown=json.dumps(breakdown, ensure_ascii=False),
            balance_after=user.point_balance,
        ))

    db.commit()

    if result["applied"]:
        msg = (f"제보 반영 완료 — {FIELD_LABEL[field]} 정보가 업데이트됐습니다"
               if result["source"] == "user"
               else f"점주 인증 정보로 {FIELD_LABEL[field]}가 확정됐습니다")
    elif result.get("reason") == "not_enough_votes":
        need = result["need"] - result["votes"]
        msg = f"제보 접수 — {need}명이 더 확인하면 반영됩니다"
    elif result.get("reason") == "tie":
        msg = "제보 접수 — 의견이 갈려 추가 확인이 필요합니다"
    else:
        msg = "제보 접수"

    return {
        "report_id": report.id,
        "applied": result["applied"],
        "status_message": msg,
        "aggregate": result,
        "earned_point": earned,
        "point_balance": user.point_balance,
        "breakdown": breakdown,
    }


def coverage(db: Session, cafe: Cafe) -> dict:
    """이 카페의 카공 정보가 얼마나 채워졌는지 (프론트 진행률 바 용)."""
    reports = db.scalars(
        select(CagongReport).where(CagongReport.cafe_id == cafe.id)
    ).all()
    by_field: dict[str, int] = {}
    for r in reports:
        by_field[r.field] = by_field.get(r.field, 0) + 1

    fields = []
    for f in ALL_FIELDS:
        n = by_field.get(f, 0)
        fields.append({
            "field": f, "label": FIELD_LABEL[f],
            "value": getattr(cafe, f), "report_count": n,
            "verified": cafe.cagong_source in ("owner", "user") and n > 0,
            "needs_report": n < MIN_VOTES and getattr(cafe, field) is None,
        })
    filled = sum(1 for f in fields if not f["needs_report"])
    return {
        "cafe_id": cafe.id, "source": cafe.cagong_source,
        "coverage_percent": round(filled / len(ALL_FIELDS) * 100),
        "fields": fields,
    }
