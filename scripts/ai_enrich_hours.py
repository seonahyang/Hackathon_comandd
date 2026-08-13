"""영업시간 원문을 AI 로 재파싱해 빈칸을 채운다.

    python -m scripts.ai_enrich_hours --dry-run --limit 20   # DB 안 건드리고 확인
    python -m scripts.ai_enrich_hours --only-closed          # 휴무일 채우기 (권장)
    python -m scripts.ai_enrich_hours --only-closed --workers 8   # 더 빠르게

왜 필요한가
-----------
정규식 파서(services/hours_parser.py)가 719건 중 714건을 처리했다. 그런데 두
구멍이 남았다.

    휴무일        0/719   ← 원문에 "매주 월요일 휴무"가 섞여 있는데 못 뽑았다
    요일별 상이   confidence 를 낮춰 저장만 해둔 상태

데이터가 없는 게 아니라 못 뽑은 것이다. 원문(hours_text)은 DB 에 그대로 남아
있으므로, 같은 텍스트를 AI 에 다시 통과시키면 채울 수 있다.

왜 병렬로 부르나
----------------
한 건에 2~4초 걸린다. 200건을 순서대로 부르면 15분이 넘는다. 그런데 이 작업은
건마다 완전히 독립적이라 기다릴 이유가 없다. 여러 건을 동시에 던지면
몇 분으로 줄어든다. 병목은 우리 CPU 가 아니라 네트워크 대기다.

왜 중간중간 저장하나
--------------------
예전 버전은 맨 끝에서 한 번만 commit 해서, 10분을 돌리다 Ctrl+C 하면 그동안
받아온 결과가 통째로 날아갔다. 이제 --commit-every 마다 저장한다. 중단해도
거기까지는 남고, --only-closed 는 이미 채운 매장을 건너뛰므로 그냥 다시
돌리면 이어서 진행된다.

안전장치
--------
- 정규식이 high 로 채운 값은 건드리지 않는다. 잘 된 걸 굳이 흔들 이유가 없다.
- AI 가 low 를 주면 저장하지 않는다. 모르는 값을 아는 척하는 게 제일 나쁘다.
- --dry-run 으로 먼저 눈으로 확인하고 반영한다.
"""

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import or_, select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Cafe  # noqa: E402
from app.services import ai  # noqa: E402

# 429 를 만나면 모든 일꾼이 잠시 멈춘다. 한 스레드만 쉬어봐야 나머지가 계속
# 두드리면 한도가 안 풀린다. 다 같이 숨을 고르는 게 결국 더 빠르다.
_throttle = threading.Event()
_throttle.set()          # set = 통행 가능
_throttle_lock = threading.Lock()


def parse_one(raw: str, max_wait: float) -> dict | None:
    """AI 호출 한 건. DB 는 건드리지 않는다(스레드에서 돌기 때문)."""
    waited = 0.0
    delay = 5.0
    while True:
        _throttle.wait()                      # 한도 걸린 동안 대기
        try:
            return ai.parse_hours(raw)
        except ai.AIRateLimited:
            if waited >= max_wait:
                return None
            with _throttle_lock:
                if _throttle.is_set():        # 첫 발견자만 브레이크를 건다
                    _throttle.clear()
                    print(f"      [한도] 전체 {delay:.0f}초 대기")
                    time.sleep(delay)
                    _throttle.set()
                else:
                    time.sleep(1)
            waited += delay
            delay = min(delay * 2, 30.0)
        except ai.AIUnavailable:
            return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200, help="처리할 매장 수")
    ap.add_argument("--dry-run", action="store_true", help="DB 에 쓰지 않고 출력만")
    ap.add_argument("--only-closed", action="store_true",
                    help="휴무일이 비어 있는 매장만 (가장 큰 공백)")
    ap.add_argument("--workers", type=int, default=4,
                    help="동시 호출 수. 한도에 자주 걸리면 2~3 으로 줄인다")
    ap.add_argument("--commit-every", type=int, default=20,
                    help="몇 건마다 저장할지. 중단해도 여기까지는 남는다")
    ap.add_argument("--max-wait", type=float, default=60.0,
                    help="429 를 만났을 때 한 건당 최대 대기(초)")
    args = ap.parse_args()

    if not ai.is_configured():
        print("AI_API_URL / AI_API_KEY 가 .env 에 없습니다.")
        sys.exit(1)

    db = SessionLocal()

    stmt = select(Cafe).where(Cafe.hours_text.is_not(None), Cafe.hours_text != "")
    if args.only_closed:
        stmt = stmt.where(or_(Cafe.closed_days == "", Cafe.closed_days.is_(None)))
    else:
        stmt = stmt.where(or_(Cafe.hours_confidence != "high",
                              Cafe.closed_days == "",
                              Cafe.closed_days.is_(None)))
    rows = db.scalars(stmt.limit(args.limit)).all()

    # 스레드에는 ORM 객체가 아니라 값만 넘긴다. Session 은 스레드 안전하지 않다.
    jobs = [(c.id, c.name, (c.hours_text or "").strip()) for c in rows]
    by_id = {c.id: c for c in rows}

    print(f"대상 {len(jobs)}건 · 동시 {args.workers}개"
          + (" (dry-run — DB 안 건드림)" if args.dry_run else ""))
    print("=" * 70)

    stat = {"ok": 0, "closed": 0, "hours": 0, "low": 0, "error": 0}
    t0 = time.time()
    done = 0
    pending_commit = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(parse_one, raw, args.max_wait): (cid, name)
                   for cid, name, raw in jobs}

        for fut in as_completed(futures):
            cid, name = futures[fut]
            done += 1
            h = fut.result()

            if h is None:
                stat["error"] += 1
                print(f"[{done}/{len(jobs)}] {name} — 실패/한도")
                continue
            if h["confidence"] == "low":
                stat["low"] += 1
                print(f"[{done}/{len(jobs)}] {name} — 신뢰도 low, 건너뜀")
                continue

            c = by_id[cid]
            changes = []

            if h["closed_days"] and not (c.closed_days or "").strip():
                changes.append(f"휴무 '{h['closed_days']}'")
                if not args.dry_run:
                    c.closed_days = h["closed_days"]
                stat["closed"] += 1

            if c.hours_confidence != "high" and h["open_time"] and h["close_time"]:
                if (c.open_time, c.close_time) != (h["open_time"], h["close_time"]):
                    changes.append(
                        f"{c.open_time}~{c.close_time} → {h['open_time']}~{h['close_time']}")
                if not args.dry_run:
                    c.open_time = h["open_time"]
                    c.close_time = h["close_time"]
                    if h["break_start"]:
                        c.break_start, c.break_end = h["break_start"], h["break_end"]
                    c.hours_confidence = h["confidence"]
                    c.hours_source = "ai"
                stat["hours"] += 1

            stat["ok"] += 1
            pending_commit += 1

            elapsed = time.time() - t0
            eta = (elapsed / done) * (len(jobs) - done)
            print(f"[{done}/{len(jobs)}] {name} — "
                  f"{' / '.join(changes) if changes else '변경 없음'}"
                  f"   (남은 시간 약 {eta / 60:.1f}분)")

            # 중간 저장 — 여기서 끊겨도 여기까지는 살아남는다
            if not args.dry_run and pending_commit >= args.commit_every:
                db.commit()
                pending_commit = 0
                print(f"      ── {done}건까지 저장됨 ──")

    if not args.dry_run:
        db.commit()

    print("=" * 70)
    print(f"소요 {(time.time() - t0) / 60:.1f}분")
    print(f"처리 {stat['ok']}건 · 휴무일 채움 {stat['closed']}건 · "
          f"영업시간 갱신 {stat['hours']}건 · low {stat['low']}건 · 실패 {stat['error']}건")
    if stat["error"]:
        print("\n실패가 많으면 무료 한도입니다. --workers 2 로 줄여 다시 돌리세요.")
        print("--only-closed 는 이미 채운 매장을 건너뛰므로 그냥 다시 돌리면 이어집니다.")
    if args.dry_run:
        print("dry-run 이었습니다. 괜찮으면 --dry-run 빼고 다시 돌리세요.")
    else:
        print("반영 완료. 발표 수치는 GET /api/ai/coverage 로 확인하세요.")


if __name__ == "__main__":
    main()
