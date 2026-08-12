from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import Cafe, CagongReport, User
from ..schemas import ReportCreate, ReportCreatedOut
from ..services import crowdsource as cs

router = APIRouter(prefix="/api/reports", tags=["reports (크라우드소싱)"])


@router.post("", response_model=ReportCreatedOut,
             summary="카공 정보 제보 (핵심기능3) — 로그인 필요")
def create_report(
    body: ReportCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    유저가 "여기 콘센트 있어요" 같은 정보를 제보한다.
    2명 이상 같은 항목을 제보하고 과반이 동의하면 자동 반영.
    점주(is_owner=true)는 1건으로 즉시 확정.

    제보에도 적립금이 붙으므로 로그인 필수 — 익명 제보를 열면
    한 사람이 여러 번 제보해서 합의 규칙을 무력화할 수 있다.
    """
    cafe = db.get(Cafe, body.cafe_id)
    if not cafe:
        raise HTTPException(404, "카페를 찾을 수 없습니다")

    try:
        return cs.submit_report(
            db, cafe, user, body.field,
            value_bool=body.value_bool, value_int=body.value_int,
            is_owner=body.is_owner,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/cafe/{cafe_id}", summary="카페별 제보 현황 + 정보 채움률")
def cafe_coverage(cafe_id: int, db: Session = Depends(get_db)):
    cafe = db.get(Cafe, cafe_id)
    if not cafe:
        raise HTTPException(404, "카페를 찾을 수 없습니다")
    return cs.coverage(db, cafe)


@router.get("/wanted", summary="제보가 가장 필요한 매장 (유저 참여 유도용)")
def wanted(
    db: Session = Depends(get_db),
    limit: int = 10,
    place_type: str = Query("cafe", pattern="^(cafe|restaurant|all)$"),
):
    """
    카공 정보가 추정치(estimated)로만 남아있는 외곽 매장을 우선 노출한다.
    '이 카페 정보를 채우면 300P' 같은 미션 카드로 쓰면 리텐션이 붙는다.

    공공데이터에는 콘센트·와이파이·노트북 허용 정보가 아예 없다(719건 전부 공란).
    그래서 서비스 초기에는 이 목록이 곧 전체 매장이고, 제보가 쌓이면서 줄어든다.
    이 감소 곡선 자체가 크라우드소싱이 작동한다는 증거가 된다.
    """
    stmt = select(Cafe).where(Cafe.cagong_source == "estimated")
    if place_type != "all":
        stmt = stmt.where(Cafe.place_type == place_type)

    rows = db.scalars(
        stmt.order_by(Cafe.is_remote.desc(), Cafe.review_count.asc()).limit(limit)
    ).all()
    return {
        "items": [{
            "cafe_id": c.id, "name": c.name, "district": c.district,
            "is_remote": c.is_remote, "lat": c.lat, "lng": c.lng,
            "missing": [f for f in cs.ALL_FIELDS],
            "reward_hint": cs.REPORT_POINT + cs.FIRST_INFO_BONUS,
        } for c in rows]
    }


@router.get("/stats", summary="제보 집계 현황 (발표용)")
def stats(db: Session = Depends(get_db)):
    total = db.scalar(select(func.count(CagongReport.id))) or 0
    applied = db.scalar(
        select(func.count(CagongReport.id)).where(CagongReport.applied.is_(True))
    ) or 0
    by_source = {}
    for src, n in db.execute(
        select(Cafe.cagong_source, func.count(Cafe.id)).group_by(Cafe.cagong_source)
    ).all():
        by_source[src] = n
    return {
        "total_reports": total,
        "applied_reports": applied,
        "cafes_by_cagong_source": by_source,
        "verified_ratio": round(
            (by_source.get("user", 0) + by_source.get("owner", 0))
            / max(sum(by_source.values()), 1) * 100, 1
        ),
    }
