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
# ── 일반(General) OCR 로 영수증 읽기 ────────────────────────────────────
#
# 영수증 전용 Document OCR 도메인은 계정에 따라 생성이 막혀 있다. 그래서
# 일반 OCR 이 주는 '글자 조각 + 좌표'만으로 결제 금액을 찾아낸다.
#
# 어려운 점은 영수증에 숫자가 아주 많다는 것이다. 사업자등록번호, 전화번호,
# 카드번호, 승인번호, 단가, 수량, 부가세, 받은금액, 거스름돈… 이 중에서
# '결제 총액' 하나만 골라야 한다. 그래서 세 단계로 좁힌다.
#
#   1) 금액이 아닌 줄을 먼저 버린다      (사업자번호·전화·카드번호 등)
#   2) '합계/결제금액' 같은 단서 옆 숫자를 찾는다
#   3) 그래도 없으면 쉼표가 찍힌 숫자만 후보로 본다 (12,400 은 금액, 1234567 은 아님)

TOTAL_HINTS = ["합계", "합 계", "합게", "총액", "총 액", "총합", "총 합",
               "받을금액", "받을 금액", "결제금액", "결제 금액", "결제대상금액",
               "판매금액", "판매 금액", "총결제", "총 결제", "청구금액",
               "승인금액", "카드결제", "신용카드", "결제액", "total", "amount"]

# 이 말이 있는 줄의 숫자는 결제 총액이 아니다.
SKIP_HINTS = ["부가세", "부가가치세", "과세", "면세", "봉사료", "받은금액", "받은 금액",
              "거스름", "잔액", "포인트", "적립", "할인", "쿠폰", "공급가", "세액",
              "사업자", "등록번호", "대표", "주소", "전화", "tel", "카드번호",
              "승인번호", "가맹점", "단말기", "일련번호", "매출표", "할부"]

# 숫자로만 이뤄져 금액과 헷갈리는 것들. 줄 전체를 버린다.
NOISE_RE = re.compile(
    r"\d{3}-\d{2}-\d{5}"          # 사업자등록번호 123-45-67890
    r"|\d{2,4}-\d{3,4}-\d{4}"     # 전화번호
    r"|\*{2,}"                     # 카드번호 마스킹 ****
    r"|\d{4}\s*-?\s*\d{4}\s*-?\s*\d{4}"   # 카드번호
)

DATE_RE = re.compile(r"(20\d{2})[-./년\s]+(\d{1,2})[-./월\s]+(\d{1,2})")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")

# 쉼표가 찍힌 금액을 최우선으로 본다. 영수증 금액은 거의 항상 12,400 꼴이다.
MONEY_COMMA_RE = re.compile(r"\d{1,3}(?:,\d{3})+")
MONEY_PLAIN_RE = re.compile(r"(?<![\d,.-])\d{3,7}(?![\d,.-])")

MIN_AMOUNT, MAX_AMOUNT = 500, 2_000_000


def _lines_from_fields(fields: list) -> list[tuple[str, list[tuple[float, str]]]]:
    """글자 조각을 줄 단위로 되돌린다. (줄 텍스트, [(x, 조각)…]) 를 함께 준다.

    줄 묶는 기준을 12px 로 고정했더니 고해상도 사진에서 전부 따로 놀았다.
    글자 하나가 40px 인 사진에서 12px 기준이면 '합계'와 '12,400' 이 다른 줄로
    갈라지고, 그러면 단서 옆 숫자를 못 찾는다. 실제 글자 높이의 중앙값으로
    기준을 잡아 사진 해상도와 무관하게 동작하게 한다.
    """
    rows: list[tuple[float, float, float, str]] = []   # (y중심, x왼쪽, 높이, 글자)
    for f in fields:
        txt = (f.get("inferText") or "").strip()
        if not txt:
            continue
        verts = _first(f, "boundingPoly", "vertices", default=[]) or []
        ys = [v.get("y", 0) for v in verts if isinstance(v, dict)]
        xs = [v.get("x", 0) for v in verts if isinstance(v, dict)]
        if not ys or not xs:
            continue
        rows.append(((min(ys) + max(ys)) / 2, min(xs), max(ys) - min(ys), txt))

    if not rows:
        return []

    heights = sorted(r[2] for r in rows if r[2] > 0)
    median_h = heights[len(heights) // 2] if heights else 12.0
    tol = max(6.0, median_h * 0.6)      # 같은 줄로 볼 세로 허용치

    rows.sort(key=lambda r: (r[0], r[1]))

    lines: list[tuple[str, list[tuple[float, str]]]] = []
    cur: list[tuple[float, str]] = []
    cur_ys: list[float] = []

    def flush():
        if cur:
            cur.sort(key=lambda c: c[0])
            lines.append((" ".join(t for _, t in cur), list(cur)))

    for y, x, _h, txt in rows:
        # 기준 y 는 '현재 줄에 담긴 조각들의 평균'이다.
        # 예전에는 이전 기준과 새 y 를 계속 절반씩 섞었는데(= 누적 평균이 아니라
        # 지수 이동평균), 기준이 아래로 끌려가면서 같은 줄의 오른쪽 조각이
        # 다음 줄로 튕겨 나갔다. '합계' 와 '14,500' 이 갈라진 게 그 탓이다.
        if cur_ys and abs(y - (sum(cur_ys) / len(cur_ys))) > tol:
            flush()
            cur, cur_ys = [], []
        cur.append((x, txt))
        cur_ys.append(y)

    flush()
    return lines


def _money_in(text: str, allow_plain: bool = True) -> int | None:
    """문자열에서 금액 후보 중 가장 큰 값. 쉼표 있는 숫자를 우선한다."""
    best = None
    for m in MONEY_COMMA_RE.finditer(text):
        v = _to_int(m.group())
        if v and MIN_AMOUNT <= v <= MAX_AMOUNT:
            best = max(best or 0, v)
    if best is not None or not allow_plain:
        return best
    for m in MONEY_PLAIN_RE.finditer(text):
        v = _to_int(m.group())
        if v and MIN_AMOUNT <= v <= MAX_AMOUNT:
            best = max(best or 0, v)
    return best


def _is_noise(line: str) -> bool:
    low = line.lower()
    return bool(NOISE_RE.search(line)) or any(h in low for h in SKIP_HINTS)


def parse_general(payload: dict) -> dict:
    """일반 OCR 응답 → { 매장명, 금액, 일시 } 추정."""
    fields = _first(payload, "images", 0, "fields", default=[]) or []
    parsed_lines = _lines_from_fields(fields)
    lines = [t for t, _ in parsed_lines]

    total = None
    total_reason = None

    # ── 1단계: '합계/결제금액' 단서가 있는 줄 ────────────────────────
    # 영수증은 위에서 아래로 갈수록 최종 금액이 나오므로 뒤쪽을 우선한다.
    for text, parts in reversed(parsed_lines):
        low = text.lower()
        if not any(h in low for h in TOTAL_HINTS):
            continue
        if _is_noise(text):
            continue
        # 단서 오른쪽의 숫자를 본다. 금액은 오른쪽 정렬이 관례다.
        hint_x = None
        for x, frag in parts:
            if any(h in frag.lower() for h in TOTAL_HINTS):
                hint_x = x
                break
        right = " ".join(t for x, t in parts if hint_x is None or x >= hint_x)
        v = _money_in(right)
        if v:
            total, total_reason = v, f"'{text.strip()[:24]}' 줄에서 인식"
            break

    # ── 2단계: 단서 줄에 숫자가 없으면 바로 아랫줄 ───────────────────
    if total is None:
        for i, (text, _p) in enumerate(parsed_lines):
            low = text.lower()
            if any(h in low for h in TOTAL_HINTS) and not _is_noise(text):
                for j in (i + 1, i + 2):
                    if j < len(parsed_lines) and not _is_noise(parsed_lines[j][0]):
                        v = _money_in(parsed_lines[j][0])
                        if v:
                            total, total_reason = v, "단서 아랫줄에서 인식"
                            break
            if total:
                break

    # ── 3단계: 그래도 없으면 쉼표 금액 중 최댓값 ─────────────────────
    # 쉼표 없는 숫자는 전화·승인번호일 확률이 높아 여기서는 쓰지 않는다.
    if total is None:
        cands = [_money_in(t, allow_plain=False) for t, _ in parsed_lines
                 if not _is_noise(t)]
        cands = [c for c in cands if c]
        if cands:
            total, total_reason = max(cands), "쉼표 금액 중 최댓값(추정)"

    # ── 매장명 ───────────────────────────────────────────────────────
    store = None
    for text, _p in parsed_lines[:8]:
        t = re.sub(r"[^0-9A-Za-z가-힣 ]", "", text).strip()
        if len(t) < 2 or t.replace(" ", "").isdigit():
            continue
        if any(k in t for k in ("영수증", "사업자", "대표", "주소", "전화", "TEL",
                                "신용카드", "매출", "가맹점", "고객용")):
            continue
        store = t
        break

    # ── 일시 ─────────────────────────────────────────────────────────
    paid = None
    blob = "\n".join(lines)
    dm = DATE_RE.search(blob)
    if dm:
        paid = f"{dm.group(1)}-{dm.group(2).zfill(2)}-{dm.group(3).zfill(2)}"
        tm = TIME_RE.search(blob[dm.end():dm.end() + 40])
        if tm:
            paid += f" {tm.group(1).zfill(2)}:{tm.group(2)}"

    if total_reason:
        logger.info("일반 OCR 금액 인식: %s원 — %s", total, total_reason)

    return {"store_name": store, "total_price": total, "paid_at": paid,
            "total_reason": total_reason, "_lines": lines[:40]}


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
            # 일반 OCR 은 글자만 주므로 우리가 '합계' 단서를 찾아 금액을 고른다.
            # 못 찾았다는 건 그 줄이 안 찍혔거나 흐렸다는 뜻이다. 어느 줄까지
            # 읽혔는지 로그에 남겨야 다음에 원인을 볼 수 있다.
            logger.warning("일반 OCR 금액 인식 실패. 인식된 줄: %s",
                           " | ".join(parsed.get("_lines", [])[:12]))
            raise OCRUnreadable(
                "결제 금액을 찾지 못했습니다. 합계 금액이 잘리지 않게 다시 찍거나, "
                "금액을 직접 입력해 주세요")
        raise OCRUnreadable("결제 금액을 찾지 못했습니다. 금액이 보이게 다시 찍어주세요")

    # 오인식 방어. 카페 영수증이 20만원을 넘을 일은 사실상 없다.
    if parsed["total_price"] > settings.receipt_max_amount:
        raise OCRUnreadable(
            f"인식된 금액({parsed['total_price']:,}원)이 비정상적으로 큽니다. 다시 찍어주세요")

    return parsed
