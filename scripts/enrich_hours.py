"""카페 영업시간을 정확한 값으로 채워 넣는다. ('여유롭게 2시간' 정확도의 핵심)

세 가지 경로를 지원한다. 위에서부터 우선순위가 높다.

  1) CSV 수동 입력   python -m scripts.enrich_hours --csv data/hours_override.csv
     → 가장 확실하다. 데모에 쓸 카페 20~30개만 직접 채우면 발표는 완벽해진다.
  2) 비짓제주 Open API  python -m scripts.enrich_hours --api
     → 키 승인이 나면 사용. 먼저 --dump 로 응답 필드명 확인 권장.
  3) 아무것도 없으면 브랜드별 통계 추정치 유지 (hours_confidence='low')

사용 예:
    python -m scripts.enrich_hours --dump          # API 응답 원본 3건 출력
    python -m scripts.enrich_hours --api
    python -m scripts.enrich_hours --csv data/hours_override.csv
    python -m scripts.enrich_hours --report        # 현재 영업시간 신뢰도 분포
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings                        # noqa: E402
from app.core.geo import haversine_km                  # noqa: E402
from app.database import Base, SessionLocal, engine    # noqa: E402
from app.models import Cafe                            # noqa: E402
from app.services.hours_parser import parse_hours      # noqa: E402
from app.services.visitjeju import VisitJejuClient, normalize  # noqa: E402

MATCH_RADIUS_KM = 0.5  # 이름이 같아도 500m 넘게 떨어지면 다른 가게로 본다


def norm_name(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isalnum()).lower()


def apply_hours(cafe: Cafe, hours_text: str, source: str) -> bool:
    parsed = parse_hours(hours_text)
    if parsed["confidence"] == "low":
        return False
    cafe.open_time = parsed["open_time"]
    cafe.close_time = parsed["close_time"]
    cafe.last_order_min = parsed["last_order_min"]
    if parsed["closed_days"]:
        cafe.closed_days = parsed["closed_days"]
    cafe.hours_text = hours_text[:255]
    cafe.hours_source = source
    cafe.hours_confidence = parsed["confidence"]
    return True


# ---------------------------------------------------------------- CSV
def run_csv(path: str):
    p = Path(path)
    if not p.exists():
        print(f"파일이 없습니다: {p}")
        print("→ data/hours_override.csv 템플릿을 채워서 다시 실행하세요.")
        sys.exit(1)

    db = SessionLocal()
    updated = skipped = 0
    try:
        with p.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                name = (row.get("name") or "").strip()
                hours_text = (row.get("hours_text") or "").strip()
                if not name or not hours_text:
                    continue

                cafe = db.query(Cafe).filter(Cafe.name == name).first()
                if not cafe:
                    target = norm_name(name)
                    cafe = next(
                        (c for c in db.query(Cafe).all() if norm_name(c.name) == target), None
                    )
                if not cafe:
                    print(f"  ? 매칭 실패: {name}")
                    skipped += 1
                    continue

                if apply_hours(cafe, hours_text, "csv"):
                    cafe.hours_confidence = "high"  # 사람이 직접 확인한 값
                    updated += 1
                    print(f"  + {cafe.name}: {cafe.open_time}~{cafe.close_time} "
                          f"(LO -{cafe.last_order_min}분, 휴무 {cafe.closed_days or '없음'})")
                else:
                    print(f"  ! 파싱 실패: {name} <- {hours_text!r}")
                    skipped += 1
        db.commit()
    finally:
        db.close()
    print(f"\nCSV 반영 완료: {updated}건 갱신 / {skipped}건 실패")


# ---------------------------------------------------------------- API
def run_api(dump_only: bool = False):
    key = getattr(settings, "visitjeju_api_key", "")
    if not key:
        print("VISITJEJU_API_KEY 가 .env 에 없습니다.")
        print("→ https://www.visitjeju.net/kr/visitjejuapi 에서 신청 (담당자 승인 필요)")
        print("→ 키를 기다리는 동안에는: python -m scripts.enrich_hours --csv data/hours_override.csv")
        sys.exit(1)

    client = VisitJejuClient(key)
    db = SessionLocal()
    matched = updated = seen = 0
    try:
        cafes = db.query(Cafe).all()
        for cat, item in client.iter_items():
            seen += 1
            if dump_only:
                print(f"\n--- category={cat} 원본 ---")
                print(json.dumps(item, ensure_ascii=False, indent=2)[:1500])
                if seen >= 3:
                    print("\n위 키 이름을 app/services/visitjeju.py 의 *_KEYS 목록에 맞춰주세요.")
                    return
                continue

            rec = normalize(item)
            if not rec["name"] or not rec["hours_text"]:
                continue

            target = norm_name(rec["name"])
            best = None
            for c in cafes:
                if norm_name(c.name) != target:
                    continue
                if rec["lat"] and rec["lng"]:
                    if haversine_km(rec["lat"], rec["lng"], c.lat, c.lng) > MATCH_RADIUS_KM:
                        continue
                best = c
                break
            if not best:
                continue

            matched += 1
            best.visitjeju_id = rec["visitjeju_id"]
            if apply_hours(best, rec["hours_text"], "visitjeju"):
                updated += 1
                print(f"  + {best.name}: {best.open_time}~{best.close_time} <- {rec['hours_text']!r}")
        db.commit()
    except PermissionError as e:
        print(f"인증 실패: {e}  (apiKey 확인)")
        sys.exit(1)
    finally:
        client.close()
        db.close()
    print(f"\nAPI 반영 완료: 조회 {seen}건 / 이름·좌표 매칭 {matched}건 / 시간 갱신 {updated}건")


# ---------------------------------------------------------------- 리포트
def run_report():
    db = SessionLocal()
    try:
        total = db.query(Cafe).count()
        rows = {}
        for c in db.query(Cafe).all():
            key = (c.hours_source, c.hours_confidence)
            rows[key] = rows.get(key, 0) + 1
        print(f"전체 {total}건\n")
        print(f"{'출처':<12}{'신뢰도':<10}{'건수':>6}")
        print("-" * 30)
        for (src, conf), n in sorted(rows.items(), key=lambda x: -x[1]):
            print(f"{src:<12}{conf:<10}{n:>6}")
        high = sum(n for (s, c), n in rows.items() if c == "high")
        print(f"\n영업시간 신뢰 가능(high): {high}/{total} ({high / total * 100:.0f}%)"
              if total else "")
        print("→ 데모에 쓸 카페는 CSV로 채워서 high 로 만들어두는 게 안전합니다.")
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", metavar="PATH", help="CSV 수동 입력 반영")
    ap.add_argument("--api", action="store_true", help="비짓제주 Open API 반영")
    ap.add_argument("--dump", action="store_true", help="API 응답 원본 3건만 출력")
    ap.add_argument("--report", action="store_true", help="영업시간 신뢰도 분포")
    args = ap.parse_args()

    Base.metadata.create_all(bind=engine)

    if args.dump:
        run_api(dump_only=True)
    elif args.api:
        run_api()
    elif args.csv:
        run_csv(args.csv)
    elif args.report:
        run_report()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
