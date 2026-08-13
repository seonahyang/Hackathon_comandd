from datetime import datetime
from typing import Literal

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


class CagongVerdict(BaseModel):
    """리뷰 투표 기반 카공 판정 결과. 프론트 뱃지·문구를 이걸로 그린다."""
    state: str              # ok(가능) / no(불가) / tie(동률) / unknown(투표없음)
    ok: bool
    yes: int
    no: int
    total_votes: int
    message: str            # 그대로 화면에 노출 가능한 문구
    size: str | None = None         # small / medium / large
    size_label: str | None = None   # 협소 / 보통 / 넓음
    size_votes: dict = {}
    source: str


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

    # None = 모름. 프론트는 '아님'과 다르게 표시해야 한다.
    laptop_ok: bool | None = None
    has_power: bool | None = None
    has_wifi: bool | None = None
    quiet: bool | None = None
    seat_count: int | None = None
    cagong_source: str

    # 리뷰 투표 기반 판정
    cagong_yes: int = 0
    cagong_no: int = 0
    cagong_ok: bool = False
    cagong_score: int
    cagong_verdict: CagongVerdict | None = None

    # 매장 넓이
    size_label: str | None = None
    size_small: int = 0
    size_medium: int = 0
    size_large: int = 0

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

    # 카공 투표 — 이 값이 매장의 '카공 가능' 판정을 만든다.
    # 안 보내면(None) 투표하지 않은 것으로 처리한다. 강제하지 않는다.
    cagong_vote: bool | None = Field(
        default=None, description="True=카공 가능 / False=카공 불가 / 미입력=모름"
    )
    size_vote: Literal["small", "medium", "large"] | None = Field(
        default=None, description="매장 넓이 — small(협소) / medium(보통) / large(넓음)"
    )


class ReviewOut(BaseModel):
    id: int
    cafe_id: int
    user_id: int
    rating: int
    content: str
    tags: list[str] = []
    cagong_vote: bool | None = None
    size_vote: str | None = None
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
