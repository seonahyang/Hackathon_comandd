"""모델에 새로 생긴 컬럼을 실제 DB에 반영한다.

    python -m scripts.migrate            # 뭘 바꿀지 보여주고 실행
    python -m scripts.migrate --dry-run  # 보기만

왜 필요한가
-----------
SQLAlchemy 의 `create_all()` 은 **없는 테이블만 만든다.** 이미 있는 테이블에
컬럼이 추가돼도 손대지 않는다. 그래서 모델에 필드를 추가하고 서버를 다시 띄우면
조용히 넘어갔다가, 쿼리할 때 이렇게 터진다.

    psycopg2.errors.UndefinedColumn: column cafes.source_key does not exist

Alembic 을 붙이는 게 정석이지만 해커톤 일정에는 과하다. 이 스크립트는
'모델에는 있는데 DB에는 없는 컬럼'을 찾아 ALTER TABLE 로 채워 넣는다.
기존 데이터는 보존되고, 여러 번 돌려도 안전하다.

한계: 컬럼 삭제·타입 변경·이름 변경은 하지 않는다. 추가만 한다.
     (해커톤에서 필요한 건 사실상 추가뿐이고, 나머지는 실수로 데이터를 날린다)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.schema import CreateColumn, CreateIndex  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.models import (  # noqa: E402,F401  — import 해야 Base.metadata 에 등록된다
    Cafe,
    CagongReport,
    PointLedger,
    Review,
    RouteCache,
    User,
)

OK, NG, ADD = "  [OK]  ", "  [실패] ", "  [추가] "


def column_ddl(col) -> str:
    """ALTER TABLE ... ADD COLUMN 뒤에 붙일 정의를 만든다."""
    ddl = str(CreateColumn(col).compile(engine)).strip()

    # 이미 행이 있는 테이블에 NOT NULL 컬럼을 그냥 붙이면 거부당한다.
    # 모델에 파이썬 기본값이 있으면 그걸 DEFAULT 로 내려서 기존 행을 채운다.
    if not col.nullable and col.server_default is None:
        default = None
        if col.default is not None and not callable(getattr(col.default, "arg", None)):
            default = col.default.arg

        if default is None:
            # 채울 값이 없으면 NULL 허용으로 낮춘다. 데이터를 지우는 것보다 낫다.
            ddl = ddl.replace(" NOT NULL", "")
        elif isinstance(default, bool):
            ddl += f" DEFAULT {'true' if default else 'false'}"
        elif isinstance(default, str):
            ddl += " DEFAULT '{}'".format(default.replace("'", "''"))
        else:
            ddl += f" DEFAULT {default}"

    return ddl


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="변경 없이 계획만 출력")
    args = ap.parse_args(argv)

    if argv is None:      # 단독 실행일 때만 제목을 찍는다 (init_supabase 안에서는 생략)
        print("\n" + "=" * 58)
        print("  스키마 마이그레이션")
        print("=" * 58)

    try:
        insp = inspect(engine)
        db_tables = set(insp.get_table_names())
    except Exception as e:  # noqa: BLE001
        print(NG, f"DB 연결 실패: {type(e).__name__}: {str(e)[:150]}")
        print("         python -m scripts.check_supabase 로 원인을 확인하세요.")
        return 1

    plan: list[tuple[str, str]] = []      # (설명, SQL)
    missing_tables = []

    for name, table in Base.metadata.tables.items():
        if name not in db_tables:
            missing_tables.append(name)
            continue

        have = {c["name"] for c in insp.get_columns(name)}
        for col in table.columns:
            if col.name not in have:
                plan.append((
                    f"{name}.{col.name}",
                    f"ALTER TABLE {name} ADD COLUMN {column_ddl(col)}",
                ))

    if missing_tables:
        print(f"  없는 테이블 {len(missing_tables)}개는 create_all 로 만듭니다: "
              f"{', '.join(missing_tables)}")

    if not plan and not missing_tables:
        print(OK, "스키마 최신 상태 — 추가할 컬럼 없음")
        return 0

    print(f"  추가할 컬럼 {len(plan)}개")
    for label, sql in plan:
        print(f"{ADD}{label:<28} {sql.split('ADD COLUMN ')[-1][:60]}")

    if args.dry_run:
        print("\n  [dry-run] DB는 건드리지 않았습니다.")
        return 0

    # 1) 없는 테이블 생성
    Base.metadata.create_all(bind=engine)

    # 2) 컬럼 추가
    failed = 0
    with engine.begin() as conn:
        for label, sql in plan:
            try:
                conn.execute(text(sql))
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(NG, f"{label}: {type(e).__name__}: {str(e)[:120]}")

    # 3) 인덱스 — 새 컬럼에 걸린 인덱스는 위 ALTER 로 안 생긴다
    idx_added = 0
    with engine.begin() as conn:
        for table in Base.metadata.tables.values():
            existing = {i["name"] for i in inspect(engine).get_indexes(table.name)}
            for idx in table.indexes:
                if idx.name in existing:
                    continue
                try:
                    conn.execute(CreateIndex(idx))
                    idx_added += 1
                except Exception:  # noqa: BLE001,S110
                    pass   # 이미 있거나 제약조건으로 커버되는 경우 — 무시해도 안전

    print(f"{OK}컬럼 {len(plan) - failed}개 추가"
          + (f", {failed}개 실패" if failed else "")
          + (f" / 인덱스 {idx_added}개 생성" if idx_added else ""))

    if failed:
        return 1

    if argv is None:
        print("\n  다음:  python -m scripts.ingest_iddy --purge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
