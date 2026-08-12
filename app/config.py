from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./jeju_cagong.db"
    kakao_rest_key: str = ""          # 카카오 로컬 검색 + 모빌리티 길찾기 공용
    visitjeju_api_key: str = ""       # 비짓제주 관광정보 Open API
    cors_origins: str = "*"

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
    def cors_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
