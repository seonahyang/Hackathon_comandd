import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..core import cagong as cg
from ..core.rewards import calc_reward
from ..services import ai
from ..services import crowdsource as cs
from ..database import get_db
from ..models import Cafe, PointLedger, Review, User
from ..schemas import ReviewCreate, ReviewCreatedOut, ReviewOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


def _to_out(r: Review) -> dict:
    return {
        "id": r.id, "cafe_id": r.cafe_id, "user_id": r.user_id,
        "rating": r.rating, "content": r.content,
        "tags": [t for t in (r.tags or "").split(",") if t],
        "cagong_vote": r.cagong_vote, "size_vote": r.size_vote,
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

    # 적립금은 지역 활성도 지수로만 결정한다. 거리·리뷰수 기준은 버렸다
    # (이유는 core/rewards.py 문서 참고).
    reward = calc_reward(
        region_index=cafe.region_index,
        content_len=len(body.content or ""),
    )

    # ── AI 분류 ─────────────────────────────────────────────────────
    # 사용자가 버튼을 안 눌러도 본문에 정보가 들어있는 경우가 많다.
    #   "콘센트가 자리마다 있고 조용했어요" → has_power/quiet/cagong
    # 공공데이터에 0건인 항목을 채우는 유일한 경로라, 여기서 놓치면 데이터가 안 는다.
    #
    # 사용자가 직접 고른 값이 항상 우선한다. AI 는 비어 있는 칸만 채운다.
    # 사람의 명시적 선택을 기계 추정으로 덮어쓰면 신뢰가 무너진다.
    cagong_vote = body.cagong_vote
    size_vote = body.size_vote
    ai_result = None

    if ai.is_configured() and (cagong_vote is None or size_vote is None):
        try:
            ai_result = ai.classify_review(body.content)
            if cagong_vote is None:
                cagong_vote = ai_result.get("cagong")
            if size_vote is None:
                size_vote = ai_result.get("size")
        except (ai.AIUnavailable, ai.AIRateLimited) as e:
            # AI 가 죽어도, 한도를 넘겨도 리뷰 등록은 성공해야 한다.
            logger.warning("리뷰 AI 분류 건너뜀: %s", e)

    review = Review(
        cafe_id=cafe.id, user_id=user.id, rating=body.rating,
        content=body.content, tags=",".join(body.tags),
        cagong_vote=cagong_vote, size_vote=size_vote,
        earned_point=reward["total"],
    )
    db.add(review)
    db.flush()

    # 카공 판정 재집계 — 이 리뷰의 투표가 매장 판정을 바꿀 수 있다.
    # flush() 뒤에 불러야 방금 넣은 리뷰가 집계에 포함된다.
    verdict = cg.recount(db, cafe)

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

    # ── AI 가 읽어낸 항목별 정보를 제보로 쌓는다 ──────────────────────
    # 표는 AI 것이 아니라 '이 리뷰를 쓴 사람' 것이다. AI 는 사람이 쓴 문장을
    # 구조화했을 뿐이라, 기존 2인 합의 규칙이 그대로 적용된다.
    # 적립금은 주지 않는다 — 리뷰 적립을 이미 받았으므로 이중 지급이 된다.
    if ai_result:
        for field in ("has_power", "has_wifi", "quiet"):
            value = ai_result.get(field)
            if value is None:
                continue
            try:
                cs.submit_report(db, cafe=cafe, user=user, field=field,
                                 value_bool=bool(value), award_point=False)
            except Exception:  # noqa: BLE001
                logger.warning("AI 제보 적재 실패: %s", field, exc_info=True)

    return {
        "review": _to_out(review),
        "earned_point": reward["total"],
        "point_balance": user.point_balance,
        "headline": reward["headline"],
        "breakdown": reward["items"],
        "cafe_review_count": cafe.review_count,
        "cafe_rating_avg": cafe.rating_avg,
        # 내 투표로 이 매장 판정이 어떻게 바뀌었는지 바로 돌려준다.
        # "당신의 리뷰로 이 카페가 카공 가능 매장이 됐어요" 를 화면에 띄우기 위함.
        "cagong_verdict": verdict,
        # AI 가 본문에서 무엇을 읽어냈는지. 화면에 "리뷰에서 콘센트 정보를
        # 자동으로 찾았어요" 를 띄우는 재료이자, 심사에서 근거 검증이
        # 동작한다는 증거(dropped)다.
        "ai": ai_result,
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
