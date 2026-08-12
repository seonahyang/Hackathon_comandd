"""DB 엔진 — Supabase(PostgreSQL) 기본, SQLite는 오프라인 폴백.

Supabase 연결에서 팀이 가장 많이 막히는 3가지를 여기서 자동으로 처리한다.

1) `postgres://` 스킴
   Supabase가 복사해주는 URI는 `postgresql://` 이지만 일부 호스팅/문서는
   `postgres://` 를 준다. SQLAlchemy 2.x는 이걸 모른다 → 자동 변환.

2) SSL
   Supabase는 SSL 없는 접속을 끊는다. sslmode가 없으면 require를 붙인다.

3) 커넥션 풀링
   Session pooler(pgbouncer, 포트 5432) 뒤에 붙기 때문에 오래 놀던 커넥션이
   조용히 끊겨 있는 경우가 많다. pool_pre_ping + pool_recycle 로 방어한다.
   해커톤 데모 중 "갑자기 500 뜨는" 대부분의 원인이 이것.
"""

import os
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


def _normalize(url: str) -> str:
    """드라이버 스킴 정리 + sslmode 보정."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]

    if url.startswith("postgresql") and "sslmode=" not in url:
        parts = urlsplit(url)
        query = f"{parts.query}&sslmode=require" if parts.query else "sslmode=require"
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))

    return url


DATABASE_URL = _normalize(settings.database_url)
IS_SQLITE = DATABASE_URL.startswith("sqlite")

# Vercel 등 서버리스 위에서 도는지. 커넥션 관리 방식이 완전히 달라진다.
IS_SERVERLESS = bool(os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"))

if IS_SQLITE:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _sqlite_enforce_fk(dbapi_conn, _record):
        """SQLite 도 외래키를 검사하게 만든다.

        SQLite 는 기본값이 검사 꺼짐이라, 참조가 걸린 행을 지워도 그냥 넘어간다.
        그러다 Supabase(PostgreSQL)에 올리는 순간 ForeignKeyViolation 으로 터진다.
        로컬에서 통과한 코드가 배포에서만 깨지는 걸 막으려고 여기서 맞춰둔다.
        """
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
elif IS_SERVERLESS:
    # Vercel 같은 서버리스에서는 인스턴스가 수시로 생겼다 사라진다.
    # 인스턴스마다 커넥션 풀을 들고 있으면 Supabase 커넥션 한도를 금방 넘긴다.
    # (인스턴스 20개 × pool_size 5 = 100 커넥션)
    # NullPool 은 요청이 끝나면 커넥션을 바로 반납한다. 풀링은 Supabase 의
    # pooler 가 이미 해주고 있으므로 우리가 또 할 이유가 없다.
    from sqlalchemy.pool import NullPool

    engine = create_engine(
        DATABASE_URL,
        poolclass=NullPool,
        connect_args={"connect_timeout": 10, "application_name": "jeju-cagong-vercel"},
        echo=False,
    )
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,      # 죽은 커넥션을 쓰기 전에 걸러낸다
        pool_recycle=300,        # pooler가 끊기 전에 우리가 먼저 버린다 (5분)
        pool_size=5,
        max_overflow=5,
        connect_args={
            "connect_timeout": 10,          # 네트워크가 막혔을 때 무한 대기 방지
            "application_name": "jeju-cagong-api",  # Supabase 대시보드에서 식별용
        },
        echo=False,
    )

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
