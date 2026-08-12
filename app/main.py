from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .core.geo import HOTSPOTS
from .database import Base, engine
from .routers import auth, cafes, reports, reviews, stats, users

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="제주 카공스팟 API",
    version="0.1.0",
    description=(
        "런케이션 관광객을 위한 제주 지도 기반 카페 추천 백엔드.\n\n"
        "**핵심 3기능**\n"
        "1. 외곽지/소외 매장 리뷰 적립금 — `POST /api/reviews`\n"
        "2. '여유롭게 2시간' 필터 — `GET /api/cafes?stay_hours=2`\n"
        "3. '카공 가능' 퀵필터 — `GET /api/cafes?cagong=true`\n\n"
        "**데이터 출처**\n"
        "- 위치/카페 목록: 카카오 로컬 API\n"
        "- 이동시간: 카카오모빌리티 자동차 길찾기 (도보는 제휴 전용이라 추정식)\n"
        "- 영업시간: 비짓제주 Open API / CSV 수동검증 (`hours_confidence`로 표기)\n"
        "- 카공 인프라: 유저·점주 크라우드소싱 (`POST /api/reports`)\n\n"
        "**인증**\n"
        "구글/카카오 소셜 로그인은 Supabase Auth가 프론트에서 처리하고, "
        "여기서는 발급된 JWT를 검증한다. 리뷰·제보는 로그인 필수.\n"
        "요청 헤더: `Authorization: Bearer <supabase access_token>`"
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(cafes.router)
app.include_router(reviews.router)
app.include_router(reports.router)
app.include_router(users.router)
app.include_router(stats.router)


@app.get("/health", tags=["meta"])
def health():
    """DB가 진짜 살아있는지까지 확인한다. 데모 직전 30초 점검용."""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "db": engine.url.get_backend_name(),          # postgresql / sqlite
        "db_host": engine.url.host,
        "db_reachable": db_ok,
        "auth_required": settings.auth_required,
        "supabase_configured": bool(settings.supabase_url and settings.supabase_anon_key),
    }


@app.get("/api/meta/hotspots", tags=["meta"], summary="과밀 핫스팟 좌표 (지도 오버레이용)")
def hotspots():
    return {"items": [{"name": n, "lat": la, "lng": lo} for n, la, lo in HOTSPOTS]}
