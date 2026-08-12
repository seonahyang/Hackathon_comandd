"""핵심기능 2 — '여유롭게 2시간' 체류 가능 판정 로직.

판정식:
    도착시각 = 현재시각 + 이동시간
    실질마감 = 영업종료 - 라스트오더 버퍼
    체류구간 = [영업시작, 브레이크시작] + [브레이크종료, 실질마감]
    체류가능  = 위 구간 중 한 곳에 (도착시각 + 체류시간)이 통째로 들어감
                AND (오늘이 휴무일이 아님)

왜 브레이크 타임을 따로 보는가
------------------------------
영업시간만 보면 "10:00~21:00 이니까 15시에 가서 2시간 OK" 로 나오지만,
15:00~17:00 브레이크가 걸려 있으면 실제로는 앉지 못한다. 제주 음식점의 약 15%가
브레이크 타임을 운영한다(관광공사 데이터 기준). 지도에 띄운 추천을 믿고 갔다가
문 닫힌 걸 보는 게 이 서비스에서 가장 치명적인 실패라, 여기서 걸러낸다.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def now_kst() -> datetime:
    return datetime.now(KST)


def parse_now(raw: str | None) -> datetime:
    """데모용 시간 오버라이드. 'ISO8601' 또는 'HH:MM' 허용."""
    if not raw:
        return now_kst()
    raw = raw.strip()
    try:
        if len(raw) == 5 and ":" in raw:
            base = now_kst()
            h, m = raw.split(":")
            return base.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=KST)
    except (ValueError, TypeError):
        return now_kst()


def _to_dt(base: datetime, hhmm: str, allow_overnight: bool = False) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    day_shift = 0
    if h >= 24:  # "26:00" 같은 새벽 표기 지원
        h -= 24
        day_shift = 1
    dt = base.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=day_shift)
    if allow_overnight and dt <= base.replace(hour=0, minute=0, second=0, microsecond=0):
        dt += timedelta(days=1)
    return dt


def is_closed_today(closed_days: str, when: datetime) -> bool:
    if not closed_days:
        return False
    today = WEEKDAYS[when.weekday()]
    return today in [d.strip() for d in closed_days.split(",") if d.strip()]


def evaluate_stay(
    cafe,
    now: datetime,
    stay_hours: float = 2.0,
    travel_min: int = 15,
) -> dict:
    """카페 1곳에 대한 체류 가능 여부 + 프론트에 뿌릴 표시 문구를 계산."""
    if is_closed_today(cafe.closed_days or "", now):
        return {
            "stay_ok": False,
            "open_now": False,
            "reason": "closed_today",
            "label": "오늘 휴무",
            "minutes_left": 0,
            "arrival_at": None,
            "last_call_at": None,
        }

    open_dt = _to_dt(now, cafe.open_time or "09:00")
    close_dt = _to_dt(now, cafe.close_time or "21:00")
    if close_dt <= open_dt:  # 새벽 마감 (예: 10:00 ~ 02:00)
        close_dt += timedelta(days=1)

    last_call = close_dt - timedelta(minutes=cafe.last_order_min or 0)
    arrival = now + timedelta(minutes=travel_min)
    need_until = arrival + timedelta(hours=stay_hours)

    open_now = open_dt <= now <= close_dt
    minutes_left = max(0, int((last_call - arrival).total_seconds() // 60))

    if now > close_dt:
        return {
            "stay_ok": False, "open_now": False, "reason": "already_closed",
            "label": "영업 종료", "minutes_left": 0,
            "arrival_at": arrival.strftime("%H:%M"), "last_call_at": last_call.strftime("%H:%M"),
        }
    if arrival < open_dt:
        return {
            "stay_ok": False, "open_now": open_now, "reason": "not_open_yet",
            "label": f"{cafe.open_time} 오픈", "minutes_left": 0,
            "arrival_at": arrival.strftime("%H:%M"), "last_call_at": last_call.strftime("%H:%M"),
        }

    # --- 브레이크 타임을 빼고 실제로 앉아 있을 수 있는 구간을 만든다 ---
    bs_raw = getattr(cafe, "break_start", None)
    be_raw = getattr(cafe, "break_end", None)
    segments: list[tuple[datetime, datetime]] = []

    if bs_raw and be_raw:
        bs = _to_dt(now, bs_raw)
        be = _to_dt(now, be_raw)
        if be <= bs:
            be += timedelta(days=1)
        segments = [(open_dt, min(bs, last_call)), (max(be, open_dt), last_call)]
    else:
        segments = [(open_dt, last_call)]

    need = timedelta(hours=stay_hours)
    ok = False
    sit_from: datetime | None = None

    for seg_start, seg_end in segments:
        if seg_end <= seg_start:
            continue
        start = max(arrival, seg_start)   # 브레이크 중 도착이면 끝나고 앉는다
        if start + need <= seg_end:
            ok, sit_from = True, start
            break

    if ok and sit_from is not None:
        wait = int((sit_from - arrival).total_seconds() // 60)
        if wait > 0:
            label = f"브레이크 후 {sit_from.strftime('%H:%M')}부터 {stay_hours:g}시간 가능"
        else:
            label = f"{minutes_left // 60}시간 {minutes_left % 60}분 여유"
        reason = "ok"
    elif bs_raw and be_raw:
        label = f"브레이크 타임 {bs_raw}~{be_raw} (2시간 확보 불가)"
        reason = "break_time"
    else:
        label = f"{minutes_left}분 뒤 마감 (2시간 부족)"
        reason = "not_enough_time"

    return {
        "stay_ok": ok,
        "open_now": open_now,
        "reason": reason,
        "label": label,
        "minutes_left": minutes_left,
        "arrival_at": arrival.strftime("%H:%M"),
        "last_call_at": last_call.strftime("%H:%M"),
        "break_time": f"{bs_raw}~{be_raw}" if (bs_raw and be_raw) else None,
        "sit_from": sit_from.strftime("%H:%M") if sit_from else None,
    }
