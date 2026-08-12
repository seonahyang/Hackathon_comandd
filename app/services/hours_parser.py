"""자유 텍스트 영업시간 → 구조화 데이터 파서.

포털/관광 데이터의 '이용시간' 필드는 형식이 제각각이다.
    "09:00~18:00"
    "매일 10:00 - 20:00 (라스트오더 19:30)"
    "오전 9시 ~ 오후 6시 / 매주 월요일 휴무"
    "연중무휴 24시간"
이걸 전부 {open_time, close_time, last_order_min, closed_days}로 바꾼다.
'여유롭게 2시간' 계산이 이 파서의 정확도에 그대로 달려 있다.

파싱 실패 시 조용히 기본값을 쓰지 않고 confidence='low'로 표시해서,
프론트가 "영업시간 정보 부정확" 뱃지를 띄울 수 있게 한다.
"""

import re

DAYS = ["월", "화", "수", "목", "금", "토", "일"]

# 구분자 통일용
_DASHES = "‐‑‒–—―ー~〜∼-"

LAST_ORDER_RE = re.compile(
    r"(?:라스트\s*오더|라스트오더|라오|last\s*order|l\.?o\.?)\s*[:\-~]?\s*"
    r"(\d{1,2})\s*[:시]\s*(\d{2})?",
    re.IGNORECASE,
)
CLOSED_RE = re.compile(
    r"((?:매주\s*)?[월화수목금토일](?:요일)?"
    r"(?:\s*[,،·および및and&\s~-]\s*[월화수목금토일](?:요일)?)*)"
    r"\s*(?:정기)?\s*(?:휴무|휴관|휴일|휴업|정기휴)"
)
RANGE_RE = re.compile(
    r"(오전|오후|am|pm)?\s*(\d{1,2})\s*[:시]\s*(\d{2})?\s*분?\s*"
    r"[~\-–—]\s*"
    r"(오전|오후|am|pm)?\s*(\d{1,2})\s*[:시]\s*(\d{2})?\s*분?"
)


_TAG_RE = re.compile(r"<[^>]{0,20}>")          # <br>, <BR/>, <p> 등
_BULLET_RE = re.compile(r"(?:^|(?<=\s))[-·•]\s*")
_TIME_RANGE_DASH_RE = re.compile(
    r"(\d{1,2}\s*:\s*\d{2})\s*[-‐‑‒–—―]\s*(\d{1,2}\s*:\s*\d{2})"
)


def _normalize(text: str) -> str:
    """관광공사 엑셀은 HTML 조각과 글머리표가 섞여 들어온다. 먼저 걷어낸다.

    실제 값 예:
        "- 월요일~금요일 10:00~21:00- 토요일 10:00~20:00"
        "[목요일~일요일]<br>10:00~18:00 <br>[월요일]<br>10:00~14:00)"
    """
    t = _TAG_RE.sub(" ", text.strip())

    # 하이픈은 시간 구분자("10:00 - 20:00")로도, 글머리표("- 평일 ...")로도 쓰인다.
    # 글머리표를 먼저 지우면 "10:00 - 20:00" 의 구분자까지 날아가 파싱이 실패한다.
    # 그래서 양쪽에 시각이 붙은 하이픈을 먼저 ~ 로 확정한 뒤 글머리표를 지운다.
    t = _TIME_RANGE_DASH_RE.sub(r"\1~\2", t)
    t = _BULLET_RE.sub(" ", t)

    for d in _DASHES:
        t = t.replace(d, "~")
    t = t.replace("：", ":").replace("　", " ")
    t = re.sub(r"[~]{2,}", "~", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" ~)")


def _hh(hour: int, minute: int | None, ampm: str | None) -> str:
    m = minute if minute is not None else 0
    if ampm in ("오후", "pm", "PM") and hour < 12:
        hour += 12
    if ampm in ("오전", "am", "AM") and hour == 12:
        hour = 0
    return f"{hour:02d}:{m:02d}"


def _expand_day_range(token: str) -> list[str]:
    """'월~수' → [월, 화, 수]"""
    if "~" in token:
        a, b = token.split("~", 1)
        a, b = a.strip()[:1], b.strip()[:1]
        if a in DAYS and b in DAYS:
            i, j = DAYS.index(a), DAYS.index(b)
            if i <= j:
                return DAYS[i : j + 1]
            return DAYS[i:] + DAYS[: j + 1]
    return [c for c in token if c in DAYS]


def parse_closed_days(text: str) -> str:
    t = _normalize(text)
    if "연중무휴" in t or "무휴" in t.replace("연중무휴", ""):
        return ""
    days: list[str] = []
    for m in CLOSED_RE.finditer(t):
        chunk = m.group(1).replace("매주", "").replace("요일", "")
        days.extend(_expand_day_range(chunk))
    seen, out = set(), []
    for d in days:
        if d in DAYS and d not in seen:
            seen.add(d)
            out.append(d)
    return ",".join(out)


def parse_hours(text: str | None) -> dict:
    """
    반환:
      open_time "10:00" / close_time "20:00" / last_order_min 30
      closed_days "월" / confidence high|medium|low / matched 원문조각
    """
    fallback = {
        "open_time": "10:00", "close_time": "20:00", "last_order_min": 30,
        "closed_days": "", "confidence": "low", "matched": None,
    }
    if not text or not text.strip():
        return fallback

    t = _normalize(text)
    closed = parse_closed_days(t)

    # 24시간 영업 / 상시 개방
    if re.search(r"24\s*시간|연중\s*24|00:00\s*~\s*24:00|상시\s*개방", t):
        return {"open_time": "00:00", "close_time": "23:59", "last_order_min": 0,
                "closed_days": closed, "confidence": "medium", "matched": "24시간"}

    m = RANGE_RE.search(t)
    if not m:
        return {**fallback, "closed_days": closed}

    o_ampm, o_h, o_m, c_ampm, c_h, c_m = m.groups()
    open_time = _hh(int(o_h), int(o_m) if o_m else None, o_ampm)
    close_h, close_m = int(c_h), int(c_m) if c_m else None

    # "10:00~02:00" 같은 새벽 마감: 오픈보다 이르면 익일로 간주해 26:00 표기
    close_time = _hh(close_h, close_m, c_ampm)
    if close_time <= open_time and c_ampm is None:
        if close_m is not None:
            # "11:00~02:00" — 분까지 쓴 24시간 표기 = 진짜 새벽 마감
            close_time = f"{close_h + 24:02d}:{close_m:02d}"
        elif close_h < 12 and close_h + 12 > int(open_time[:2]):
            # "10시~7시" — 오후 표기 누락으로 보고 19:00 해석
            close_time = _hh(close_h + 12, close_m, None)
        else:
            close_time = f"{close_h + 24:02d}:00"

    # 라스트오더
    last_order_min = 30
    lo = LAST_ORDER_RE.search(t)
    if lo:
        lo_time = _hh(int(lo.group(1)), int(lo.group(2)) if lo.group(2) else None, None)
        try:
            ch, cm = (int(x) for x in close_time.split(":"))
            lh, lm = (int(x) for x in lo_time.split(":"))
            diff = (ch * 60 + cm) - (lh * 60 + lm)
            if 0 <= diff <= 180:
                last_order_min = diff
        except ValueError:
            pass

    # 분 단위까지 명시됐으면 신뢰도 높음
    confidence = "high" if (o_m is not None or c_m is not None) else "medium"

    # "월~금 10:00~21:00, 토 10:00~20:00" 처럼 구간이 여러 개면 요일별로 다르다는 뜻.
    # 우리 스키마는 요일별 분리를 안 하므로 첫 구간(대개 평일)을 쓰되, 신뢰도를
    # 낮춰서 프론트가 "요일별 상이" 뱃지를 띄울 수 있게 한다.
    # 있는 그대로 표시하는 게, 틀린 값을 자신있게 보여주는 것보다 낫다.
    varies = len(RANGE_RE.findall(t)) > 1
    if varies:
        confidence = "medium" if confidence == "high" else "low"

    return {
        "open_time": open_time,
        "close_time": close_time,
        "last_order_min": last_order_min,
        "closed_days": closed,
        "confidence": confidence,
        "varies_by_day": varies,
        "matched": m.group(0),
    }


def parse_break(text: str | None) -> tuple[str | None, str | None]:
    """'브레이크 타임' 컬럼 → (시작, 종료). 파싱 실패하면 (None, None).

    브레이크는 잘못 넣는 것보다 없는 걸로 두는 게 안전하다. 없는 브레이크를
    넣으면 멀쩡한 가게가 추천에서 빠지고, 그건 되돌릴 방법이 없다.
    """
    if not text or not str(text).strip():
        return None, None

    m = RANGE_RE.search(_normalize(str(text)))
    if not m:
        return None, None

    o_ampm, o_h, o_m, c_ampm, c_h, c_m = m.groups()
    start = _hh(int(o_h), int(o_m) if o_m else None, o_ampm)
    end = _hh(int(c_h), int(c_m) if c_m else None, c_ampm)
    return (start, end) if start != end else (None, None)


def last_order_gap_min(close_time: str, last_order: str | None) -> int | None:
    """마감시각과 라스트오더 시각의 차이(분). 이상한 값이면 None.

    엑셀의 '라스트 오더' 컬럼은 '22:00' 같은 절대시각이라, 우리 스키마의
    '마감 N분 전'으로 환산해야 한다.
    """
    if not last_order:
        return None
    m = re.search(r"(\d{1,2})\s*[:시]\s*(\d{2})", str(last_order))
    if not m:
        return None
    try:
        ch, cm = (int(x) for x in close_time.split(":"))
    except ValueError:
        return None

    lh, lm = int(m.group(1)), int(m.group(2))
    diff = (ch * 60 + cm) - (lh * 60 + lm)
    if diff < 0:                 # 마감이 새벽(26:00)으로 표기된 경우
        diff += 24 * 60
    return diff if 0 <= diff <= 180 else None


# 자체 검증용 — python -m app.services.hours_parser
if __name__ == "__main__":
    CASES = [
        "09:00~18:00",
        "매일 10:00 - 20:00 (라스트오더 19:30)",
        "오전 9시 ~ 오후 6시 / 매주 월요일 휴무",
        "연중무휴 24시간",
        "10:00~22:00, 매주 화,수 휴무",
        "월~금 09:00~18:00",
        "11:00~02:00",
        "09:00 ~ 21:00 L.O 20:00",
        "매주 월~수 정기휴무 10:00~19:00",
        "",
        "영업시간 문의 요망",
    ]
    for c in CASES:
        print(f"{c!r:45} -> {parse_hours(c)}")
