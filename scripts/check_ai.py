"""Gemini API 연결 점검 — 키를 꽂고 제일 먼저 돌려볼 것.

    python -m scripts.check_ai

연결·인증·모델명을 순서대로 확인하고, 실제 분류/파싱까지 한 번씩 돌려본다.
모델명이 틀리면 사용 가능한 목록을 뽑아준다.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.services import ai  # noqa: E402

OK, NO, WARN = "  [OK]", "  [실패]", "  [주의]"


def head(t):
    print(f"\n{'=' * 62}\n  {t}\n{'=' * 62}")


# ── 1. 설정 ────────────────────────────────────────────────────────────
head("1. .env 설정")

if not settings.ai_api_url:
    print(f"{NO} AI_API_URL 이 비어 있습니다")
    print("       Gemini: https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
    sys.exit(1)
if not settings.ai_api_key:
    print(f"{NO} AI_API_KEY 가 비어 있습니다")
    print("       https://aistudio.google.com/apikey 에서 발급")
    sys.exit(1)

key = settings.ai_api_key
print(f"  URL   : {settings.ai_api_url}")
print(f"  Key   : {key[:6]}…{key[-4:]} (길이 {len(key)})")
print(f"  Model : {settings.ai_model}")

if "generativelanguage.googleapis.com" in settings.ai_api_url:
    if "/openai/" not in settings.ai_api_url:
        print(f"{NO} Gemini 는 OpenAI 호환 경로를 써야 합니다.")
        print("       .../v1beta/openai/chat/completions 형태여야 합니다")
        sys.exit(1)
    if not settings.ai_api_url.rstrip("/").endswith("/chat/completions"):
        print(f"{WARN} URL 이 /chat/completions 로 끝나지 않습니다")
    print(f"{OK} Gemini OpenAI 호환 엔드포인트")

# ── 2. 모델 목록 ───────────────────────────────────────────────────────
head("2. 사용 가능한 모델")

models_url = settings.ai_api_url.rsplit("/chat/completions", 1)[0] + "/models"
try:
    r = httpx.get(models_url, headers={"Authorization": f"Bearer {key}"}, timeout=15)
    if r.status_code == 200:
        ids = [m.get("id", "") for m in (r.json().get("data") or [])]
        ids = [i.replace("models/", "") for i in ids]
        flash = [i for i in ids if "flash" in i][:12]
        print(f"  총 {len(ids)}개. flash 계열 일부:")
        for i in flash:
            mark = "  ← 현재 설정" if i == settings.ai_model else ""
            print(f"      {i}{mark}")
        if settings.ai_model not in ids:
            print(f"\n{WARN} '{settings.ai_model}' 이 목록에 없습니다.")
            print("       위 목록에서 하나 골라 .env 의 AI_MODEL 에 넣으세요.")
    elif r.status_code == 401:
        print(f"{NO} 401 — API 키가 올바르지 않습니다")
        sys.exit(1)
    else:
        print(f"{WARN} 모델 목록 조회 실패 ({r.status_code}). 건너뛰고 계속합니다.")
except Exception as e:  # noqa: BLE001
    print(f"{WARN} 모델 목록 조회 실패({type(e).__name__}). 건너뛰고 계속합니다.")

# ── 2-b. 실제로 호출되는 모델 찾기 ─────────────────────────────────────
head("2-b. 어떤 모델명이 실제로 먹히나")

# 목록에 있다고 호출까지 되는 건 아니다. 목록은 '이 프로젝트가 아는 모델'이고,
# 실제 호출 가능 여부는 키 종류·요금제·리전에 따라 다르다. 그래서 진짜로
# 한 번씩 찔러본다. 후보를 짧은 프롬프트로 테스트하므로 비용은 거의 없다.
candidates = []
for m in (settings.ai_model, f"models/{settings.ai_model}"):
    if m not in candidates:
        candidates.append(m)
for m in ("gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite",
          "gemini-3-flash-preview", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"):
    for v in (m, f"models/{m}"):
        if v not in candidates:
            candidates.append(v)

working = None
for m in candidates:
    try:
        rr = httpx.post(
            settings.ai_api_url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": m, "max_tokens": 20,
                  "messages": [{"role": "user", "content": "1+1은? 숫자만"}]},
            timeout=25,
        )
    except Exception as e:  # noqa: BLE001
        print(f"      {m:38} 연결 실패 {type(e).__name__}")
        continue

    if rr.status_code == 200:
        print(f"{OK} {m:36} 호출 성공")
        working = working or m
        if working == m:
            break
    else:
        msg = rr.text[:120].replace("\n", " ")
        print(f"      {m:38} {rr.status_code}  {msg}")

print()
if working:
    if working != settings.ai_model:
        print(f"{WARN} .env 의 AI_MODEL 을 아래 값으로 바꾸세요:")
        print(f"\n      AI_MODEL={working}\n")
        print("       바꾼 뒤 이 스크립트를 다시 돌리세요.")
        sys.exit(0)
    print(f"{OK} 현재 설정({settings.ai_model}) 그대로 사용 가능합니다")
else:
    print(f"{NO} 어떤 모델명으로도 호출이 안 됩니다.")
    print("       위 응답 메시지를 확인하세요. 자주 있는 원인:")
    print("       · 키가 Google AI Studio 키가 아님 (AI Studio 키는 'AIza' 로 시작)")
    print("       · Google Cloud 프로젝트에 Generative Language API 가 꺼져 있음")
    print("       · 결제 계정 미연결로 무료 한도조차 안 열린 상태")
    sys.exit(1)


# ── 3. 리뷰 분류 ───────────────────────────────────────────────────────
head("3. 리뷰 텍스트 → 카공 속성 (데이터 분류)")

SAMPLES = [
    "콘센트가 자리마다 있고 조용해서 3시간 작업했어요. 자리는 좀 좁아요",
    "뷰는 예쁜데 콘센트가 없어서 노트북 작업은 못 했어요",   # 부정 표현 처리 확인
    "커피가 맛있어요",                                      # 정보 없음 → 전부 null
]

for text in SAMPLES:
    print(f"\n  입력: {text}")
    try:
        r = ai.classify_review(text)
        print(f"      cagong={r['cagong']}  power={r['has_power']}  "
              f"wifi={r['has_wifi']}  quiet={r['quiet']}  size={r['size']}")
        for f, q in (r.get("evidence") or {}).items():
            if r.get(f) is not None:
                print(f"      근거[{f}] \"{q}\"")
        if r.get("dropped"):
            print(f"      근거 미검증으로 폐기: {r['dropped']}")
    except ai.AIUnavailable as e:
        print(f"{NO} {e}")
        sys.exit(1)

# ── 4. 영업시간 파싱 ───────────────────────────────────────────────────
head("4. 영업시간 원문 → 구조화 (시간 파싱)")

HOURS = [
    "[목요일~일요일]<br>10:00~18:00 <br>[월요일] 휴무",
    "- 월요일~금요일 10:00~21:00- 토요일 10:00~20:00",
    "11:00~02:00",                       # 새벽 정규화
    "오전 9시 ~ 오후 6시 / 매주 월요일 휴무",
    "오더 14:30",                        # 영업시간이 아님 → low
]

for raw in HOURS:
    print(f"\n  입력: {raw}")
    try:
        h = ai.parse_hours(raw)
        print(f"      {h['open_time']} ~ {h['close_time']}"
              f"{'  브레이크 ' + h['break_start'] + '~' + h['break_end'] if h['break_start'] else ''}")
        print(f"      휴무: '{h['closed_days']}'  신뢰도: {h['confidence']}")
        if h.get("weekday_note"):
            print(f"      요일별: {h['weekday_note']}")
    except ai.AIUnavailable as e:
        print(f"{NO} {e}")

head("완료")
print("  전부 통과했으면 서버를 켜고 /docs 에서 /api/ai/* 를 눌러보세요.")
print("  영업시간 일괄 채우기: python -m scripts.ai_enrich_hours --limit 20 --dry-run")
