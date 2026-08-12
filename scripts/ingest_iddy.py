"""제주 관광공사 음식점 데이터(엑셀) → cafes 테이블 적재.

    python -m scripts.ingest_iddy --purge          # 더미 지우고 실데이터로 교체
    python -m scripts.ingest_iddy                  # 기존 유지하고 추가/갱신
    python -m scripts.ingest_iddy --dry-run        # DB 안 건드리고 통계만

입력: data/iddy_fnb.xlsx  (시트 '이디_음식', 719건)

이 데이터에 있는 것 / 없는 것
-----------------------------
있음: 좌표(100%), 영업시간(99.6%), 브레이크타임(15%), 라스트오더(57%),
      주차(97.5%), 화장실(54%), 소개문(100%), 인허가번호(98%)
없음: 콘센트 / 와이파이 / 노트북 허용 / 좌석수 (좌석수는 719건 중 1건뿐)

→ 카공 인프라는 이 데이터로 알 수 없다. 이름 휴리스틱으로 초기 추정만 하고
  (cagong_source='estimated') 실제 값은 크라우드소싱 제보로 채운다.
  심사에서 "이 정보 어디서 났냐"고 물으면 이 구분을 그대로 답하면 된다.
"""

import argparse
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.cagong import guess_cagong  # noqa: E402
from app.core.geo import classify_remote, in_jeju  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Cafe  # noqa: E402
from app.services.hours_parser import (  # noqa: E402
    last_order_gap_min,
    parse_break,
    parse_hours,
)

XLSX = Path(__file__).resolve().parents[1] / "data" / "iddy_fnb.xlsx"
SHEET = "이디_음식"

# --- 카페 / 일반음식점 분류 --------------------------------------------------
# 이 데이터셋은 '음식점' 전체라 카페와 식당이 섞여 있다. 카공 추천의 기본 모수는
# 카페여야 하므로 갈라둔다. 완벽한 분류는 불가능하니 근거를 남기고(place_type),
# 애매하면 식당으로 보낸다 — 카페 목록에 갈치조림집이 뜨는 게 반대보다 나쁘다.
NAME_CAFE = (
    r"카페|까페|커피|coffee|로스터|roast|베이커리|bakery|디저트|dessert"
    r"|브런치|brunch|찻집|티하우스|다방|espresso|에스프레소|빵집|제과"
    r"|케이크|cake|브루잉|brew"
)
# 소개문 앞부분(첫 문장 언저리)에 나오면 그 가게의 정체를 말하는 것으로 본다.
# 뒤쪽에 나오는 '근처에 카페가 있다' 류와 구분하기 위해 80자로 자른다.
LEAD_CAFE = r"카페|커피|로스터|베이커리|디저트|브런치|찻집|코워킹|북라운지|북카페|작업실"
# 이름에 이게 박혀 있으면 소개문이 뭐라 하든 식당이다.
NAME_RESTAURANT = (
    r"갈치|흑돼지|근고기|횟집|회센터|식당|국수|해장|칼국수|짜장|중화|초밥|스시"
    r"|고깃집|구이|불고기|삼겹|보쌈|족발|찜|탕|전복죽|해물|물회|밀면|비빔밥"
    r"|한정식|뷔페|치킨|피자|버거|분식|떡볶이|김밥|라멘|우동|돈까스|돈가스"
)


def classify(name: str, summary: str) -> str:
    if re.search(NAME_CAFE, name, re.I):
        return "cafe"
    if re.search(NAME_RESTAURANT, name, re.I):
        return "restaurant"
    if re.search(LEAD_CAFE, summary[:80], re.I):
        return "cafe"
    return "restaurant"


# --- 값 정리 -----------------------------------------------------------------
def clean(v) -> str:
    """엑셀의 NaN / HTML 조각 / 중복 공백을 걷어낸다."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    s = re.sub(r"<[^>]{0,20}>", " ", str(v))
    return re.sub(r"\s+", " ", s).strip()


def parse_region(address: str) -> tuple[str | None, str | None]:
    """'제주특별자치도 서귀포시 태평로 353' → ('서귀포시', '태평로')"""
    m = re.search(r"제주특별자치도\s+(\S+시)\s+(\S+)", address)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def parse_parking(v: str) -> bool | None:
    s = clean(v)
    if not s:
        return None
    if s.startswith("불가"):
        return False
    return True if "가능" in s else None


def parse_toilet(v: str) -> bool | None:
    s = clean(v)
    if "화장실" not in s:
        return None
    return False if re.search(r"화장실\s*:\s*(없음|불가)", s) else True


def parse_phone(v: str) -> str | None:
    m = re.search(r"(0\d{1,3}[-\s]?\d{3,4}[-\s]?\d{4})", clean(v))
    return m.group(1) if m else None


def parse_license(v) -> str | None:
    """인허가번호는 엑셀에서 float로 읽혀 '19800631234.0' 꼴로 들어온다."""
    s = clean(v)
    if not s:
        return None
    try:
        return str(int(float(s)))
    except ValueError:
        return s[:32]


def make_source_key(name: str, lat: float, lng: float) -> str:
    """재적재용 자연키. 인허가번호를 못 믿어서 이름+좌표로 만든다."""
    slug = re.sub(r"\s+", "", name)[:100]
    return f"{slug}@{lat:.5f},{lng:.5f}"


# --- 본 처리 -----------------------------------------------------------------
def build_rows(df: pd.DataFrame) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    seen_keys: set[str] = set()
    stats = {
        "total": len(df), "skipped_coord": 0, "skipped_dup": 0,
        "cafe": 0, "restaurant": 0,
        "hours_high": 0, "hours_medium": 0, "hours_low": 0,
        "break": 0, "last_order": 0, "remote": 0, "closed_days": 0,
    }

    for _, r in df.iterrows():
        name = clean(r.get("명칭"))
        lat, lng = r.get("위도"), r.get("경도")

        if not name or pd.isna(lat) or pd.isna(lng) or not in_jeju(float(lat), float(lng)):
            stats["skipped_coord"] += 1
            continue

        lat, lng = float(lat), float(lng)

        key = make_source_key(name, lat, lng)
        if key in seen_keys:          # 엑셀 안에서 같은 가게가 두 번 나온 경우
            stats["skipped_dup"] += 1
            continue
        seen_keys.add(key)

        address = clean(r.get("주소"))
        summary = clean(r.get("개요"))
        region, district = parse_region(address)

        # 영업시간
        h = parse_hours(clean(r.get("영업시간")) or None)
        stats[f"hours_{h['confidence']}"] += 1
        if h["closed_days"]:
            stats["closed_days"] += 1

        # 라스트오더는 엑셀에 절대시각('22:00')으로 들어있다 → '마감 N분 전'으로 환산
        gap = last_order_gap_min(h["close_time"], clean(r.get("라스트 오더")) or None)
        if gap is not None:
            stats["last_order"] += 1

        bs, be = parse_break(clean(r.get("브레이크 타임")) or None)
        if bs:
            stats["break"] += 1

        dist, is_remote = classify_remote(lat, lng)
        if is_remote:
            stats["remote"] += 1

        place_type = classify(name, summary)
        stats[place_type] += 1

        cagong = guess_cagong(name, summary[:60])

        rows.append({
            "license_no": parse_license(r.get("인허가번호")),
            "source_key": make_source_key(name, lat, lng),
            "name": name[:120],
            "place_type": place_type,
            "category": "카페" if place_type == "cafe" else "음식점",
            "address": address[:255] or None,
            "road_address": address[:255] or None,
            "phone": parse_phone(r.get("문의 및 안내")),
            "lat": lat, "lng": lng,
            "region": region, "district": district,
            "open_time": h["open_time"][:5],
            "close_time": h["close_time"][:5],
            "last_order_min": gap if gap is not None else 30,
            "closed_days": h["closed_days"],
            "break_start": bs, "break_end": be,
            "hours_source": "visitjeju",
            "hours_text": (clean(r.get("영업시간")) or None),
            "hours_confidence": h["confidence"],
            "parking": parse_parking(r.get("주차 시설")),
            "has_toilet": parse_toilet(r.get("상세정보")),
            "summary": summary or None,
            "dist_to_hotspot_km": dist,
            "is_remote": is_remote,
            # 실데이터에는 리뷰가 없다. 0에서 시작하는 게 정직하고, 적립금 로직의
            # '소외 매장' 판정도 그래야 의미가 맞는다.
            "review_count": 0,
            "rating_avg": 0.0,
            **cagong,
        })

    return rows, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--purge", action="store_true",
                    help="기존 cafes 전체 삭제 후 적재 (리뷰·제보도 함께 삭제)")
    ap.add_argument("--dry-run", action="store_true", help="DB 안 건드리고 통계만")
    ap.add_argument("--only-cafe", action="store_true", help="카페만 적재")
    args = ap.parse_args()

    if not XLSX.exists():
        print(f"  [실패] 파일이 없습니다: {XLSX}")
        return 1

    df = pd.read_excel(XLSX, sheet_name=SHEET)
    rows, stats = build_rows(df)

    if args.only_cafe:
        rows = [r for r in rows if r["place_type"] == "cafe"]

    print("\n" + "=" * 58)
    print("  제주 음식점 데이터 적재")
    print("=" * 58)
    print(f"  원본           {stats['total']}건")
    print(f"  좌표 이상 제외   {stats['skipped_coord']}건")
    print(f"  파일 내 중복 제외 {stats['skipped_dup']}건")
    print(f"  적재 대상       {len(rows)}건  "
          f"(카페 {stats['cafe']} / 음식점 {stats['restaurant']})")
    print(f"  영업시간 신뢰도  high {stats['hours_high']} / "
          f"medium {stats['hours_medium']} / low {stats['hours_low']}")
    print(f"  브레이크타임     {stats['break']}건")
    print(f"  라스트오더 환산  {stats['last_order']}건")
    print(f"  휴무일 확인      {stats['closed_days']}건")
    print(f"  외곽지(5km↑)    {stats['remote']}건")

    if args.dry_run:
        print("\n  [dry-run] DB는 건드리지 않았습니다.")
        return 0

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if args.purge:
            from app.models import CagongReport, PointLedger, Review

            # 리뷰/제보는 cafe_id 외래키를 잡고 있어 카페보다 먼저 지워야 한다
            n_rev = db.query(Review).delete()
            n_rep = db.query(CagongReport).delete()
            db.query(PointLedger).update({"cafe_id": None, "review_id": None})
            n_cafe = db.query(Cafe).delete()
            db.commit()
            print(f"\n  기존 데이터 삭제: 카페 {n_cafe} / 리뷰 {n_rev} / 제보 {n_rep}")

        existing = {
            key: cid for key, cid in db.execute(
                select(Cafe.source_key, Cafe.id).where(Cafe.source_key.is_not(None))
            ).all()
        }

        created = updated = 0
        for row in rows:
            cafe_id = existing.get(row["source_key"])

            if cafe_id:
                cafe = db.get(Cafe, cafe_id)
                for k, v in row.items():
                    # 크라우드소싱으로 확정된 값은 공공데이터로 덮어쓰지 않는다.
                    # 현장 제보가 공식 데이터보다 최신인 경우가 많다.
                    if k in ("laptop_ok", "has_power", "has_wifi", "quiet",
                             "seat_count", "cagong_source", "review_count",
                             "rating_avg") and cafe.cagong_source in ("user", "owner"):
                        continue
                    setattr(cafe, k, v)
                updated += 1
            else:
                db.add(Cafe(**row))
                created += 1

        db.commit()
        total = db.query(Cafe).count()
        n_cafe = db.query(Cafe).filter(Cafe.place_type == "cafe").count()
        n_remote = db.query(Cafe).filter(Cafe.is_remote.is_(True)).count()
    finally:
        db.close()

    print(f"\n  신규 {created} / 갱신 {updated}")
    print(f"  DB 전체 {total}건 (카페 {n_cafe} / 외곽 {n_remote})")
    print("\n  다음:  uvicorn app.main:app --reload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
