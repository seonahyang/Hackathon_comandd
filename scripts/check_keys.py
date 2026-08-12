"""API 키가 살아있는지 확인. 데이터 수집 전에 제일 먼저 실행할 것.

    python -m scripts.check_keys
"""

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

OK, NG, SKIP = "  [OK]  ", "  [실패] ", "  [건너뜀]"


def check_kakao_local() -> bool:
    print("\n1) 카카오 로컬 API (카페 목록 수집용)")
    if not settings.kakao_rest_key:
        print(SKIP, "KAKAO_REST_KEY 가 .env 에 없습니다")
        return False
    try:
        r = httpx.get(
            "https://dapi.kakao.com/v2/local/search/category.json",
            params={"category_group_code": "CE7", "x": 126.5312, "y": 33.4996,
                    "radius": 2000, "size": 5},
            headers={"Authorization": f"KakaoAK {settings.kakao_rest_key}"},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        print(NG, f"네트워크 오류: {e}")
        return False

    if r.status_code == 200:
        body = r.json()
        print(OK, f"제주시청 반경 2km 카페 {body['meta']['total_count']}건 조회됨")
        for d in body["documents"][:3]:
            print(f"         - {d['place_name']} ({d['address_name']})")
        return True
    if r.status_code == 401:
        print(NG, "401 인증 실패 — JavaScript 키를 넣었을 확률이 높습니다.")
        print("         developers.kakao.com > 내 애플리케이션 > 앱 키 > REST API 키")
    else:
        print(NG, f"{r.status_code} {r.text[:200]}")
    return False


def check_kakao_navi() -> bool:
    print("\n2) 카카오모빌리티 자동차 길찾기 (이동시간 계산용)")
    if not settings.kakao_rest_key:
        print(SKIP, "KAKAO_REST_KEY 없음")
        return False
    try:
        r = httpx.get(
            "https://apis-navi.kakaomobility.com/v1/directions",
            params={"origin": "126.5312,33.4996",       # 제주시청
                    "destination": "126.8380,33.3260",  # 표선
                    "summary": "true"},
            headers={"Authorization": f"KakaoAK {settings.kakao_rest_key}"},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        print(NG, f"네트워크 오류: {e}")
        return False

    if r.status_code == 200:
        route = (r.json().get("routes") or [{}])[0]
        if route.get("result_code") == 0:
            s = route["summary"]
            print(OK, f"제주시청 → 표선  {s['distance'] / 1000:.1f}km / "
                      f"{s['duration'] // 60}분 (실시간 교통 반영)")
            return True
        print(NG, f"경로 탐색 실패: {route.get('result_msg')}")
        return False

    print(NG, f"{r.status_code} {r.text[:200]}")
    print("         길찾기가 별도 활성화를 요구할 수 있습니다.")
    print("         안 되면 .env 에 USE_KAKAO_NAVI=false 로 두세요 (추정치로 동작, 데모 지장 없음)")
    return False


def check_visitjeju() -> bool:
    print("\n3) 비짓제주 관광정보 Open API (영업시간용)")
    if not settings.visitjeju_api_key:
        print(SKIP, "VISITJEJU_API_KEY 없음 — 아직 승인 대기 중이면 정상입니다.")
        print("         대체 경로: python -m scripts.enrich_hours --csv data/hours_override.csv")
        return False
    try:
        r = httpx.get(
            "https://api.visitjeju.net/vsjApi/contents/searchList",
            params={"apiKey": settings.visitjeju_api_key, "locale": "kr",
                    "category": "c4", "page": 1},
            timeout=10.0,
        )
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        print(NG, f"요청 실패: {e}")
        return False

    if str(body.get("result")) == "403":
        print(NG, f"{body.get('resultMessage')} — 키를 다시 확인하세요")
        return False
    items = body.get("items") or []
    print(OK, f"{len(items)}건 조회됨")
    print("         → python -m scripts.enrich_hours --dump 로 필드명 먼저 확인하세요")
    return True


def main():
    print("=" * 55)
    print("API 키 점검")
    print("=" * 55)
    results = {
        "카카오 로컬": check_kakao_local(),
        "카카오 길찾기": check_kakao_navi(),
        "비짓제주": check_visitjeju(),
    }

    print("\n" + "=" * 55)
    for name, ok in results.items():
        print(f"  {name:<14} {'사용 가능' if ok else '사용 불가'}")
    print("=" * 55)

    if results["카카오 로컬"]:
        print("\n다음 단계: python -m scripts.ingest_kakao --fast")
    else:
        print("\n카카오 로컬이 안 되면 실제 카페 수집이 불가합니다.")
        print("데모는 더미 데이터로 가능: python -m scripts.seed")
    if not results["카카오 길찾기"]:
        print(".env 에 USE_KAKAO_NAVI=false 를 넣어두면 추정치로 정상 동작합니다.")


if __name__ == "__main__":
    main()
