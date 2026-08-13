"""카카오 로컬 API로 제주 전역 카페를 수집해 DB에 적재한다.

실행:
    python -m scripts.ingest_kakao              # 전체 그리드 스캔 (5~10분)
    python -m scripts.ingest_kakao --fast       # 성긴 그리드 (1~2분, 데모용)

동작 원리
---------
카카오 카테고리 검색(CE7=카페)은 쿼리당 최대 45건만 준다.
그래서 제주도 bbox를 격자로 잘라 각 셀 중심에서 반경 검색을 반복한다.
kakao_place_id로 중복 제거하므로 여러 번 돌려도 안전(upsert).

카카오가 안 주는 값 (콘센트/와이파이/영업시간)은
  - 카공 환경 → app/core/cagong.py 휴리스틱으로 추정
  - 영업시간   → 브랜드별 통계적 기본값으로 추정 (hours_source='estimated')
로 채우고, 응답에 출처를 명시한다. PATCH /api/cafes/{id}/flags 로 덮어쓸 수 있음.
"""

import argparse
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings                     # noqa: E402
from app.core.geo import JEJU_BBOX, classify_remote # noqa: E402
from app.database import Base, SessionLocal, engine # noqa: E402
from app.models import Cafe                         # noqa: E402

KAKAO_URL = "https://dapi.kakao.com/v2/local/search/category.json"
CATEGORY = "CE7"  # 카페

# 브랜드별 영업시간 추정 테이블
HOURS_RULES = [
    (["스타벅스"], "07:00", "22:00", 30),
    (["투썸", "할리스", "커피빈", "폴바셋", "엔제리너스", "탐앤탐스"], "08:00", "22:00", 30),
    (["메가", "컴포즈", "빽다방", "더벤티", "이디야", "매머드"], "08:00", "22:00", 15),
    (["베이커리", "브런치"], "08:00", "19:00", 30),
    (["로스터리", "디저트"], "10:00", "20:00", 30),
]
DEFAULT_HOURS = ("10:00", "20:00", 30)  # 제주 개인 카페 평균값


def guess_hours(name: str, category: str) -> dict:
    text = f"{name} {category}"
    for keys, o, c, lo in HOURS_RULES:
        if any(k in text for k in keys):
            return {"open_time": o, "close_time": c, "last_order_min": lo,
                    "hours_source": "estimated"}
    o, c, lo = DEFAULT_HOURS
    return {"open_time": o, "close_time": c, "last_order_min": lo, "hours_source": "estimated"}


def parse_region(address: str | None) -> tuple[str | None, str | None]:
    if not address:
        return None, None
    parts = address.split()
    region = parts[1] if len(parts) > 1 else None   # 제주시 / 서귀포시
    district = parts[2] if len(parts) > 2 else None  # 애월읍 / 노형동 ...
    return region, district


def fetch_cell(client: httpx.Client, lat: float, lng: float, radius: int) -> list[dict]:
    out, page = [], 1
    while page <= 3:  # 카카오 최대 3페이지(45건)
        try:
            r = client.get(KAKAO_URL, params={
                "category_group_code": CATEGORY, "x": lng, "y": lat,
                "radius": radius, "page": page, "size": 15, "sort": "distance",
            })
            if r.status_code != 200:
                print(f"  ! {r.status_code} {r.text[:120]}")
                break
            body = r.json()
            out.extend(body.get("documents", []))
            if body.get("meta", {}).get("is_end", True):
                break
            page += 1
            time.sleep(0.06)
        except httpx.HTTPError as e:
            print(f"  ! 요청 실패: {e}")
            break
    return out


def upsert(db, doc: dict) -> bool:
    try:
        lat, lng = float(doc["y"]), float(doc["x"])
    except (KeyError, TypeError, ValueError):
        return False

    place_id = str(doc.get("id"))
    cafe = db.query(Cafe).filter(Cafe.kakao_place_id == place_id).one_or_none()
    is_new = cafe is None
    if is_new:
        cafe = Cafe(kakao_place_id=place_id)
        db.add(cafe)

    name = doc.get("place_name", "")
    category = doc.get("category_name", "")
    address = doc.get("address_name")
    dist, remote = classify_remote(lat, lng)
    region, district = parse_region(address)

    cafe.name = name
    cafe.category = category
    cafe.address = address
    cafe.road_address = doc.get("road_address_name")
    cafe.phone = doc.get("phone")
    cafe.place_url = doc.get("place_url")
    cafe.lat, cafe.lng = lat, lng
    cafe.region, cafe.district = region, district
    cafe.dist_to_hotspot_km, cafe.is_remote = dist, remote

    if is_new:  # 이미 제보(user/owner)로 갱신된 건 덮어쓰지 않음
        # 카공 정보는 추측하지 않는다. 브랜드명으로 콘센트 유무를 단정하던
        # 로직은 제거했다. 값은 리뷰 투표와 제보로만 채워진다.
        cafe.cagong_source = "unknown"
        for k, v in guess_hours(name, category).items():
            setattr(cafe, k, v)
    return is_new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="성긴 그리드로 빠르게")
    args = ap.parse_args()

    if not settings.kakao_rest_key:
        print("KAKAO_REST_KEY 가 .env 에 없습니다.")
        print("→ developers.kakao.com > 내 애플리케이션 > 앱 키 > REST API 키")
        print("→ 키 없이 데모하려면: python -m scripts.seed")
        sys.exit(1)

    Base.metadata.create_all(bind=engine)
    step, radius = (0.06, 5000) if args.fast else (0.03, 2500)

    lats, lngs = [], []
    v = JEJU_BBOX["min_lat"]
    while v <= JEJU_BBOX["max_lat"]:
        lats.append(round(v, 4)); v += step
    v = JEJU_BBOX["min_lng"]
    while v <= JEJU_BBOX["max_lng"]:
        lngs.append(round(v, 4)); v += step

    cells = [(la, lo) for la in lats for lo in lngs]
    print(f"그리드 {len(lats)}x{len(lngs)} = {len(cells)}셀, 반경 {radius}m 스캔 시작")

    headers = {"Authorization": f"KakaoAK {settings.kakao_rest_key}"}
    db = SessionLocal()
    seen: set[str] = set()
    new_cnt = 0

    try:
        with httpx.Client(headers=headers, timeout=10.0) as client:
            for i, (la, lo) in enumerate(cells, 1):
                for doc in fetch_cell(client, la, lo, radius):
                    pid = str(doc.get("id"))
                    if pid in seen:
                        continue
                    seen.add(pid)
                    if upsert(db, doc):
                        new_cnt += 1
                if i % 20 == 0:
                    db.commit()
                    print(f"  [{i}/{len(cells)}] 수집 {len(seen)}건 (신규 {new_cnt})")
        db.commit()
    finally:
        db.close()

    print(f"\n완료: 총 {len(seen)}건 조회 / 신규 {new_cnt}건 저장")
    print("다음: uvicorn app.main:app --reload 후 http://127.0.0.1:8000/docs")


if __name__ == "__main__":
    main()
