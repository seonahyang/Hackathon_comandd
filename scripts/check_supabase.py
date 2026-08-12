"""Supabase 연결 상태를 한 번에 점검한다.

    python -m scripts.check_supabase

DB 연결 / 테이블 존재 / Auth JWKS 도달 가능 여부를 순서대로 확인하고,
막히는 지점마다 정확히 뭘 고쳐야 하는지 알려준다.
"""

import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings          # noqa: E402
from app.database import engine          # noqa: E402

OK, NG, INFO = "  [OK]  ", "  [실패] ", "  [안내] "
EXPECTED_TABLES = ["cafes", "users", "reviews", "point_ledger",
                   "cagong_reports", "route_cache"]


def check_url_shape() -> bool:
    print("\n1) DATABASE_URL 형식")
    url = settings.database_url

    if url.startswith("sqlite"):
        print(INFO, "지금 SQLite를 쓰고 있습니다.")
        print("         Supabase로 바꾸려면 .env 의 DATABASE_URL을 교체하세요:")
        print("         대시보드 상단 [Connect] > Session pooler > URI 복사")
        return False

    if url.startswith("https://"):
        print(NG, "프로젝트 URL을 넣으셨습니다. 이건 DB 접속 주소가 아닙니다.")
        print("         [Connect] > Session pooler 탭의 postgresql:// 주소가 필요합니다.")
        return False

    if url.startswith("postgres://"):
        print(NG, "postgres:// → postgresql:// 로 바꿔주세요 (SQLAlchemy 요구사항)")
        return False

    if not url.startswith("postgresql"):
        print(NG, f"알 수 없는 형식: {url[:40]}...")
        return False

    parsed = urlparse(url)
    host = parsed.hostname or ""
    if "[" in url or "]" in url:
        print(NG, "대괄호 [] 가 남아있습니다. Supabase UI의 자리표시자이니 지우세요.")
        return False

    print(OK, f"형식 정상 (host={host}, port={parsed.port})")

    if host.startswith("db.") and host.endswith(".supabase.co"):
        print(INFO, "⚠️ 직접 연결(db.*) 주소입니다. IPv6 전용이라 국내망에서 자주 실패합니다.")
        print("         연결이 안 되면 Session pooler 주소(pooler.supabase.com)로 바꾸세요.")
    return True


def check_dns() -> bool:
    print("\n2) DNS 조회")
    host = urlparse(settings.database_url).hostname
    if not host:
        print(NG, "호스트를 읽을 수 없습니다")
        return False
    try:
        infos = socket.getaddrinfo(host, None)
        families = {("IPv6" if i[0] == socket.AF_INET6 else "IPv4") for i in infos}
        print(OK, f"{host} → {', '.join(sorted(families))}")
        if families == {"IPv6"}:
            print(INFO, "IPv6 주소만 나옵니다. IPv4만 되는 망이면 연결이 실패합니다.")
            print("         → Session pooler 주소를 쓰세요.")
        return True
    except socket.gaierror as e:
        print(NG, f"{host} 조회 실패: {e}")
        print("         → 호스트명 오타이거나 IPv6 전용 주소입니다.")
        print("         → 대시보드 [Connect] > Session pooler 의 주소로 교체하세요.")
        return False


def check_connect() -> bool:
    print("\n3) DB 접속")
    try:
        with engine.connect() as conn:
            ver = conn.execute(text("select version()")).scalar()
        print(OK, str(ver)[:70])
        return True
    except Exception as e:  # noqa: BLE001 - 원인별 안내가 목적
        msg = str(e)
        print(NG, msg.split("\n")[0][:160])
        if "password authentication failed" in msg:
            print("         → 비밀번호가 틀렸습니다. 특수문자는 URL 인코딩 필요 (^ → %5E, @ → %40)")
        elif "Name or service not known" in msg or "could not translate" in msg:
            print("         → 호스트 조회 실패. Session pooler 주소로 바꾸세요.")
        elif "timeout" in msg.lower():
            print("         → 방화벽 또는 IPv6 문제. Session pooler(포트 5432/6543) 사용")
        elif "Tenant or user not found" in msg:
            print("         → pooler 사용자명은 postgres 가 아니라 postgres.<프로젝트ref> 형식입니다")
        return False


def check_tables() -> bool:
    print("\n4) 테이블")
    try:
        names = set(inspect(engine).get_table_names())
    except Exception as e:  # noqa: BLE001
        print(NG, str(e)[:120])
        return False

    missing = [t for t in EXPECTED_TABLES if t not in names]
    if missing:
        print(NG, f"없는 테이블: {', '.join(missing)}")
        print("         → python -m scripts.seed 를 실행하면 자동 생성됩니다")
        return False

    print(OK, f"{len(EXPECTED_TABLES)}개 테이블 모두 존재")
    try:
        with engine.connect() as conn:
            for t in ("cafes", "users", "reviews"):
                n = conn.execute(text(f"select count(*) from {t}")).scalar()
                print(f"         {t}: {n}건")
    except Exception:  # noqa: BLE001
        pass
    return True


def check_auth() -> bool:
    print("\n5) Supabase Auth (구글/카카오 로그인)")
    if not settings.supabase_url:
        print(NG, "SUPABASE_URL 이 .env 에 없습니다")
        print("         대시보드 > Settings > API > Project URL (https://xxxx.supabase.co)")
        return False
    try:
        r = httpx.get(settings.jwks_url, timeout=10.0)
    except httpx.HTTPError as e:
        print(NG, f"JWKS 요청 실패: {e}")
        return False

    if r.status_code != 200:
        print(NG, f"JWKS {r.status_code}")
        return False

    keys = r.json().get("keys", [])
    if keys:
        algs = {k.get("alg") for k in keys}
        print(OK, f"공개키 {len(keys)}개 ({', '.join(sorted(a for a in algs if a))})")
        print(INFO, "비대칭 키 방식입니다. SUPABASE_JWT_SECRET 없이 검증됩니다.")
    else:
        print(INFO, "JWKS에 키가 없습니다 = 레거시 HS256 프로젝트입니다.")
        if settings.supabase_jwt_secret:
            print(OK, "SUPABASE_JWT_SECRET 설정됨")
        else:
            print(NG, "SUPABASE_JWT_SECRET 가 필요합니다")
            print("         대시보드 > Settings > API > JWT Secret")
            return False
    return True


def main():
    print("=" * 58)
    print("Supabase 점검")
    print("=" * 58)

    shape = check_url_shape()
    dns = check_dns() if shape else False
    conn = check_connect() if dns else False
    tables = check_tables() if conn else False
    auth = check_auth()

    print("\n" + "=" * 58)
    for name, ok in [("DATABASE_URL 형식", shape), ("DNS", dns),
                     ("DB 접속", conn), ("테이블", tables), ("Auth JWKS", auth)]:
        print(f"  {name:<20} {'정상' if ok else '확인 필요'}")
    print("=" * 58)

    if not shape:
        print("\n먼저 .env 의 DATABASE_URL 을 Session pooler 주소로 바꾸세요.")
    elif conn and not tables:
        print("\n다음 단계: python -m scripts.seed")
    elif tables and auth:
        print("\n준비 완료. AUTH_REQUIRED=true 로 두고 프론트 로그인을 붙이면 됩니다.")
        print("프론트가 아직이면 .env 에 AUTH_REQUIRED=false 로 두고 개발하세요.")


if __name__ == "__main__":
    main()
