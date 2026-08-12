from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import cagong as cg
from ..core import rewards
from ..core.geo import haversine_km
from ..core.hours import evaluate_stay, parse_now
from ..database import get_db
from ..models import Cafe
from ..schemas import CafeFlagsIn, CafeListOut, CafeOut
from ..services import travel as tv

router = APIRouter(prefix="/api/cafes", tags=["cafes"])


def base_fields(cafe: Cafe) -> dict:
    d = {c.name: getattr(cafe, c.name) for c in Cafe.__table__.columns}
    d["cagong_ok"] = cg.is_cagong_ok(cafe)
    d["cagong_score"] = cg.cagong_score(cafe)
    d["reward"] = rewards.preview_reward(cafe)
    return d


def attach_travel(d: dict, cafe: Cafe, user_lat, user_lng, mode, travel_min_fixed,
                  db=None, precise=False) -> dict:
    if travel_min_fixed is not None:
        d["travel_min"] = travel_min_fixed
        d["travel_source"] = "fixed"
        d["distance_km"] = (
            haversine_km(user_lat, user_lng, cafe.lat, cafe.lng)
            if user_lat is not None else None
        )
        return d

    if user_lat is None or user_lng is None:
        d["travel_min"] = 15
        d["travel_source"] = "default"
        d["distance_km"] = None
        return d

    t = tv.get_travel(db, user_lat, user_lng, cafe.lat, cafe.lng,
                      mode=mode, precise=precise)
    d["travel_min"] = t["minutes"]
    d["distance_km"] = t["distance_km"]
    d["travel_source"] = t["source"]
    return d


@router.get("", response_model=CafeListOut, summary="지도 카페 검색 (모든 필터 진입점)")
def list_cafes(
    db: Session = Depends(get_db),
    lat: float | None = Query(None, description="유저 현재 위도"),
    lng: float | None = Query(None, description="유저 현재 경도"),
    radius_km: float = Query(10.0, description="lat/lng 기준 반경(km)"),
    sw_lat: float | None = Query(None, description="지도 좌하단 위도(bbox)"),
    sw_lng: float | None = None,
    ne_lat: float | None = None,
    ne_lng: float | None = None,
    q: str | None = Query(None, description="가게명 키워드"),
    place_type: str = Query(
        "cafe", pattern="^(cafe|restaurant|all)$",
        description="cafe(기본) / restaurant / all. 카공 추천은 cafe, "
                    "오버투어리즘 분산 통계는 all",
    ),
    cagong: bool = Query(False, description="핵심기능3: '카공 가능'만 보기"),
    stay_hours: float | None = Query(None, description="핵심기능2: '여유롭게 N시간' (기본 2)"),
    travel_mode: str = Query("car", pattern="^(car|walk)$", description="이동수단"),
    travel_min: int | None = Query(None, description="이동시간 고정값(분). 지정 시 계산 안 함"),
    precise: bool = Query(False, description="상위 N개는 카카오 길찾기로 실제 소요시간 계산"),
    precise_top: int = Query(10, le=30, description="정밀 계산할 개수 (쿼터 보호)"),
    open_now: bool = Query(False, description="지금 영업중만"),
    remote_only: bool = Query(False, description="핵심기능1: 외곽/소외 매장만"),
    min_reward: int | None = Query(None, description="예상 적립금 하한"),
    hours_verified: bool = Query(False, description="영업시간이 검증된(high) 매장만"),
    now: str | None = Query(None, description="데모용 현재시각 override ('14:30' or ISO)"),
    sort: str = Query("distance", pattern="^(distance|reward|review|cagong)$"),
    limit: int = Query(200, le=500),
):
    """
    지도 기반 검색. 프론트는 이 하나만 호출하면 3대 기능이 전부 커버된다.

    예)
      /api/cafes?lat=33.499&lng=126.531&cagong=true&stay_hours=2
      /api/cafes?lat=33.499&lng=126.531&stay_hours=2&precise=true   (실제 도로 기준)
      /api/cafes?remote_only=true&sort=reward
    """
    now_dt = parse_now(now)
    stmt = select(Cafe)

    if None not in (sw_lat, sw_lng, ne_lat, ne_lng):
        stmt = stmt.where(
            Cafe.lat.between(sw_lat, ne_lat), Cafe.lng.between(sw_lng, ne_lng)
        )
    if q:
        stmt = stmt.where(Cafe.name.ilike(f"%{q}%"))
    if place_type != "all":
        stmt = stmt.where(Cafe.place_type == place_type)
    if cagong:
        stmt = stmt.where(Cafe.laptop_ok.is_(True), Cafe.has_power.is_(True),
                          Cafe.has_wifi.is_(True))
    if remote_only:
        stmt = stmt.where(Cafe.is_remote.is_(True))
    if hours_verified:
        stmt = stmt.where(Cafe.hours_confidence == "high")

    rows = list(db.scalars(stmt).all())

    # 1단계: 직선거리로 후보 압축 (길찾기 API 호출 전에 범위부터 좁힌다)
    if lat is not None and lng is not None:
        scored = [(haversine_km(lat, lng, c.lat, c.lng), c) for c in rows]
        scored = [(d, c) for d, c in scored if d <= radius_km]
        scored.sort(key=lambda x: x[0])
        rows = [c for _, c in scored]

    # 2단계: 정밀 계산은 가까운 순 상위 N개만 (쿼터 보호)
    precise_ids = {c.id for c in rows[:precise_top]} if precise else set()

    items: list[dict] = []
    for cafe in rows:
        d = base_fields(cafe)
        d = attach_travel(d, cafe, lat, lng, travel_mode, travel_min,
                          db=db, precise=cafe.id in precise_ids)
        d["stay"] = evaluate_stay(cafe, now_dt, stay_hours or 2.0, d["travel_min"])

        if stay_hours is not None and not d["stay"]["stay_ok"]:
            continue
        if open_now and not d["stay"]["open_now"]:
            continue
        if min_reward is not None and d["reward"]["point"] < min_reward:
            continue
        items.append(d)

    if sort == "distance" and lat is not None:
        items.sort(key=lambda x: x["distance_km"] if x["distance_km"] is not None else 9e9)
    elif sort == "reward":
        items.sort(key=lambda x: -x["reward"]["point"])
    elif sort == "review":
        items.sort(key=lambda x: -x["review_count"])
    elif sort == "cagong":
        items.sort(key=lambda x: -x["cagong_score"])

    items = items[:limit]
    return {
        "total": len(items),
        "now": now_dt.strftime("%Y-%m-%d %H:%M"),
        "filters_applied": {
            "place_type": place_type,
            "cagong": cagong, "stay_hours": stay_hours, "open_now": open_now,
            "remote_only": remote_only, "hours_verified": hours_verified,
            "radius_km": radius_km if lat else None,
            "travel_mode": travel_mode, "precise": precise,
        },
        "items": items,
    }


@router.get("/{cafe_id}", response_model=CafeOut, summary="카페 상세 (실제 이동시간 계산)")
def get_cafe(
    cafe_id: int,
    db: Session = Depends(get_db),
    lat: float | None = None,
    lng: float | None = None,
    travel_mode: str = Query("car", pattern="^(car|walk)$"),
    precise: bool = Query(True, description="상세는 기본으로 실제 길찾기 호출"),
    stay_hours: float = 2.0,
    now: str | None = None,
):
    cafe = db.get(Cafe, cafe_id)
    if not cafe:
        raise HTTPException(404, "카페를 찾을 수 없습니다")

    now_dt = parse_now(now)
    d = base_fields(cafe)
    d = attach_travel(d, cafe, lat, lng, travel_mode, None, db=db, precise=precise)
    d["stay"] = evaluate_stay(cafe, now_dt, stay_hours, d["travel_min"])
    return d


@router.patch("/{cafe_id}/flags", response_model=CafeOut, summary="카공/영업시간 직접 수정 (관리용)")
def update_flags(cafe_id: int, body: CafeFlagsIn, db: Session = Depends(get_db)):
    """단건 강제 수정. 유저 제보는 POST /api/reports 를 쓸 것."""
    cafe = db.get(Cafe, cafe_id)
    if not cafe:
        raise HTTPException(404, "카페를 찾을 수 없습니다")

    payload = body.model_dump(exclude_none=True)
    source = payload.pop("source", "user")
    for key, value in payload.items():
        setattr(cafe, key, value)
    if any(k in payload for k in ("laptop_ok", "has_power", "has_wifi", "quiet", "seat_count")):
        cafe.cagong_source = source
    if any(k in payload for k in ("open_time", "close_time", "closed_days")):
        cafe.hours_source = "manual"
        cafe.hours_confidence = "high" if source == "owner" else "medium"

    db.commit()
    db.refresh(cafe)

    d = base_fields(cafe)
    d = attach_travel(d, cafe, None, None, "car", None)
    d["stay"] = evaluate_stay(cafe, parse_now(None), 2.0, d["travel_min"])
    return d
