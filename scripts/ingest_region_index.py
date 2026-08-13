"""지역 활성도 지수를 매장에 붙인다.

    python -m scripts.ingest_region_index --dry-run   # 매칭률만 확인
    python -m scripts.ingest_region_index             # 실제 반영

입력: data/region_index.csv
      (지역명, 내비게이션 건수, 방문객, 추정 소비액, 종합 지수, 분류 상태)

하는 일
-------
CSV 의 지역명(읍면동)을 Cafe.district 와 맞춰서 region_state / region_index /
region_rank 를 채운다. 이 값이 적립금 차등의 유일한 근거가 된다.

매칭이 왜 까다로운가
--------------------
공공데이터의 주소 표기가 일정하지 않다. '애월읍'과 '제주시 애월읍', '연동'과
'제주시 연동' 이 섞여 있고, 공백·괄호가 붙기도 한다. 그래서 정규화한 뒤
    1) 완전 일치
    2) district 안에 지역명이 들어있는지
    3) 주소(address) 안에 지역명이 들어있는지
순으로 3단계로 찾는다. 그래도 못 찾으면 '보통'으로 둔다 — 데이터가 없는 곳에
큰 보너스를 주면 그게 어뷰징 통로가 되기 때문이다.
"""

import argparse
import csv
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Cafe  # noqa: E402

CSV_PATH = ROOT / "data" / "region_index.csv"

# 침체 지역으로 보는 지수 상한. rewards.py 의 첫 구간과 맞춘다.
REMOTE_INDEX_MAX = 0.20

STATE_MAP = {"과밀": "과밀", "보통": "보통", "소멸": "침체", "침체": "침체"}


def norm(s: str) -> str:
    """비교용 정규화. 공백·괄호를 걷어낸다."""
    return re.sub(r"[\s()（）]", "", (s or "").strip())


def load_csv() -> list[dict]:
    if not CSV_PATH.exists():
        print(f"CSV 가 없습니다: {CSV_PATH}")
        sys.exit(1)
    rows = list(csv.DictReader(io.open(CSV_PATH, encoding="utf-8-sig")))
    out = []
    for i, r in enumerate(sorted(rows, key=lambda x: -float(x["종합 지수"])), start=1):
        out.append({
            "name": r["지역명"].strip(),
            "key": norm(r["지역명"]),
            "index": float(r["종합 지수"]),
            "state": STATE_MAP.get(r["분류 상태"].strip(), "보통"),
            "rank": i,
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="DB 에 쓰지 않고 매칭률만")
    args = ap.parse_args()

    regions = load_csv()
    by_key = {r["key"]: r for r in regions}
    print(f"CSV {len(regions)}개 지역 로드")
    for state in ("과밀", "보통", "침체"):
        n = sum(1 for r in regions if r["state"] == state)
        print(f"   {state} {n}곳")

    db = SessionLocal()
    cafes = db.scalars(select(Cafe)).all()
    print(f"\nDB 매장 {len(cafes)}건\n" + "=" * 64)

    hit = {"exact": 0, "district": 0, "address": 0, "none": 0}
    state_count = {"과밀": 0, "보통": 0, "침체": 0}
    unmatched_districts: dict[str, int] = {}

    for c in cafes:
        d = norm(c.district)
        found = None
        how = None

        if d and d in by_key:                         # 1) 완전 일치
            found, how = by_key[d], "exact"
        elif d:                                       # 2) district 부분 일치
            for k, r in by_key.items():
                if k in d or d in k:
                    found, how = r, "district"
                    break
        if not found and c.address:                   # 3) 주소에서 찾기
            addr = norm(c.address)
            for k, r in by_key.items():
                if k in addr:
                    found, how = r, "address"
                    break

        if found:
            hit[how] += 1
            state_count[found["state"]] += 1
            if not args.dry_run:
                c.region_state = found["state"]
                c.region_index = found["index"]
                c.region_rank = found["rank"]
                # '소외 상권' 필터의 새 정의. 거리가 아니라 활성도 기준이다.
                c.is_remote = found["index"] < REMOTE_INDEX_MAX
        else:
            hit["none"] += 1
            key = c.district or "(district 없음)"
            unmatched_districts[key] = unmatched_districts.get(key, 0) + 1
            state_count["보통"] += 1
            if not args.dry_run:
                # 미매칭은 보통 지역으로. 모르는 곳에 큰 보너스를 주지 않는다.
                c.region_state = "보통"
                c.region_index = None
                c.region_rank = None
                c.is_remote = False

    if not args.dry_run:
        db.commit()

    total = len(cafes)
    matched = total - hit["none"]
    print(f"매칭 성공 {matched}/{total} ({matched / total * 100:.1f}%)")
    print(f"   완전일치 {hit['exact']} · district 부분일치 {hit['district']} "
          f"· 주소에서 {hit['address']} · 실패 {hit['none']}")

    print("\n지역 상태별 매장 수")
    for state, n in state_count.items():
        print(f"   {state:<4} {n:>4}건 ({n / total * 100:>5.1f}%)")

    remote = state_count["침체"]
    print(f"\n소외 상권(is_remote) {remote}건")

    if unmatched_districts:
        print("\n매칭 실패한 district (상위 10)")
        for d, n in sorted(unmatched_districts.items(), key=lambda x: -x[1])[:10]:
            print(f"   {d!r:<20} {n}건")
        print("   → 전부 '보통'으로 처리했습니다(기본 적립만).")

    # 적립금이 실제로 갈리는지 확인. 이게 전부 같으면 정책이 작동하지 않는 것이다.
    from app.core.rewards import calc_reward
    print("\n적립금 미리보기")
    for state in ("과밀", "보통", "침체"):
        sample = next((c for c in cafes if c.region_state == state), None)
        if sample:
            r = calc_reward(sample.region_index)
            print(f"   {state:<4} {sample.name[:16]:<18} {r['total']:>5,}P  ({r['region_tier']})")

    if args.dry_run:
        print("\ndry-run 이었습니다. 괜찮으면 --dry-run 빼고 다시 돌리세요.")
    else:
        print("\n반영 완료.")


if __name__ == "__main__":
    main()
