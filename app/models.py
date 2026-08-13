from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Cafe(Base):
    """제주 카페/작업공간 마스터 테이블."""

    __tablename__ = "cafes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kakao_place_id: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    visitjeju_id: Mapped[str | None] = mapped_column(String(40), index=True)
    # 인허가번호. 참고용으로만 보관한다 — 원본 데이터에 서로 다른 가게가 같은
    # 번호를 쓰는 사례가 3쌍 있어서 유일키로 못 쓴다(공공데이터 품질 이슈).
    license_no: Mapped[str | None] = mapped_column(String(32), index=True)
    # 재적재 시 중복 방지용 자연키: 정규화한 이름 + 좌표(소수 5자리 ≈ 1m).
    # 같은 엑셀을 여러 번 돌려도 안전하고, 좌표가 바뀌면 다른 지점으로 본다.
    source_key: Mapped[str | None] = mapped_column(String(160), unique=True, index=True)

    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(120))
    # cafe(카페·베이커리·디저트) / restaurant(일반 음식점)
    # 카공 추천은 cafe 기본, 오버투어리즘 분산 통계는 둘 다 사용한다.
    place_type: Mapped[str] = mapped_column(String(12), default="cafe", index=True)
    address: Mapped[str | None] = mapped_column(String(255))
    road_address: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40))
    place_url: Mapped[str | None] = mapped_column(String(255))
    thumbnail_url: Mapped[str | None] = mapped_column(String(255))

    # 위치
    lat: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    lng: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    region: Mapped[str | None] = mapped_column(String(20))    # 제주시 / 서귀포시
    district: Mapped[str | None] = mapped_column(String(40))  # 애월읍, 성산읍 ...

    # 영업시간 (HH:MM 문자열 — 해커톤 스코프에서 요일별 분리 안 함)
    open_time: Mapped[str] = mapped_column(String(5), default="09:00")
    close_time: Mapped[str] = mapped_column(String(5), default="21:00")
    last_order_min: Mapped[int] = mapped_column(Integer, default=30)  # 마감 N분 전 라스트오더
    closed_days: Mapped[str] = mapped_column(String(20), default="")  # "월,화"

    # 브레이크 타임 — '여유롭게 2시간'의 실제 방해 요인.
    # 영업시간만 보면 통과하는데 15:00~17:00 브레이크에 걸려 실제로는 못 앉는 경우가
    # 제주 음식점에 흔하다(전체의 15%). 둘 다 비어있으면 브레이크 없음.
    break_start: Mapped[str | None] = mapped_column(String(5))
    break_end: Mapped[str | None] = mapped_column(String(5))
    hours_source: Mapped[str] = mapped_column(String(16), default="estimated")
    # estimated(추정) / visitjeju(공식API) / csv(수동입력) / owner(점주) / manual
    hours_text: Mapped[str | None] = mapped_column(String(255))       # 파싱 전 원문 보존
    hours_confidence: Mapped[str] = mapped_column(String(8), default="low")  # high/medium/low

    # 카공 환경 — 항목별 부가정보. 필터 판정은 아래 '리뷰 투표'가 담당한다.
    # None = 모름. '아님(False)'과 반드시 구분한다. 근거 없이 채우지 않는다.
    laptop_ok: Mapped[bool | None] = mapped_column(Boolean)     # 노트북 사용 허용
    has_power: Mapped[bool | None] = mapped_column(Boolean)     # 콘센트
    has_wifi: Mapped[bool | None] = mapped_column(Boolean)      # 와이파이
    quiet: Mapped[bool | None] = mapped_column(Boolean)         # 조용한 분위기
    seat_count: Mapped[int | None] = mapped_column(Integer)
    cagong_source: Mapped[str] = mapped_column(String(12), default="unknown")
    # unknown(정보없음) / review(리뷰 투표) / user(제보) / owner(점주 인증)

    # 카공 판정 — 리뷰 투표 집계. '카공 가능' 필터의 유일한 근거.
    cagong_yes: Mapped[int] = mapped_column(Integer, default=0)   # 가능 투표 수
    cagong_no: Mapped[int] = mapped_column(Integer, default=0)    # 불가 투표 수
    cagong_ok: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # cagong_ok 는 yes > no 의 결과를 저장한 것. 지도 쿼리에서 DB 인덱스로
    # 걸러야 해서 파생값이지만 컬럼으로 둔다.

    # 매장 넓이 — 좌석수를 숫자로 물으면 아무도 모른다. 체감 3단계로 받는다.
    size_small: Mapped[int] = mapped_column(Integer, default=0)
    size_medium: Mapped[int] = mapped_column(Integer, default=0)
    size_large: Mapped[int] = mapped_column(Integer, default=0)
    size_label: Mapped[str | None] = mapped_column(String(8))    # small/medium/large

    # 공공데이터에서 그대로 오는 부가정보 (크라우드소싱 대상 아님)
    parking: Mapped[bool | None] = mapped_column(Boolean)       # 주차 가능
    has_toilet: Mapped[bool | None] = mapped_column(Boolean)    # 화장실 유무
    summary: Mapped[str | None] = mapped_column(Text)           # 관광공사 소개문

    # 오버투어리즘 지표
    # dist_to_hotspot_km 은 이제 화면 표시용이다. 적립금은 아래 지역 지수로 계산한다.
    # 이유: 침체 지역 9곳 중 5곳(삼도1동·이도1동·용담1동·화북동·봉개동)이 제주시
    # 원도심이다. 거리 기준으로는 '핫스팟과 가까움'이라 보너스가 안 붙는데, 정작
    # 소비액은 가장 낮은 곳들이다. 거리는 소외의 대리지표로 쓰기에 부정확하다.
    dist_to_hotspot_km: Mapped[float] = mapped_column(Float, default=0.0)
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # 지역 활성도 — 내비게이션 검색량 + 방문객 수 + 추정 소비액의 종합 지수.
    # 적립금 차등의 유일한 근거다. (data/region_index.csv)
    region_state: Mapped[str] = mapped_column(String(8), default="보통", index=True)
    # 과밀 / 보통 / 침체 / 미분류
    region_index: Mapped[float | None] = mapped_column(Float)   # 0.0(침체) ~ 0.92(과밀)
    region_rank: Mapped[int | None] = mapped_column(Integer)    # 42개 지역 중 순위

    # 집계
    review_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    rating_avg: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    reviews: Mapped[list["Review"]] = relationship(back_populates="cafe")


Index("ix_cafes_bbox", Cafe.lat, Cafe.lng)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Supabase Auth 의 사용자 UUID(JWT의 sub 클레임). 소셜 로그인 유저의 진짜 신원.
    supabase_uid: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    provider: Mapped[str | None] = mapped_column(String(20))   # google / kakao / email
    avatar_url: Mapped[str | None] = mapped_column(String(400))

    nickname: Mapped[str] = mapped_column(String(40), nullable=False)
    email: Mapped[str | None] = mapped_column(String(120), unique=True)
    is_workationer: Mapped[bool] = mapped_column(Boolean, default=True)  # 런케이션 참가자 여부
    point_balance: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cafe_id: Mapped[int] = mapped_column(ForeignKey("cafes.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    rating: Mapped[int] = mapped_column(Integer, default=5)
    content: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(String(255), default="")  # "콘센트많음,조용함"

    # 카공 투표 — 이 두 값이 매장의 '카공 가능' 판정을 만든다.
    # None 을 허용하는 이유: 리뷰는 쓰되 카공 여부는 모르겠다는 사람을 강제로
    # 찍게 만들면, 그 찍은 값이 그대로 오염 데이터가 된다.
    cagong_vote: Mapped[bool | None] = mapped_column(Boolean)   # True=가능 / False=불가
    size_vote: Mapped[str | None] = mapped_column(String(8))    # small/medium/large

    earned_point: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    cafe: Mapped["Cafe"] = relationship(back_populates="reviews")


class PointLedger(Base):
    """적립금 원장 — 왜 이만큼 줬는지 근거를 남겨야 심사에서 설명이 됨."""

    __tablename__ = "point_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    review_id: Mapped[int | None] = mapped_column(ForeignKey("reviews.id"))
    cafe_id: Mapped[int | None] = mapped_column(ForeignKey("cafes.id"))

    amount: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(120), default="")
    breakdown: Mapped[str] = mapped_column(Text, default="")  # JSON 문자열
    balance_after: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CagongReport(Base):
    """핵심기능3 — 카공 인프라 크라우드소싱 제보.

    한 사람 말만 믿지 않는다. 같은 항목에 제보가 2건 이상 쌓이고
    과반이 동의할 때만 카페 정보에 반영한다(services/crowdsource.py).
    점주(is_owner=True) 제보는 1건으로 즉시 확정.
    """

    __tablename__ = "cagong_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cafe_id: Mapped[int] = mapped_column(ForeignKey("cafes.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    field: Mapped[str] = mapped_column(String(20), index=True)  # laptop_ok/has_power/...
    value_bool: Mapped[bool | None] = mapped_column(Boolean)
    value_int: Mapped[int | None] = mapped_column(Integer)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RouteCache(Base):
    """카카오모빌리티 길찾기 응답 캐시.

    같은 출발지-도착지를 지도 이동할 때마다 다시 호출하면 쿼터가 순식간에 녹는다.
    좌표를 소수점 3자리(약 100m)로 반올림해 캐시 키로 쓴다.
    """

    __tablename__ = "route_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(10), default="car")
    distance_m: Mapped[int] = mapped_column(Integer, default=0)
    duration_s: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="kakao")  # kakao/estimated
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
