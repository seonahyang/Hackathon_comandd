"""AI 기능 시연·검증용 엔드포인트.

Swagger(/docs)에서 바로 눌러볼 수 있게 열어둔다. 발표 때 화면 전환 없이
"이 문장을 넣으면 이렇게 구조화됩니다"를 보여주는 용도다.

실제 서비스 경로는 여기가 아니라 POST /api/reviews 안에 들어가 있다.
(리뷰를 쓰면 자동으로 분류가 돌아간다)
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Cafe
from ..services import ai

router = APIRouter(prefix="/api/ai", tags=["ai (분류·파싱)"])


@router.get("/config", summary="AI 사용 가능 여부")
def ai_config():
    """프론트가 AI 관련 UI 를 켤지 판단한다. 키는 절대 내려보내지 않는다."""
    return {
        "enabled": ai.is_configured(),
        "model": settings.ai_model if ai.is_configured() else None,
        "hint": None if ai.is_configured()
        else "AI_API_URL / AI_API_KEY 를 .env 에 넣으면 켜집니다",
    }


class ReviewIn(BaseModel):
    text: str = Field(..., description="리뷰 본문",
                      examples=["콘센트가 자리마다 있고 조용해서 3시간 작업했어요. 자리는 좀 좁아요"])


@router.post("/classify-review", summary="리뷰 텍스트 → 카공 속성 (데이터 분류)")
def classify_review(body: ReviewIn):
    """공공데이터에 0건인 카공 정보를 리뷰 문장에서 뽑아낸다.

    `dropped` 는 모델이 값을 냈지만 근거 문장이 원문에 없어서 우리가 버린 항목이다.
    이 값이 환각 방어가 실제로 동작한다는 증거라서 응답에 그대로 노출한다.
    """
    try:
        return ai.classify_review(body.text)
    except ai.AIRateLimited as e:
        raise HTTPException(429, f"무료 한도를 넘겼습니다. 잠시 후 다시 시도하세요 — {e}") from e
    except ai.AIUnavailable as e:
        raise HTTPException(503, str(e)) from e


class HoursIn(BaseModel):
    text: str = Field(..., description="영업시간 원문(비정형)",
                      examples=["[목요일~일요일]<br>10:00~18:00 <br>[월요일] 휴무"])


@router.post("/parse-hours", summary="영업시간 원문 → 구조화 (시간 파싱)")
def parse_hours(body: HoursIn):
    try:
        return ai.parse_hours(body.text)
    except ai.AIRateLimited as e:
        raise HTTPException(429, f"무료 한도를 넘겼습니다. 잠시 후 다시 시도하세요 — {e}") from e
    except ai.AIUnavailable as e:
        raise HTTPException(503, str(e)) from e


@router.get("/coverage", summary="AI 가 채운 데이터가 얼마나 되나 (발표용)")
def coverage(db: Session = Depends(get_db)):
    """'데이터를 늘렸다'는 주장을 숫자로 만든다.

    발표에서 before/after 를 이 한 번의 호출로 보여줄 수 있다.
    """
    total = db.scalar(select(func.count(Cafe.id))) or 0

    def filled(col):
        return db.scalar(select(func.count(Cafe.id)).where(col.is_not(None))) or 0

    closed = db.scalar(
        select(func.count(Cafe.id)).where(Cafe.closed_days != "")) or 0
    voted = db.scalar(
        select(func.count(Cafe.id)).where((Cafe.cagong_yes + Cafe.cagong_no) > 0)) or 0
    hours_high = db.scalar(
        select(func.count(Cafe.id)).where(Cafe.hours_confidence == "high")) or 0

    def pct(n):
        return round(n / total * 100, 1) if total else 0.0

    return {
        "total_cafes": total,
        "note": "공공데이터 원본에는 콘센트·와이파이·좌석수·휴무일이 모두 0건이었다",
        "fields": {
            "has_power": {"filled": filled(Cafe.has_power), "percent": pct(filled(Cafe.has_power))},
            "has_wifi": {"filled": filled(Cafe.has_wifi), "percent": pct(filled(Cafe.has_wifi))},
            "quiet": {"filled": filled(Cafe.quiet), "percent": pct(filled(Cafe.quiet))},
            "size_label": {"filled": filled(Cafe.size_label), "percent": pct(filled(Cafe.size_label))},
            "closed_days": {"filled": closed, "percent": pct(closed)},
            "cagong_voted": {"filled": voted, "percent": pct(voted)},
            "hours_high_confidence": {"filled": hours_high, "percent": pct(hours_high)},
        },
    }
