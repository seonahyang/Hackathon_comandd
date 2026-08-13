from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./jeju_cagong.db"
    kakao_rest_key: str = ""          # 카카오 로컬 검색 + 모빌리티 길찾기 공용 (서버 전용)
    # 지도 렌더링용. REST 키와 다른 값이고, 브라우저에 그대로 노출된다.
    # 유출이 아니라 '공개가 전제'인 키라서 카카오 콘솔의 도메인 제한이 방어선이다.
    kakao_js_key: str = ""
    visitjeju_api_key: str = ""       # 비짓제주 관광정보 Open API

    # --- 네이버 CLOVA OCR (영수증 인식) ---
    # 콘솔에서 도메인을 만들면 이 두 값이 나온다. 둘 다 서버 전용이다.
    # ⚠️ Secret 이 프론트로 새면 남이 우리 쿼터로 OCR 을 돌릴 수 있으므로
    #    이미지 업로드는 반드시 백엔드를 거친다.
    clova_ocr_url: str = ""           # APIGW Invoke URL (…/document/receipt)
    clova_ocr_secret: str = ""        # X-OCR-SECRET 헤더 값
    # 영수증 금액의 몇 %를 적립할지 (목업 기준 2%)
    receipt_cashback_rate: float = 0.02
    # 인식된 금액 상한. OCR 오인식으로 100만원짜리 영수증이 들어와도 방어한다.
    receipt_max_amount: int = 200_000
    cors_origins: str = "*"

    # --- 생성형 AI (데이터 분류 + 비정형 시간 파싱) ---
    # OpenAI 호환 Chat Completions 규격이면 어느 제공사든 그대로 꽂힌다.
    # 공급사를 코드에 박지 않는 이유: 해커톤에서 키가 막히면 URL 한 줄만
    # 바꿔서 다른 제공사로 갈아탈 수 있어야 한다.
    ai_api_url: str = ""      # 예: https://api.openai.com/v1/chat/completions
    ai_api_key: str = ""      # 서버 전용. 절대 프론트로 내려보내지 않는다.
    ai_model: str = "gemini-flash-latest"
    # gemini-2.5-flash 같은 고정 버전명은 신규 사용자에게 닫히는 일이 있다
    # ("no longer available to new users"). -latest 별칭은 구글이 살아있는
    # 버전으로 알아서 넘겨주므로 해커톤처럼 새로 발급한 키에 안전하다.
    ai_timeout: float = 20.0  # Vercel 함수 상한 30초 안에서 끝나야 한다
    # Gemini 3.x/flash-latest 계열은 답을 내기 전에 '생각'하는 데도 토큰을 쓴다.
    # 그 몫까지 max_tokens 에 포함되므로, 낮춰두지 않으면 정작 JSON 이 잘린다.
    # 우리 작업(분류·파싱)은 긴 추론이 필요 없어서 low 로 충분하다.
    ai_reasoning_effort: str = "low"   # none / low / medium / high / 빈값이면 미전송

    # --- Supabase Auth (구글/카카오 소셜 로그인) ---
    supabase_url: str = ""          # https://xxxxx.supabase.co
    supabase_anon_key: str = ""     # 프론트에 그대로 노출되는 공개키 (RLS가 방어선)
    supabase_jwt_secret: str = ""   # 레거시 HS256 프로젝트만 필요. 신규 프로젝트는 비워둘 것
    # false면 토큰 없이도 API 호출 가능 (프론트 로그인 붙기 전 개발용)
    auth_required: bool = True

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    # 길찾기 API를 실제로 호출할지. false면 직선거리 추정만 사용(쿼터 절약)
    use_kakao_navi: bool = True
    # 도보 길찾기는 카카오 제휴 계약 전용이라 추정식으로 대체
    walk_detour_factor: float = 1.3   # 직선거리 대비 실제 보행거리 배수
    walk_speed_kmh: float = 4.0

    @property
    def cors_allow_all(self) -> bool:
        return self.cors_origins.strip() == "*"

    @property
    def cors_list(self) -> list[str]:
        """명시적으로 허용할 출처 목록.

        프론트를 file:// 로 바로 열면 브라우저가 Origin 을 문자열 "null" 로 보낸다.
        디자인 목업(카페맵.dc.html)을 더블클릭해서 여는 경우가 이 케이스라
        기본으로 넣어둔다. 이게 없으면 CORS 에러만 뜨고 원인을 못 찾는다.
        """
        if self.cors_allow_all:
            return ["*"]

        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        if "null" not in origins:
            origins.append("null")
        return origins

    # localhost 는 포트가 팀원마다 다르다(5173 Vite / 3000 CRA / 8080 …).
    # 하나하나 적는 대신 정규식으로 한 번에 허용한다.
    cors_localhost_regex: str = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


settings = Settings()


def _warn_env_problems() -> list[str]:
    """.env 의 흔한 함정을 서버 시작 때 잡아낸다.

    같은 키를 두 번 쓰면 python-dotenv 는 '뒤에 나온 값'을 채택한다. 그래서
    실제 키를 넣어둬도 아래에 빈 줄이 하나 더 있으면 조용히 빈 값이 된다.
    에러도 경고도 없어서 원인을 찾는 데 한참 걸린다 — 여기서 미리 알린다.
    """
    import re
    from pathlib import Path

    problems: list[str] = []
    env = Path(__file__).resolve().parents[1] / ".env"
    if not env.exists():
        # 배포 환경(Vercel 등)은 .env 파일 없이 환경변수로 주입한다. 정상이다.
        import os
        if os.getenv("VERCEL"):
            return []
        return [".env 파일이 없습니다"]

    seen: dict[str, int] = {}
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=", line.strip())
        if m:
            seen[m.group(1)] = seen.get(m.group(1), 0) + 1

    for key, n in seen.items():
        if n > 1:
            problems.append(f"{key} 가 {n}번 정의됨 — 마지막 값이 이깁니다. 한 줄만 남기세요")

    if not settings.kakao_js_key:
        problems.append("KAKAO_JS_KEY 비어있음 — 지도가 손그림으로 표시됩니다")
    if not settings.kakao_rest_key:
        problems.append("KAKAO_REST_KEY 비어있음 — 이동시간이 추정치로만 계산됩니다")
    if not settings.supabase_anon_key:
        problems.append("SUPABASE_ANON_KEY 비어있음 — 소셜 로그인이 동작하지 않습니다")
    if not settings.auth_required:
        problems.append("AUTH_REQUIRED=false — 개발 모드입니다. 발표 전 true 로 바꾸세요")

    return problems


ENV_PROBLEMS = _warn_env_problems()
