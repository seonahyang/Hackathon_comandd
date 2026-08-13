"""생성형 AI — 비정형 텍스트를 우리 DB 스키마로 옮긴다.

무엇을 하나
-----------
1. 리뷰 텍스트 → 카공 환경 속성 (분류)
2. 영업시간 원문 → 구조화된 시간표 (파싱)

왜 필요한가 (심사 질문 대비)
---------------------------
관광공사 데이터 719건을 전수 조사한 결과다.

    콘센트 언급   0/719
    와이파이 언급 0/719
    좌석수        1/719
    휴무일        0/719

카공 정보는 어떤 공공데이터에도 없다. 유일하게 존재하는 곳이 사람이 자유롭게
쓴 문장 — 리뷰 본문과 영업시간 원문이다. 그 문장을 구조화하는 게 이 모듈이고,
그래서 AI 가 부가 기능이 아니라 데이터 수집 경로 그 자체다. 빼면 채울 값이 없다.

규칙 기반으로 안 되는 이유
-------------------------
    "콘센트가 없어서 아쉬웠다"        → 키워드 매칭이면 has_power=True 가 된다
    "주말엔 노트북 금지"              → 조건부 부정
    "[목~일] 10:00~18:00 [월] 휴무"   → 요일별 분기
부정·조건·분기 표현 때문에 정규식으로는 한계가 있다. 실제로 기존 정규식 파서는
719건 중 714건을 처리했지만 휴무일은 한 건도 못 뽑았다.

환각을 어떻게 막나 (이 질문은 반드시 나온다)
-------------------------------------------
1. 근거 문장을 원문에서 '그대로' 인용하게 하고, 원문에 없으면 그 필드를 버린다.
   → `_verify_evidence()`. 지어낸 값은 코드가 걸러낸다.
2. 언급이 없으면 null. "카페니까 와이파이는 있겠지" 같은 추론을 금지한다.
3. AI 결과는 확정이 아니다. 리뷰 투표에서는 사용자가 직접 고른 값이 항상 우선하고,
   AI 는 사용자가 고르지 않은 칸만 채운다.

공급사
------
Gemini 의 OpenAI 호환 엔드포인트를 쓴다. 코드에 구글을 박지 않았으므로
키가 막히면 .env 의 AI_API_URL / AI_MODEL 두 줄만 바꿔 다른 제공사로 옮길 수 있다.
"""

import json
import logging
import re

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


class AIRateLimited(RuntimeError):
    """무료 한도 초과(429). 기다리면 풀리므로 '실패'와 다르게 다뤄야 한다.

    배치 처리는 이걸 잡아서 잠깐 쉬었다 재시도하면 되고, 사용자 요청 경로는
    그냥 건너뛴다(리뷰 등록 자체는 성공해야 하므로).
    """


class AIUnavailable(RuntimeError):
    """키 미설정·네트워크 실패 등 '우리 쪽 문제'.

    호출한 쪽은 이걸 잡아서 조용히 넘어가야 한다. AI 가 죽었다고 리뷰 등록이
    실패하면 안 된다. 시연 중 API 가 끊겨도 화면은 돌아가야 한다.
    """


def is_configured() -> bool:
    return bool(settings.ai_api_url and settings.ai_api_key)


def _norm(s: str) -> str:
    """공백·문장부호를 걷어낸 비교용 문자열. 인용문 대조에 쓴다."""
    return re.sub(r"[\s.,!?~…·\-'\"()]", "", s or "")


def chat_json(system: str, user: str, max_tokens: int = 2048) -> dict:
    """OpenAI 호환 Chat Completions 를 호출하고 JSON 을 돌려준다.

    temperature=0 인 이유: 심사에서 같은 입력을 다시 넣었을 때 다른 답이 나오면
    신뢰를 잃는다. 재현 가능해야 한다.
    """
    if not is_configured():
        raise AIUnavailable("AI_API_URL / AI_API_KEY 가 .env 에 없습니다")

    payload = {
        "model": settings.ai_model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    # 추론에 쓰는 토큰을 줄여 답변 몫을 남긴다. 제공사가 이 파라미터를 모르면
    # 400 이 오는데, 그때는 빼고 한 번 더 보낸다(아래 재시도).
    if settings.ai_reasoning_effort:
        payload["reasoning_effort"] = settings.ai_reasoning_effort

    def _post(body: dict):
        return httpx.post(
            settings.ai_api_url,
            headers={
                "Authorization": f"Bearer {settings.ai_api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=httpx.Timeout(settings.ai_timeout, connect=5.0),
        )

    try:
        r = _post(payload)
        if r.status_code == 400 and "reasoning_effort" in r.text:
            logger.info("이 제공사는 reasoning_effort 를 모릅니다. 빼고 재시도합니다.")
            payload.pop("reasoning_effort", None)
            r = _post(payload)
    except Exception as e:  # noqa: BLE001
        raise AIUnavailable(f"AI 호출 실패: {type(e).__name__}") from e

    if r.status_code != 200:
        # 응답 본문을 반드시 함께 넘긴다. "404" 라는 숫자만으로는 모델명 문제인지
        # 경로 문제인지 권한 문제인지 못 가른다. 원인을 버리는 에러 메시지는
        # 없느니만 못하다.
        detail = r.text[:400].replace("\n", " ")
        logger.warning("AI %s: %s", r.status_code, detail)

        if r.status_code == 401:
            raise AIUnavailable(f"AI_API_KEY 가 올바르지 않습니다 (401) — {detail}")
        if r.status_code == 404:
            raise AIUnavailable(
                f"404 — 모델 '{settings.ai_model}' 또는 URL 경로 문제입니다.\n"
                f"    응답: {detail}\n"
                f"    python -m scripts.check_ai 로 되는 모델명을 찾으세요")
        if r.status_code == 429:
            raise AIRateLimited(detail)
        raise AIUnavailable(f"AI 오류 ({r.status_code}) — {detail}")

    try:
        choice = r.json()["choices"][0]
        content = choice["message"]["content"]
    except Exception as e:  # noqa: BLE001
        raise AIUnavailable("AI 응답 형식이 예상과 다릅니다") from e

    # 토큰 상한에 걸려 잘린 경우. JSON 파싱 실패로 넘어가면 원인이 안 보인다.
    # "JSON 을 못 읽었다"와 "말을 끝까지 못 했다"는 고치는 방법이 다르다.
    if choice.get("finish_reason") == "length":
        raise AIUnavailable(
            f"응답이 max_tokens({max_tokens})에 걸려 잘렸습니다. "
            f".env 의 AI_REASONING_EFFORT 를 none 이나 low 로 두거나 모델을 바꾸세요")

    # json_object 를 요청해도 ```json 펜스를 붙여 보내는 모델이 있다.
    content = re.sub(r"^\s*```(?:json)?|```\s*$", "", content.strip())

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning("AI JSON 파싱 실패: %s", content[:300])
        raise AIUnavailable("AI 응답을 JSON 으로 읽지 못했습니다") from e


# ══════════════════════════════════════════════════════════════════════
#  1. 리뷰 텍스트 → 카공 환경 분류
# ══════════════════════════════════════════════════════════════════════

REVIEW_SYSTEM = """너는 제주 카페 리뷰에서 '노트북 작업 환경' 정보만 뽑아내는 도구다.

규칙:
- 리뷰에 명시적으로 언급된 것만 뽑는다. 언급이 없으면 반드시 null 이다.
- 일반 상식으로 추론하지 마라. "카페니까 와이파이는 있겠지"는 금지다.
- 부정 표현에 주의해라. "콘센트가 없어서 아쉬웠다"는 has_power=false 다.
- 조건부 표현("주말엔 노트북 금지")은 laptop_ok=false 로 하고 근거에 조건을 남겨라.
- 판단한 각 항목마다, 근거가 되는 문장을 리뷰 원문에서 토씨 하나 바꾸지 말고 그대로 인용해라.

아래 JSON 만 출력한다. 설명 문장을 붙이지 마라.
{
  "cagong": true|false|null,      // 노트북 작업하기 좋은 곳인가 (종합 판단)
  "has_power": true|false|null,   // 콘센트
  "has_wifi": true|false|null,    // 와이파이
  "quiet": true|false|null,       // 조용한 분위기
  "size": "small"|"medium"|"large"|null,  // 매장 넓이 (협소/보통/넓음)
  "evidence": { "필드명": "원문에서 그대로 인용한 문장" },
  "confidence": { "필드명": 0.0~1.0 }
}"""

REVIEW_FIELDS = ("cagong", "has_power", "has_wifi", "quiet", "size")
SIZE_VALUES = ("small", "medium", "large")


def _verify_evidence(result: dict, source: str) -> dict:
    """근거 문장이 원문에 실재하는지 대조한다.

    LLM 이 그럴듯한 문장을 지어내는 게 환각의 대표 형태다. 인용문이 원문에
    없으면 그 필드를 통째로 버린다. 이게 신뢰성 방어선 1번이고, 심사에서
    "환각 어떻게 막나요"에 대한 우리 답이다.
    """
    src = _norm(source)
    ev = result.get("evidence") or {}
    dropped = []

    for f in REVIEW_FIELDS:
        if result.get(f) is None:
            continue
        quote = ev.get(f)
        if not quote or _norm(quote) not in src:
            result[f] = None
            dropped.append(f)

    # 넓이는 정해진 값 밖으로 나가면 버린다
    if result.get("size") not in SIZE_VALUES:
        if result.get("size") is not None:
            dropped.append("size(형식오류)")
        result["size"] = None

    result["dropped"] = dropped
    if dropped:
        logger.info("근거 미검증으로 폐기: %s", dropped)
    return result


def classify_review(text: str) -> dict:
    """리뷰 1건 → 카공 속성. 실패하면 AIUnavailable."""
    text = (text or "").strip()
    empty = {f: None for f in REVIEW_FIELDS} | {
        "evidence": {}, "confidence": {}, "dropped": []}

    if len(text) < 10:          # 너무 짧으면 부를 가치가 없다 (비용·지연 절약)
        return empty

    result = chat_json(REVIEW_SYSTEM, text)
    for f in REVIEW_FIELDS:
        result.setdefault(f, None)
    result.setdefault("evidence", {})
    result.setdefault("confidence", {})
    return _verify_evidence(result, text)


# ══════════════════════════════════════════════════════════════════════
#  2. 영업시간 원문 → 구조화
# ══════════════════════════════════════════════════════════════════════

HOURS_SYSTEM = """너는 한국 음식점·카페의 영업시간 원문을 구조화하는 도구다.

입력은 사람이 자유롭게 쓴 텍스트다. <br> 태그, 글머리표, 요일별 분기가 섞여 있다.

규칙:
- 24시간 표기(HH:MM)로 바꾼다. "오전 9시"는 "09:00", "오후 6시"는 "18:00".
- 자정을 넘기면 26:00 처럼 24를 더해 표기한다. "11:00~02:00" → open 11:00, close 26:00.
- 요일마다 시간이 다르면 평일(월~금) 기준을 open/close 에 넣고, weekday_note 에 원문 요약을 남긴다.
- 휴무일은 "월,화" 처럼 쉼표로 잇는다. 연중무휴면 빈 문자열이다.
- 브레이크타임이 없으면 null 이다. 지어내지 마라.
- 영업시간 정보가 아니면(예: "오더 14:30") 전부 null 로 두고 confidence 를 low 로 한다.

아래 JSON 만 출력한다.
{
  "open_time": "HH:MM"|null,
  "close_time": "HH:MM"|null,
  "break_start": "HH:MM"|null,
  "break_end": "HH:MM"|null,
  "closed_days": "월,화",
  "weekday_note": "요일별로 다르면 한 줄 요약, 아니면 null",
  "confidence": "high"|"medium"|"low",
  "reason": "왜 그 신뢰도인지 한 줄"
}"""

_HHMM = re.compile(r"^([0-2]?\d):([0-5]\d)$")
_DAYS = ("월", "화", "수", "목", "금", "토", "일")


def _clean_time(v) -> str | None:
    """HH:MM 형식만 통과시킨다. 0~26시까지 허용(새벽 정규화)."""
    if not isinstance(v, str):
        return None
    m = _HHMM.match(v.strip())
    if not m:
        return None
    h = int(m.group(1))
    if not 0 <= h <= 26:
        return None
    return f"{h:02d}:{m.group(2)}"


def _clean_days(value) -> str:
    """휴무일 문자열에서 요일만 뽑는다. 예) "매주 월요일" → "월"

    낱글자만 훑으면 안 된다. "월요일" 안의 '일' 이 일요일로 잡혀서
    "월,일" 이 되고, 실제로는 여는 일요일에 '오늘 휴무' 로 걸러진다.
    '요일' 이 붙은 형태를 먼저 보고, 없을 때만 낱글자 토큰을 본다.
    """
    text = str(value or "")

    found = [m.group(1) for m in re.finditer(r"([월화수목금토일])\s*요일", text)]
    if not found:
        # "월,화" 처럼 낱글자로만 준 경우
        found = [t for t in re.split(r"[,\s/·및]+", text) if t in _DAYS]

    # 원래 요일 순서로 정렬하고 중복을 없앤다
    return ",".join(d for d in _DAYS if d in found)


def parse_hours(raw: str) -> dict:
    """영업시간 원문 → 구조화. 실패하면 AIUnavailable."""
    raw = (raw or "").strip()
    if not raw:
        raise AIUnavailable("원문이 비어 있습니다")

    r = chat_json(HOURS_SYSTEM, raw, max_tokens=1200)

    out = {
        "open_time": _clean_time(r.get("open_time")),
        "close_time": _clean_time(r.get("close_time")),
        "break_start": _clean_time(r.get("break_start")),
        "break_end": _clean_time(r.get("break_end")),
        "weekday_note": r.get("weekday_note") or None,
        "reason": r.get("reason") or None,
    }

    out["closed_days"] = _clean_days(r.get("closed_days"))

    conf = str(r.get("confidence") or "low").lower()
    out["confidence"] = conf if conf in ("high", "medium", "low") else "low"

    # 열고 닫는 시각이 온전하지 않으면 신뢰도를 낮춘다.
    # 반쪽짜리 값을 high 로 저장하면 '2시간 체류' 판정이 조용히 틀린다.
    if not (out["open_time"] and out["close_time"]):
        out["confidence"] = "low"

    # 브레이크는 짝이 맞아야 의미가 있다
    if not (out["break_start"] and out["break_end"]):
        out["break_start"] = out["break_end"] = None

    return out
