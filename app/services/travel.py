"""이동시간 계산 — 카카오모빌리티 자동차 길찾기 + 도보 추정.

설계 결정 (심사에서 물어보면 이렇게 답하면 됨)
------------------------------------------------
Q. 왜 목록 API에서 카페마다 길찾기를 호출하지 않나?
A. 지도 한 번 움직일 때마다 200개 카페 × 1회 = 200콜. 쿼터도 지연도 감당 불가.
   그래서 2단계로 나눴다.
     - 목록: 직선거리 기반 추정 (제주 평균 30km/h + 주차·도보 5분)으로 즉시 필터
     - 상세/정밀조회: `precise=true` 일 때만 실제 카카오 길찾기 호출 (상위 N개 한정)
   추정치와 실측치가 다르면 응답의 travel_source 로 구분해서 내려준다.

Q. 도보는 왜 실제 API를 안 쓰나?
A. 카카오모빌리티 도보 길찾기는 제휴 파트너 전용(사전 계약 필요)이라
   해커톤 기간에 발급 불가. 직선거리 × 1.3(우회계수) ÷ 4km/h 로 추정한다.
   이 계수는 config에서 조정 가능.

Q. 캐싱?
A. 좌표를 소수점 3자리(약 100m)로 반올림한 키로 DB에 저장. 같은 경로 재호출 안 함.
"""

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..core.geo import haversine_km
from ..models import RouteCache

NAVI_URL = "https://apis-navi.kakaomobility.com/v1/directions"


def _key(o_lat, o_lng, d_lat, d_lng, mode) -> str:
    return f"{mode}:{o_lat:.3f},{o_lng:.3f}>{d_lat:.3f},{d_lng:.3f}"


# ---------------------------------------------------------------- 추정식
def estimate_car(distance_km: float) -> int:
    """제주 평균 주행 30km/h + 주차·도보 5분. 분 단위."""
    if distance_km <= 0:
        return 5
    return max(5, round(distance_km / 30 * 60) + 5)


def estimate_walk(distance_km: float) -> int:
    """직선거리 × 우회계수 ÷ 보행속도. 분 단위."""
    if distance_km <= 0:
        return 3
    real_km = distance_km * settings.walk_detour_factor
    return max(3, round(real_km / settings.walk_speed_kmh * 60))


def estimate(distance_km: float, mode: str = "car") -> int:
    return estimate_walk(distance_km) if mode == "walk" else estimate_car(distance_km)


# ---------------------------------------------------------------- 실제 호출
def _call_kakao(o_lat, o_lng, d_lat, d_lng) -> tuple[int, int] | None:
    """카카오모빌리티 자동차 길찾기. (거리 m, 소요 초) 또는 실패 시 None."""
    if not settings.kakao_rest_key:
        return None
    try:
        r = httpx.get(
            NAVI_URL,
            params={
                "origin": f"{o_lng},{o_lat}",       # 경도,위도 순서 주의
                "destination": f"{d_lng},{d_lat}",
                "priority": "RECOMMEND",
                "car_fuel": "GASOLINE",
                "summary": "true",
            },
            headers={"Authorization": f"KakaoAK {settings.kakao_rest_key}"},
            timeout=5.0,
        )
        if r.status_code != 200:
            return None
        routes = r.json().get("routes") or []
        if not routes or routes[0].get("result_code") != 0:
            return None
        s = routes[0].get("summary", {})
        return int(s.get("distance", 0)), int(s.get("duration", 0))
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def get_travel(
    db: Session | None,
    o_lat: float, o_lng: float,
    d_lat: float, d_lng: float,
    mode: str = "car",
    precise: bool = False,
) -> dict:
    """
    반환: {minutes, distance_km, source}
      source = "kakao"(실측) | "cache" | "estimated"(직선거리 추정)
    """
    straight_km = haversine_km(o_lat, o_lng, d_lat, d_lng)

    # 도보는 제휴 API 필요 → 항상 추정
    if mode == "walk":
        return {"minutes": estimate_walk(straight_km),
                "distance_km": round(straight_km * settings.walk_detour_factor, 2),
                "source": "estimated"}

    if not precise or not settings.use_kakao_navi:
        return {"minutes": estimate_car(straight_km),
                "distance_km": straight_km, "source": "estimated"}

    key = _key(o_lat, o_lng, d_lat, d_lng, mode)

    if db is not None:
        hit = db.query(RouteCache).filter(RouteCache.cache_key == key).first()
        if hit:
            return {"minutes": max(1, round(hit.duration_s / 60)),
                    "distance_km": round(hit.distance_m / 1000, 2),
                    "source": "cache" if hit.source == "kakao" else "estimated"}

    result = _call_kakao(o_lat, o_lng, d_lat, d_lng)
    if result is None:
        # API 실패해도 서비스는 멈추지 않는다 — 추정치로 폴백
        return {"minutes": estimate_car(straight_km),
                "distance_km": straight_km, "source": "estimated"}

    dist_m, dur_s = result
    if db is not None:
        db.add(RouteCache(cache_key=key, mode=mode, distance_m=dist_m,
                          duration_s=dur_s, source="kakao"))
        db.commit()

    return {"minutes": max(1, round(dur_s / 60)),
            "distance_km": round(dist_m / 1000, 2), "source": "kakao"}
