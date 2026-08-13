"""발표/PPT용 집계 API — 유경·소은님이 그래프 그릴 때 그대로 쓰면 됨."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.rewards import calc_reward
from ..database import get_db
from ..models import Cafe, Review

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/dispersion", summary="지역별 분산 지표 (오버투어리즘 해소 근거)")
def dispersion(db: Session = Depends(get_db)):
    rows = db.execute(
        select(
            Cafe.district,
            func.count(Cafe.id),
            func.sum(Cafe.review_count),
            func.avg(Cafe.dist_to_hotspot_km),
        ).group_by(Cafe.district)
    ).all()

    out = []
    for district, cafe_cnt, review_sum, avg_dist in rows:
        avg_reward = calc_reward(float(avg_dist or 0),
                                 int((review_sum or 0) / max(cafe_cnt, 1)))["total"]
        out.append({
            "district": district or "미분류",
            "cafe_count": cafe_cnt,
            "review_count": int(review_sum or 0),
            "avg_dist_to_hotspot_km": round(float(avg_dist or 0), 2),
            "avg_reward_point": avg_reward,
        })
    out.sort(key=lambda x: -x["avg_reward_point"])
    return {"regions": out}


@router.get("/summary", summary="전체 요약 (대시보드 상단 카드)")
def summary(db: Session = Depends(get_db)):
    total = db.scalar(select(func.count(Cafe.id))) or 0
    remote = db.scalar(select(func.count(Cafe.id)).where(Cafe.is_remote.is_(True))) or 0
    # 카공 가능 = 리뷰 투표에서 '가능'이 '불가'보다 많은 매장
    cagong = db.scalar(select(func.count(Cafe.id)).where(Cafe.cagong_ok.is_(True))) or 0
    cagong_voted = db.scalar(
        select(func.count(Cafe.id)).where((Cafe.cagong_yes + Cafe.cagong_no) > 0)
    ) or 0
    reviews = db.scalar(select(func.count(Review.id))) or 0
    remote_reviews = db.scalar(
        select(func.count(Review.id)).join(Cafe, Cafe.id == Review.cafe_id)
        .where(Cafe.is_remote.is_(True))
    ) or 0
    points = db.scalar(select(func.sum(Review.earned_point))) or 0

    return {
        "total_cafes": total,
        "remote_cafes": remote,
        "remote_ratio": round(remote / total * 100, 1) if total else 0,
        "cagong_cafes": cagong,
        "cagong_voted_cafes": cagong_voted,   # 투표가 1건이라도 달린 매장
        "total_reviews": reviews,
        "remote_reviews": remote_reviews,
        "remote_review_ratio": round(remote_reviews / reviews * 100, 1) if reviews else 0,
        "total_points_issued": int(points),
    }


@router.get("/underrated", summary="소외 매장 TOP N (적립금 높은 순)")
def underrated(db: Session = Depends(get_db), limit: int = 10):
    rows = db.scalars(
        select(Cafe).where(Cafe.is_remote.is_(True)).order_by(Cafe.review_count.asc()).limit(limit * 3)
    ).all()
    items = [{
        "id": c.id, "name": c.name, "district": c.district,
        "review_count": c.review_count,
        "dist_to_hotspot_km": c.dist_to_hotspot_km,
        "reward_point": calc_reward(c.dist_to_hotspot_km, c.review_count)["total"],
        "lat": c.lat, "lng": c.lng,
    } for c in rows]
    items.sort(key=lambda x: -x["reward_point"])
    return {"items": items[:limit]}
