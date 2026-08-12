"""위치 계산 유틸 + 제주 오버투어리즘 핫스팟 정의."""

from math import asin, cos, radians, sin, sqrt

# 실제로 인파가 몰리는 제주 대표 과밀 지점 (오버투어리즘 기준점)
HOTSPOTS: list[tuple[str, float, float]] = [
    ("제주시청·연동 상권", 33.4996, 126.5312),
    ("애월 한담해안", 33.4667, 126.3106),
    ("협재해수욕장", 33.3940, 126.2396),
    ("성산일출봉", 33.4581, 126.9425),
    ("서귀포 올레시장", 33.2496, 126.5636),
    ("중문관광단지", 33.2504, 126.4104),
    ("함덕해수욕장", 33.5433, 126.6697),
    ("제주공항", 33.5070, 126.4930),
]

# 제주도 대략 bbox (수집 스크립트 / 유효성 검사용)
JEJU_BBOX = {"min_lat": 33.10, "max_lat": 33.60, "min_lng": 126.14, "max_lng": 126.99}

REMOTE_THRESHOLD_KM = 5.0  # 모든 핫스팟에서 5km 이상 떨어지면 '외곽지'


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    )
    return round(2 * r * asin(sqrt(a)), 3)


def nearest_hotspot(lat: float, lng: float) -> tuple[str, float]:
    """가장 가까운 과밀 핫스팟과 거리(km)."""
    best = min(HOTSPOTS, key=lambda h: haversine_km(lat, lng, h[1], h[2]))
    return best[0], haversine_km(lat, lng, best[1], best[2])


def classify_remote(lat: float, lng: float) -> tuple[float, bool]:
    """(핫스팟까지 거리, 외곽지 여부)"""
    _, dist = nearest_hotspot(lat, lng)
    return dist, dist >= REMOTE_THRESHOLD_KM


def estimate_travel_min(distance_km: float) -> int:
    """제주 도로 평균 30km/h 가정 + 주차/도보 5분. 이동시간(분)."""
    if distance_km <= 0:
        return 5
    return max(5, int(round(distance_km / 30 * 60)) + 5)


def in_jeju(lat: float, lng: float) -> bool:
    return (
        JEJU_BBOX["min_lat"] <= lat <= JEJU_BBOX["max_lat"]
        and JEJU_BBOX["min_lng"] <= lng <= JEJU_BBOX["max_lng"]
    )
