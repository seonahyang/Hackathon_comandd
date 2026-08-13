"""네이버 CLOVA OCR — 영수증 인식.

무엇을 하나
-----------
영수증 사진 → { 매장명, 결제금액, 결제일시 }

왜 백엔드를 거치나
------------------
CLOVA 의 Secret 은 서버 전용이다. 프론트에서 직접 부르면 키가 브라우저에 노출되고,
남이 우리 쿼터로 OCR 을 돌릴 수 있다(호출당 과금). 그래서 이미지는 우리 서버로
올리고, 서버가 CLOVA 를 부른다.

콘솔 설정 (한 번만)
-------------------
console.ncloud.com > Services > AI Services > CLOVA OCR
  1. 도메인 생성 — 모델을 'Document OCR / 영수증'으로 선택
  2. 생성 후 [APIGW 연동] 까지 완료해야 호출 가능
  3. Invoke URL 과 Secret Key 를 .env 에 넣는다
     CLOVA_OCR_URL=https://…apigw.ntruss.com/custom/v1/…/document/receipt
     CLOVA_OCR_SECRET=…

⚠️ Invoke URL 은 도메인마다 다르다. 이 파일의 예시를 쓰지 말고 콘솔 값을 복사할 것.
"""

import base64
import logging
import re
import time
import uuid

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


class OCRUnavailable(RuntimeError):
    """키 미설정·네트워크 실패 등 '우리 잘못'. 사용자에게는 수동 입력을 권한다."""


class OCRUnreadable(ValueError):
    """호출은 됐는데 영수증을 못 읽음. 다시 찍어달라고 안내한다."""


def is_configured() -> bool:
    return bool(settings.clova_ocr_url and settings.clova_ocr_secret)


# 한 번 성공한 주소를 기억한다. 서버리스에서도 같은 인스턴스가 살아있는 동안은
# 유지되므로, 두 번째 요청부터는 실패하는 주소에 커넥트 타임아웃을 다시 낭비하지
# 않는다. Vercel 함수 상한이 30초라 이 낭비가 곧 504 로 이어진다.
_WORKING_URL: str | None = None


def endpoints() -> list[str]:
    """시도할 URL 목록.

    시크릿이 헤더에 실려 나가므로 https 가 바람직하다. 다만 주소 종류에 따라
    성공 확률이 다르므로 순서를 다르게 잡는다.

      APIGW(*.apigw.ntruss.com)  → https 가 정상. https 를 먼저.
      구버전(clovaocr-api-kr…)   → 443 이 안 열려 있는 경우가 많다. 적힌 대로 먼저.

    무턱대고 https 를 먼저 찔러보면 구버전 주소에서 매번 커넥트 타임아웃만큼
    시간을 버리고, 그게 Vercel 30초 상한을 밀어 올린다.
    """
    u = settings.clova_ocr_url.strip()

    if _WORKING_URL:                      # 이미 되는 주소를 안다
        return [_WORKING_URL]

    if not u.startswith("http://"):
        return [u]

    https = "https://" + u[len("http://"):]
    if "apigw.ntruss.com" in u:
        return [https, u]                 # APIGW 는 https 가 정답
    return [u, https]                     # 구버전은 적힌 대로 먼저


def endpoint() -> str:
    """표시·점검용 대표 URL."""
    return endpoints()[0]


def is_receipt_model() -> bool:
    """영수증 전용(Document OCR) 도메인인지, 일반 OCR 도메인인지.

    두 모델은 응답 형태가 완전히 다르다.
      영수증 모델 → images[0].receipt.result 에 storeInfo/totalPrice 가 구조화돼 옴
      일반 OCR    → images[0].fields[] 에 글자 조각만 옴 (우리가 직접 해석해야 함)
    URL 로 1차 판단하고, 실제 응답을 보고 다시 확인한다.
    """
    return "/document/receipt" in settings.clova_ocr_url or "receipt" in settings.clova_ocr_url


def _first(d: dict, *path, default=None):
    """CLOVA 응답은 중첩이 깊고 필드가 빠지는 경우가 많아 안전하게 파고든다."""
    cur = d
    for k in path:
        if isinstance(cur, list):
            cur = cur[k] if len(cur) > k else None
        elif isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return default
        if cur is None:
            return default
    return cur


def _text(node) -> str | None:
    """{'text': '...', 'formatted': {...}} 형태에서 값을 꺼낸다.

    formatted 의 모양이 필드마다 다르다.
      금액 → {'value': '12400'}          숫자만 있어 text 보다 깨끗하다
      날짜 → {'year','month','day'}      조각으로 나뉘어 있다
    year 만 집어오면 '2026' 이 되어 날짜가 통째로 날아간다. 조각을 다시 합친다.
    """
    if not isinstance(node, dict):
        return None

    fmt = node.get("formatted")
    if isinstance(fmt, dict):
        if fmt.get("value"):
            return str(fmt["value"])
        y, m, d = fmt.get("year"), fmt.get("month"), fmt.get("day")
        if y and m and d:
            return f"{y}-{str(m).zfill(2)}-{str(d).zfill(2)}"
        h, mi = fmt.get("hour"), fmt.get("minute")
        if h and mi:
            return f"{str(h).zfill(2)}:{str(mi).zfill(2)}"

    return str(node["text"]).strip() if node.get("text") else None


def _to_int(s: str | None) -> int | None:
    if not s:
        return None
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else None


def parse_receipt(payload: dict) -> dict:
    """CLOVA 영수증 응답 → 우리가 쓰는 3개 필드.

    응답 스키마가 모델 버전에 따라 조금씩 다르다. 그래서 고정 경로 하나만 믿지 않고
    후보 경로를 순서대로 훑는다. 못 찾으면 None 을 주고 UI 가 수동 입력을 띄운다.
    """
    result = _first(payload, "images", 0, "receipt", "result", default={}) or {}

    store = _text(_first(result, "storeInfo", "name")) or \
        _text(_first(result, "storeInfo", "bizNum"))

    pay = result.get("paymentInfo") or {}
    date = _text(pay.get("date"))
    t = _text(pay.get("time"))

    total = None
    for path in (("totalPrice", "price"), ("totalPrice",)):
        total = _to_int(_text(_first(result, *path)))
        if total:
            break
    if not total:
        # subResults 안의 합계를 마지막 수단으로 본다
        subs = result.get("subResults") or []
        for sub in subs:
            total = _to_int(_text(_first(sub, "totalPrice", "price")))
            if total:
                break

    return {
        "store_name": store,
        "total_price": total,
        "paid_at": " ".join(x for x in (date, t) if x) or None,
    }


# --- 일반 OCR(구버전 external/v1) 응답 해석 ----------------------------------
# 영수증 모델과 달리 글자 조각만 온다. 좌표로 줄을 복원한 뒤 사람처럼 읽어야 한다.

# 합계를 나타내는 말은 가게마다 제각각이다. 아래 중 하나가 있는 줄의 숫자를 총액으로 본다.
TOTAL_HINTS = ["합계", "총액", "총 액", "받을금액", "받을 금액", "결제금액", "결제 금액",
               "판매금액", "판매 금액", "총결제", "합 계", "total", "합게"]
# 총액으로 착각하기 쉬운 것들. 이 말이 있는 줄은 건너뛴다.
SKIP_HINTS = ["부가세", "과세", "면세", "봉사료", "받은금액", "거스름", "잔액", "포인트",
              "할인", "쿠폰", "적립", "공급가"]
DATE_RE = re.compile(r"(20\d{2})[-./년\s]+(\d{1,2})[-./월\s]+(\d{1,2})")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
MONEY_RE = re.compile(r"\d{1,3}(?:,\d{3})+|\d{3,7}")


def _lines_from_fields(fields: list) -> list[str]:
    """글자 조각을 y좌표로 묶어 줄 단위 텍스트로 되돌린다."""
    rows: list[tuple[float, float, str]] = []
    for f in fields:
        txt = (f.get("inferText") or "").strip()
        if not txt:
            continue
        verts = _first(f, "boundingPoly", "vertices", default=[]) or []
        ys = [v.get("y", 0) for v in verts if isinstance(v, dict)]
        xs = [v.get("x", 0) for v in verts if isinstance(v, dict)]
        rows.append((sum(ys) / len(ys) if ys else 0.0,
                     min(xs) if xs else 0.0, txt))

    rows.sort(key=lambda r: (r[0], r[1]))

    lines: list[str] = []
    cur: list[str] = []
    last_y = None
    for y, _x, txt in rows:
        # 같은 줄로 볼 세로 허용치. 영수증 글자 높이를 감안해 12px.
        if last_y is not None and abs(y - last_y) > 12:
            lines.append(" ".join(cur))
            cur = []
        cur.append(txt)
        last_y = y
    if cur:
        lines.append(" ".join(cur))
    return [ln.strip() for ln in lines if ln.strip()]


def _money_in(line: str) -> int | None:
    """줄에서 가장 큰 금액 후보를 뽑는다."""
    best = None
    for m in MONEY_RE.finditer(line):
        v = _to_int(m.group())
        if v and 100 <= v <= 10_000_000:      # 100원 미만·천만원 초과는 금액이 아니다
            best = max(best or 0, v)
    return best


def parse_general(payload: dict) -> dict:
    """일반 OCR 응답 → { 매장명, 금액, 일시 } 추정."""
    fields = _first(payload, "images", 0, "fields", default=[]) or []
    lines = _lines_from_fields(fields)

    # 총액: '합계' 류가 있는 줄 우선, 없으면 전체에서 가장 큰 금액
    total = None
    for ln in lines:
        low = ln.lower()
        if any(s in low for s in SKIP_HINTS):
            continue
        if any(h in low for h in TOTAL_HINTS):
            total = _money_in(ln) or total
    if not total:
        cands = [_money_in(ln) for ln in lines
                 if not any(s in ln.lower() for s in SKIP_HINTS)]
        cands = [c for c in cands if c]
        total = max(cands) if cands else None

    # 매장명: 위쪽 줄 중 숫자·기호가 아닌 첫 한글 이름
    store = None
    for ln in lines[:6]:
        t = re.sub(r"[^0-9A-Za-z가-힣 ]", "", ln).strip()
        if len(t) >= 2 and not t.replace(" ", "").isdigit() \
                and not any(k in t for k in ("영수증", "사업자", "대표", "주소", "전화", "TEL")):
            store = t
            break

    # 일시
    paid = None
    blob = "\n".join(lines)
    dm = DATE_RE.search(blob)
    if dm:
        paid = f"{dm.group(1)}-{dm.group(2).zfill(2)}-{dm.group(3).zfill(2)}"
        tm = TIME_RE.search(blob[dm.end():dm.end() + 40])
        if tm:
            paid += f" {tm.group(1).zfill(2)}:{tm.group(2)}"

    return {"store_name": store, "total_price": total, "paid_at": paid,
            "_lines": lines[:40]}


def scan_receipt(image_bytes: bytes, filename: str = "receipt.jpg") -> dict:
    """영수증 이미지를 CLOVA 로 보내 파싱 결과를 돌려준다."""
    if not is_configured():
        raise OCRUnavailable("CLOVA_OCR_URL / CLOVA_OCR_SECRET 이 .env 에 없습니다")

    ext = (filename.rsplit(".", 1)[-1] or "jpg").lower()
    if ext not in ("jpg", "jpeg", "png", "pdf", "tiff"):
        ext = "jpg"

    body = {
        "version": "V2",
        "requestId": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
        "images": [{
            "format": ext,
            "name": "receipt",
            "data": base64.b64encode(image_bytes).decode(),
        }],
    }

    urls = endpoints()
    last_err: Exception | None = None
    r = None

    for i, url in enumerate(urls):
        try:
            r = httpx.post(
                url,
                headers={"X-OCR-SECRET": settings.clova_ocr_secret},
                json=body,
                # Vercel 함수 상한이 30 초다. 후보를 두 번 시도해도 그 안에
                # 끝나야 504 대신 우리 에러 메시지가 사용자에게 간다.
                #   1차 실패(연결 4초) + 2차 읽기 20초 = 24초 < 30초
                timeout=httpx.Timeout(20.0, connect=4.0),
            )
            global _WORKING_URL
            _WORKING_URL = url            # 다음 요청부터는 이 주소만 쓴다
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("CLOVA OCR 호출 실패(%s): %s: %s",
                           url.split("://")[0], type(e).__name__, str(e)[:120])
            if i < len(urls) - 1:
                logger.warning("→ 다음 후보로 재시도합니다")

    if r is None:
        raise OCRUnavailable("OCR 서버에 연결하지 못했습니다") from last_err

    if r.status_code == 401:
        raise OCRUnavailable("CLOVA Secret 이 올바르지 않습니다 (401)")
    if r.status_code == 404:
        raise OCRUnavailable("Invoke URL 이 올바르지 않습니다 (404). 콘솔 값을 다시 확인하세요")
    if r.status_code != 200:
        logger.warning("CLOVA OCR %s: %s", r.status_code, r.text[:200])
        raise OCRUnavailable(f"OCR 오류 ({r.status_code})")

    payload = r.json()
    infer = _first(payload, "images", 0, "inferResult")
    if infer and infer != "SUCCESS":
        raise OCRUnreadable("영수증을 인식하지 못했습니다. 글자가 선명하게 나오도록 다시 찍어주세요")

    # 도메인 종류에 따라 응답 형태가 다르다. URL 로 짐작하지 말고 실제 응답을 보고 고른다.
    if _first(payload, "images", 0, "receipt"):
        parsed = parse_receipt(payload)
        parsed["model"] = "receipt"          # 구조화 응답 — 정확도 높음
    else:
        parsed = parse_general(payload)
        parsed["model"] = "general"          # 글자만 온 것을 우리가 해석 — 추정치
    if not parsed["total_price"]:
        # 원인이 둘인데 안내가 하나면 사용자가 사진만 계속 다시 찍는다.
        #   general 모델 → 도메인 설정 문제. 다시 찍어도 절대 안 된다.
        #   receipt 모델 → 진짜 사진 문제. 다시 찍으면 된다.
        if parsed["model"] == "general":
            logger.error(
                "일반 OCR 도메인이라 금액 구조화가 안 됩니다. "
                "콘솔에서 'Document OCR - 영수증' 도메인을 만들고 Invoke URL 을 바꾸세요. "
                "현재 URL: %s", settings.clova_ocr_url)
            raise OCRUnreadable(
                "영수증 인식 설정이 완료되지 않았습니다. 금액을 직접 입력해 주세요")
        raise OCRUnreadable("결제 금액을 찾지 못했습니다. 금액이 보이게 다시 찍어주세요")

    # 오인식 방어. 카페 영수증이 20만원을 넘을 일은 사실상 없다.
    if parsed["total_price"] > settings.receipt_max_amount:
        raise OCRUnreadable(
            f"인식된 금액({parsed['total_price']:,}원)이 비정상적으로 큽니다. 다시 찍어주세요")

    return parsed
