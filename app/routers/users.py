from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Cafe, PointLedger, Review, User
from ..schemas import PointSummaryOut, UserCreate, UserOut

router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("", response_model=UserOut, deprecated=True,
             summary="[구버전] 유저 직접 생성 — 소셜 로그인은 GET /api/auth/me 사용")
def create_user(body: UserCreate, db: Session = Depends(get_db)):
    """소셜 로그인 도입 후로는 필요 없다. 테스트/시드용으로만 남겨둠."""
    if body.email:
        exist = db.scalar(select(User).where(User.email == body.email))
        if exist:
            return exist
    user = User(**body.model_dump())
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "유저를 찾을 수 없습니다")
    return user


@router.get("/{user_id}/points", response_model=PointSummaryOut, summary="적립금 잔액 + 내역")
def get_points(user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "유저를 찾을 수 없습니다")

    history = db.scalars(
        select(PointLedger).where(PointLedger.user_id == user_id)
        .order_by(PointLedger.id.desc()).limit(50)
    ).all()

    review_count = db.scalar(
        select(func.count(Review.id)).where(Review.user_id == user_id)
    ) or 0
    remote_count = db.scalar(
        select(func.count(Review.id))
        .join(Cafe, Cafe.id == Review.cafe_id)
        .where(Review.user_id == user_id, Cafe.is_remote.is_(True))
    ) or 0

    return {
        "user_id": user.id,
        "nickname": user.nickname,
        "point_balance": user.point_balance,
        "review_count": review_count,
        "remote_review_count": remote_count,
        "history": history,
    }
