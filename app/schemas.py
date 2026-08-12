from datetime import datetime

from pydantic import BaseModel, Field


# ---------- Cafe ----------
class RewardPreview(BaseModel):
    point: int
    multiplier: float
    is_boosted: bool
    badge: str | None = None


class StayInfo(BaseModel):
    stay_ok: bool
    open_now: bool
    reason: str            # ok / closed_today / already_closed / not_open_yet
    #                        / not_enough_time / break_time
    label: str
    minutes_left: int
    arrival_at: str | None = None
    last_call_at: str | None = None
    break_time: str | None = None   # "15:00~17:00" — 있으면 프론트에 배지로 표시
    sit_from: str | None = None     # 브레이크 후 앉을 수 있는 시각


class CafeOut(BaseModel):
    id: int
    name: str
    place_type: str = "cafe"      # cafe / restaurant
    category: str | None = None
    address: str | None = None
    road_address: str | None = None
    phone: str | None = None
    place_url: str | None = None
    lat: float
    lng: float
    region: str | None = None
    district: str | None = None

    open_time: str
    close_time: str
    closed_days: str
    break_start: str | None = None
    break_end: str | None = None
    hours_source: str
    hours_confidence: str
    hours_text: str | None = None

    parking: bool | None = None
    has_toilet: bool | None = None
    summary: str | None = None

    laptop_ok: bool
    has_power: bool
    has_wifi: bool
    quiet: bool
    seat_count: int
    cagong_source: str
    cagong_ok: bool
    cagong_score: int

    review_count: int
    rating_avg: float
    dist_to_hotspot_km: float
    is_remote: bool

    distance_km: float | None = None
    travel_min: int | None = None
    travel_source: str | None = None  # kakao(실측) / cache / estimated / fixed / default
    reward: RewardPreview
    stay: StayInfo | None = None

    model_config = {"from_attributes": True}


class CafeListOut(BaseModel):
    total: int
    now: str
    filters_applied: dict
    items: list[CafeOut]


class CafeFlagsIn(BaseModel):
    """유저 제보 / 점주 인증으로 카공 정보 갱신."""
    laptop_ok: bool | None = None
    has_power: bool | None = None
    has_wifi: bool | None = None
    quiet: bool | None = None
    seat_count: int | None = None
    open_time: str | None = None
    close_time: str | None = None
    closed_days: str | None = None
    source: str = Field(default="user", pattern="^(user|owner|manual)$")


# ---------- User ----------
class UserCreate(BaseModel):
    nickname: str
    email: str | None = None
    is_workationer: bool = True


class UserOut(BaseModel):
    id: int
    nickname: str
    email: str | None = None
    provider: str | None = None      # google / kakao / dev
    avatar_url: str | None = None
    is_workationer: bool
    point_balance: int
    model_config = {"from_attributes": True}


class LedgerOut(BaseModel):
    id: int
    amount: int
    reason: str
    balance_after: int
    cafe_id: int | None = None
    created_at: datetime | None = None
    model_config = {"from_attributes": True}


class PointSummaryOut(BaseModel):
    user_id: int
    nickname: str
    point_balance: int
    review_count: int
    remote_review_count: int
    history: list[LedgerOut]


# ---------- Review ----------
class ReviewCreate(BaseModel):
    """작성자는 Authorization 헤더의 토큰에서 가져온다 (user_id 안 받음)."""
    cafe_id: int
    rating: int = Field(default=5, ge=1, le=5)
    content: str = ""
    tags: list[str] = []


class ReviewOut(BaseModel):
    id: int
    cafe_id: int
    user_id: int
    rating: int
    content: str
    tags: list[str] = []
    earned_point: int
    created_at: datetime | None = None
    model_config = {"from_attributes": True}


class ReportCreate(BaseModel):
    """카공 정보 제보. field는 laptop_ok/has_power/has_wifi/quiet/seat_count.

    제보자는 Authorization 헤더의 토큰에서 가져온다 (user_id 안 받음).
    """
    cafe_id: int
    field: str
    value_bool: bool | None = None
    value_int: int | None = None
    is_owner: bool = False


class ReportCreatedOut(BaseModel):
    report_id: int
    applied: bool
    status_message: str
    aggregate: dict
    earned_point: int
    point_balance: int
    breakdown: list[dict] = []


class ReviewCreatedOut(BaseModel):
    review: ReviewOut
    earned_point: int
    point_balance: int
    headline: str
    breakdown: list[dict]
    cafe_review_count: int
    cafe_rating_avg: float
