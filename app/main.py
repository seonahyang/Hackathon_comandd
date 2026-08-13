from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import ENV_PROBLEMS, settings
from .core.geo import HOTSPOTS
from .database import IS_SERVERLESS, Base, SessionLocal, engine
from .routers import ai, auth, cafes, receipts, reports, reviews, stats, users

# 설정 문제를 서버 뜰 때 눈에 띄게 알린다. 조용히 넘어가면 화면이 이상해진 뒤에야
# 알게 되고, 그때는 원인이 .env 라는 걸 떠올리기 어렵다.
if ENV_PROBLEMS:
    print("\n" + "=" * 62)
    print("  ⚠️  .env 확인이 필요합니다")
    for _p in ENV_PROBLEMS:
        print(f"     · {_p}")
    print("=" * 62 + "\n")

# 서버리스에서는 건너뛴다. 콜드스타트마다 DB에 스키마를 확인하러 가면
# 첫 요청이 느려지고, 인스턴스가 동시에 뜨면 불필요한 커넥션이 몰린다.
# 테이블은 배포 전에 scripts/migrate 로 이미 만들어둔다.
if not IS_SERVERLESS:
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

# --- CORS -------------------------------------------------------------------
# 프론트는 백엔드와 다른 출처에서 돈다(Vite 5173, 목업은 file://). 브라우저가
# 기본으로 막기 때문에 여기서 열어줘야 한다.
#
# allow_credentials 를 False 로 둔 이유:
#   우리는 쿠키 세션을 안 쓴다. Supabase 토큰을 Authorization 헤더로 보낸다.
#   그런데 allow_credentials=True 이면 브라우저가 `Access-Control-Allow-Origin: *`
#   응답을 거부한다(스펙상 credentials 모드에서는 와일드카드 금지). 쓰지도 않는
#   쿠키 때문에 CORS_ORIGINS=* 가 통째로 깨지는 셈이라 False 가 맞다.
#   Authorization 헤더는 credentials 없이도 정상 전송된다.
_cors = {
    "allow_credentials": False,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
    # 프론트가 응답 헤더를 읽어야 할 때 대비 (페이지네이션 등 확장 여지)
    "expose_headers": ["*"],
    "max_age": 3600,        # preflight 캐시 — 지도 이동마다 OPTIONS 왕복 방지
}

if settings.cors_allow_all:
    app.add_middleware(CORSMiddleware, allow_origins=["*"], **_cors)
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_list,
        allow_origin_regex=settings.cors_localhost_regex,
        **_cors,
    )

app.include_router(auth.router)
app.include_router(cafes.router)
app.include_router(reviews.router)
app.include_router(reports.router)
app.include_router(receipts.router)
app.include_router(users.router)
app.include_router(stats.router)
app.include_router(ai.router)


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


# --- 프론트 서빙 ------------------------------------------------------------
# 폴더를 통째로 mount 하지 않는다. 프로젝트 루트를 열면 http://…/.env 로
# DB 비밀번호가 그대로 읽힌다. 실제로 자주 나는 사고라 파일을 하나씩 지정한다.
ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
MOCKUP = ROOT / "카페맵.dc.html"
MOCKUP_ASSETS = {"support.js", "image-slot.js", "ios-frame.jsx"}

if WEB_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=WEB_DIR, html=True), name="ui")

if MOCKUP.exists():
    # 목업 HTML 은 ./support.js, _ds/… 를 상대경로로 부른다.
    # 그래서 반드시 /mockup/ (슬래시 포함) 아래에서 서빙해야 경로가 맞는다.
    for sub in ("_ds", "uploads"):
        if (ROOT / sub).is_dir():
            app.mount(f"/mockup/{sub}", StaticFiles(directory=ROOT / sub), name=f"mk_{sub}")

    @app.get("/mockup", include_in_schema=False)
    def mockup_redirect(request: Request):
        return RedirectResponse("/mockup/" + _qs(request))

    # 개발 중에는 캐시를 끈다. 안 그러면 HTML 을 고쳐도 브라우저가 예전 걸 계속
    # 보여줘서 "코드는 고쳤는데 화면이 그대로"인 상황에 시간을 태운다.
    NO_CACHE = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    }

    @app.get("/mockup/", include_in_schema=False)
    def mockup_page():
        return FileResponse(MOCKUP, headers=NO_CACHE)

    @app.get("/mockup/{name}", include_in_schema=False)
    def mockup_asset(name: str):
        # 허용 목록에 없는 이름은 거부한다. 안 그러면 /mockup/.env 로 새어나간다.
        if name not in MOCKUP_ASSETS:
            raise HTTPException(404)
        return FileResponse(ROOT / name, headers=NO_CACHE)


def _qs(request: Request) -> str:
    """리다이렉트할 때 쿼리스트링을 그대로 넘긴다.

    이게 없으면 OAuth 로그인이 깨진다.
    Supabase 는 인증 후 `/?code=...` 로 돌려보내는데, 쿼리를 버리고 `/mockup/` 로
    보내면 그 code 가 사라져서 세션 교환이 아예 일어나지 않는다.
    화면상으로는 '로그인 창이 번쩍하고 원래 화면으로 돌아오는' 것처럼 보인다.

    (해시(#access_token=...)는 브라우저가 서버로 보내지 않고 리다이렉트에도
     그대로 따라가므로 여기서 신경 쓸 필요가 없다)
    """
    q = request.url.query
    return f"?{q}" if q else ""


@app.get("/", include_in_schema=False)
def root(request: Request):
    dest = "/mockup/" if MOCKUP.exists() else "/ui/"
    return RedirectResponse(dest + _qs(request))


@app.get("/api/meta/build", tags=["meta"], summary="지금 서버가 내보내는 프론트 파일 정보")
def build_info():
    """'코드는 고쳤는데 화면이 그대로'일 때 어느 쪽 문제인지 가른다.

    integrated 가 true 인데 화면이 예전 그대로면 → 브라우저 캐시 (Ctrl+Shift+R)
    integrated 가 false 면 → 서버가 다른 폴더의 파일을 보고 있다
    """
    import datetime

    def info(path):
        if not path.exists():
            return {"exists": False}
        text = path.read_text(encoding="utf-8", errors="replace")
        return {
            "exists": True,
            "path": str(path),
            "bytes": path.stat().st_size,
            "modified": datetime.datetime.fromtimestamp(
                path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            # 연동 코드가 실제로 들어있는 파일인지 표시
            "integrated": all(k in text for k in ("loadStores", "kakao-map", "mapWarn")),
        }

    return {"mockup": info(MOCKUP), "ui": info(WEB_DIR / "index.html")}


@app.get("/api/meta/db", tags=["meta"], summary="DB에 실제로 뭐가 저장돼 있는지")
def db_info():
    """'리뷰가 저장되긴 한 건가?'를 브라우저에서 바로 확인한다.

    db_host 가 supabase 주소면 Supabase 에 쌓이고 있는 것이고,
    sqlite 면 로컬 파일에 쌓이는 중이라 대시보드에는 안 보인다.
    """
    from sqlalchemy import func, select

    from .models import Cafe, CagongReport, PointLedger, Review, User

    out = {
        "db": engine.url.get_backend_name(),
        "db_host": engine.url.host,
        "supabase": bool(engine.url.host and "supabase" in str(engine.url.host)),
    }
    try:
        with SessionLocal() as db:
            for name, model in (("cafes", Cafe), ("users", User), ("reviews", Review),
                                ("point_ledger", PointLedger), ("reports", CagongReport)):
                out[name] = db.scalar(select(func.count()).select_from(model)) or 0

            out["recent_users"] = [
                {"id": u.id, "nickname": u.nickname, "provider": u.provider,
                 "point_balance": u.point_balance}
                for u in db.scalars(select(User).order_by(User.id.desc()).limit(5))
            ]
            out["recent_reviews"] = [
                {"id": r.id, "cafe_id": r.cafe_id, "user_id": r.user_id,
                 "rating": r.rating, "earned_point": r.earned_point}
                for r in db.scalars(select(Review).order_by(Review.id.desc()).limit(5))
            ]
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"

    return out


@app.get("/api/meta/cors", tags=["meta"], summary="CORS 설정 확인 (프론트 디버깅용)")
def cors_info():
    """'왜 CORS 에러가 나지?' 할 때 제일 먼저 열어보는 곳."""
    return {
        "allow_all": settings.cors_allow_all,
        "allow_origins": settings.cors_list,
        "allow_origin_regex": None if settings.cors_allow_all
        else settings.cors_localhost_regex,
        "allow_credentials": False,
        "hint": (
            "쿠키를 안 쓰므로 credentials=false 입니다. 프론트에서 fetch 할 때 "
            "credentials:'include' 를 넣으면 오히려 차단됩니다. 토큰은 "
            "Authorization 헤더로만 보내세요."
        ),
    }
