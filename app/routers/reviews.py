import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..core.rewards import calc_reward
from ..database import get_db
from ..models import Cafe, PointLedger, Review, User
from ..schemas import ReviewCreate, ReviewCreatedOut, ReviewOut

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


def _to_out(r: Review) -> dict:
    return {
        "id": r.id, "cafe_id": r.cafe_id, "user_id": r.user_id,
        "rating": r.rating, "content": r.content,
        "tags": [t for t in (r.tags or "").split(",") if t],
        "earned_point": r.earned_point, "created_at": r.created_at,
    }


@router.post("", response_model=ReviewCreatedOut,
             summary="리뷰 작성 + 적립금 지급 (핵심기능1) — 로그인 필요")
def create_review(
    body: ReviewCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """적립금이 걸린 행위라 로그인 필수. 작성자는 토큰에서 가져온다."""
    cafe = db.get(Cafe, body.cafe_id)
    if not cafe:
        raise HTTPException(404, "카페를 찾을 수 없습니다")

    dup = db.scalar(
        select(Review).where(Review.cafe_id == cafe.id, Review.user_id == user.id)
    )
    if dup:
        raise HTTPException(409, "이미 이 매장에 리뷰를 작성했습니다")

    # 적립금은 '리뷰가 달리기 직전'의 희소성 기준으로 계산해야 공정하다
    reward = calc_reward(
        dist_to_hotspot_km=cafe.dist_to_hotspot_km or 0.0,
        review_count=cafe.review_count or 0,
        content_len=len(body.content or ""),
    )

    review = Review(
        cafe_id=cafe.id, user_id=user.id, rating=body.rating,
        content=body.content, tags=",".join(body.tags),
        earned_point=reward["total"],
    )
    db.add(review)
    db.flush()

    total_score = (cafe.rating_avg or 0) * (cafe.review_count or 0) + body.rating
    cafe.review_count = (cafe.review_count or 0) + 1
    cafe.rating_avg = round(total_score / cafe.review_count, 2)

    user.point_balance = (user.point_balance or 0) + reward["total"]
    db.add(PointLedger(
        user_id=user.id, review_id=review.id, cafe_id=cafe.id,
        amount=reward["total"], reason=reward["headline"],
        breakdown=json.dumps(reward["items"], ensure_ascii=False),
        balance_after=user.point_balance,
    ))

    db.commit()
    db.refresh(review)

    return {
        "review": _to_out(review),
        "earned_point": reward["total"],
        "point_balance": user.point_balance,
        "headline": reward["headline"],
        "breakdown": reward["items"],
        "cafe_review_count": cafe.review_count,
        "cafe_rating_avg": cafe.rating_avg,
    }


@router.get("/cafe/{cafe_id}", response_model=list[ReviewOut], summary="카페별 리뷰 목록")
def list_by_cafe(cafe_id: int, db: Session = Depends(get_db), limit: int = 50):
    rows = db.scalars(
        select(Review).where(Review.cafe_id == cafe_id)
        .order_by(Review.id.desc()).limit(limit)
    ).all()
    return [_to_out(r) for r in rows]


@router.get("/user/{user_id}", response_model=list[ReviewOut], summary="유저별 리뷰 목록")
def list_by_user(user_id: int, db: Session = Depends(get_db), limit: int = 50):
    rows = db.scalars(
        select(Review).where(Review.user_id == user_id)
        .order_by(Review.id.desc()).limit(limit)
    ).all()
    return [_to_out(r) for r in rows]
