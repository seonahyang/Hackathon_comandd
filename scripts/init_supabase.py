"""Supabase 초기 세팅을 한 방에 끝낸다.

    python -m scripts.init_supabase

하는 일
  1) 테이블 생성 + 모델에 새로 생긴 컬럼 반영 (scripts/migrate)
  2) scripts/supabase_rls.sql 적용 — RLS 잠금 + 인덱스
  3) 결과 요약 출력

여러 번 돌려도 안전합니다(전부 IF NOT EXISTS / 멱등).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text  # noqa: E402

from app.database import DATABASE_URL, IS_SQLITE, Base, engine  # noqa: E402
from app.models import (  # noqa: E402,F401  — import 해야 Base에 등록됨
    Cafe,
    CagongReport,
    PointLedger,
    Review,
    RouteCache,
    User,
)

SQL_FILE = Path(__file__).with_name("supabase_rls.sql")
OK, NG = "  [OK]  ", "  [실패] "


def load_statements(path: Path) -> list[str]:
    """SQL 파일에서 실행할 구문만 뽑아낸다.

    주석(--)을 먼저 걷어낸 뒤 세미콜론으로 자른다. 순서를 반대로 하면
    주석 안의 세미콜론이나 주석 처리해둔 정책 블록이 섞여 들어온다.
    """
    body = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("--")
    )
    return [s.strip() for s in body.split(";") if s.strip()]


def main() -> int:
    print("\n대상 DB:", DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL)

    if IS_SQLITE:
        print(NG, "지금 SQLite에 연결돼 있습니다. .env 의 DATABASE_URL을")
        print("         Supabase Session pooler 주소로 바꾼 뒤 다시 실행하세요.")
        return 1

    # 1) 테이블 -------------------------------------------------------
    # create_all 은 '없는 테이블'만 만든다. 이미 있는 테이블에 모델 컬럼이 추가된
    # 경우는 손대지 않아서, 나중에 UndefinedColumn 에러로 터진다. migrate 가 그걸 메운다.
    print("\n1) 테이블 생성 + 컬럼 동기화")
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as e:  # noqa: BLE001
        print(NG, f"연결 실패: {type(e).__name__}: {str(e)[:200]}")
        print("         python -m scripts.check_supabase 로 원인을 확인하세요.")
        return 1

    from scripts.migrate import main as migrate_main

    if migrate_main([]) != 0:
        print(NG, "컬럼 동기화 실패. 위 메시지를 확인하세요.")
        return 1

    tables = sorted(inspect(engine).get_table_names())
    print(OK, f"테이블 {len(tables)}개: {', '.join(tables)}")

    # 2) RLS ----------------------------------------------------------
    print("\n2) RLS 잠금 + 인덱스 적용")
    statements = load_statements(SQL_FILE)

    applied, failed = 0, 0
    with engine.begin() as conn:
        for stmt in statements:
            try:
                conn.execute(text(stmt))
                applied += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                head = " ".join(stmt.split())[:70]
                print(NG, f"{head}... → {type(e).__name__}: {str(e)[:120]}")

    print(OK, f"{applied}개 구문 적용" + (f", {failed}개 실패" if failed else ""))

    # 3) 검증 ---------------------------------------------------------
    print("\n3) RLS 상태 확인")
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' "
            "ORDER BY relname"
        )).all()

    unlocked = [name for name, rls in rows if not rls]
    for name, rls in rows:
        print(f"       {'🔒' if rls else '⚠️ '} {name}")

    if unlocked:
        print(NG, f"RLS가 꺼진 테이블: {', '.join(unlocked)}")
        print("         anon key만 있으면 외부에서 읽힙니다. 대시보드 SQL Editor에서")
        print("         scripts/supabase_rls.sql 을 직접 실행해주세요.")
        return 1

    print("\n" + OK, "완료. 다음: python -m scripts.seed  (데모 카페 시드)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
