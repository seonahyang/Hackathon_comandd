"""비짓제주(제주관광공사) 관광정보 Open API 클라이언트.

엔드포인트 (2026-08 기준 동작 확인):
    GET https://api.visitjeju.net/vsjApi/contents/searchList
        ?apiKey={KEY}&locale=kr&category={CAT}&page={N}

키 발급: https://www.visitjeju.net/kr/visitjejuapi 에서 이메일 신청 → 담당자 승인 후 메일 발송
        (자동 발급이 아니라 사람이 승인하는 방식이라 반나절~하루 걸릴 수 있음)

⚠️ 응답 필드명은 키 없이 검증할 수 없었다. 그래서 이 모듈은
   '있을 법한 키 이름'을 모두 시도하는 방어적 추출기(pick)로 짜여 있고,
   --dump 옵션으로 실제 응답 원본을 찍어볼 수 있게 해뒀다.
   키 받으면 `python -m scripts.enrich_hours --dump` 먼저 돌려서 필드명 확인할 것.
"""

import httpx

BASE = "https://api.visitjeju.net/vsjApi/contents/searchList"

# 카테고리 코드는 활용가이드 PDF 참고. 음식/카페 계열이 보통 c4.
# 확실치 않으므로 여러 개를 순회하고 결과에서 카페만 골라낸다.
DEFAULT_CATEGORIES = ["c4", "c1", "c5"]

# 영업시간이 들어있을 법한 키 후보 (관광 데이터는 사이트마다 이름이 다름)
HOURS_KEYS = ["usetime", "useTime", "opentime", "openTime", "businessHours",
              "playtime", "restdate", "usetimefestival", "infocenter"]
NAME_KEYS = ["title", "name", "cntsNm", "contentsTitle"]
ADDR_KEYS = ["roadaddress", "roadAddress", "address", "addr"]
LAT_KEYS = ["latitude", "lat", "ycoord", "mapy"]
LNG_KEYS = ["longitude", "lng", "xcoord", "mapx"]
ID_KEYS = ["contentsid", "contentsId", "cid"]


def pick(item: dict, keys: list[str]) -> str | None:
    """후보 키를 순서대로 시도. 중첩 dict도 한 단계 탐색."""
    for k in keys:
        v = item.get(k)
        if isinstance(v, dict):
            v = v.get("value") or v.get("label")
        if v not in (None, "", []):
            return str(v)
    return None


def pick_hours(item: dict) -> str | None:
    """명시 키 우선, 없으면 '시간'이 들어간 아무 문자열 필드나 긁는다."""
    v = pick(item, HOURS_KEYS)
    if v:
        return v
    for k, val in item.items():
        if isinstance(val, str) and ("시간" in val or ":" in val) and len(val) < 200:
            if any(t in k.lower() for t in ("time", "hour", "open", "use")):
                return val
    return None


class VisitJejuClient:
    def __init__(self, api_key: str, timeout: float = 10.0):
        self.api_key = api_key
        self._client = httpx.Client(timeout=timeout)

    def close(self):
        self._client.close()

    def search(self, category: str, page: int = 1, locale: str = "kr") -> dict:
        r = self._client.get(BASE, params={
            "apiKey": self.api_key, "locale": locale,
            "category": category, "page": page,
        })
        r.raise_for_status()
        return r.json()

    def iter_items(self, categories: list[str] | None = None, max_pages: int = 30):
        """카테고리별로 페이지를 돌며 항목을 흘려준다."""
        for cat in (categories or DEFAULT_CATEGORIES):
            for page in range(1, max_pages + 1):
                try:
                    body = self.search(cat, page)
                except httpx.HTTPError as e:
                    print(f"  ! {cat} p{page} 요청 실패: {e}")
                    break

                if str(body.get("result")) == "403":
                    raise PermissionError(body.get("resultMessage", "apiKey is invalid"))

                items = body.get("items") or body.get("item") or []
                if isinstance(items, dict):
                    items = [items]
                if not items:
                    break

                for it in items:
                    yield cat, it

                total_page = body.get("totalPage") or body.get("pageCount") or 1
                if page >= int(total_page):
                    break


def normalize(item: dict) -> dict:
    """API 원본 → 우리 스키마."""
    lat, lng = pick(item, LAT_KEYS), pick(item, LNG_KEYS)
    return {
        "visitjeju_id": pick(item, ID_KEYS),
        "name": pick(item, NAME_KEYS),
        "address": pick(item, ADDR_KEYS),
        "lat": float(lat) if lat else None,
        "lng": float(lng) if lng else None,
        "hours_text": pick_hours(item),
    }
