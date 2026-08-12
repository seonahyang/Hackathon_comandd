from fastapi import APIRouter, Depends

from ..auth import get_current_user
from ..config import settings
from ..models import User
from ..schemas import UserOut

router = APIRouter(prefix="/api/auth", tags=["auth (소셜 로그인)"])


@router.get("/me", response_model=UserOut, summary="내 정보 (토큰 검증 + 최초 로그인 시 자동 가입)")
def me(user: User = Depends(get_current_user)):
    """
    프론트가 로그인 직후 한 번 호출하면 된다.
    우리 DB에 계정이 없으면 이 호출에서 자동 생성되므로 별도 회원가입 API가 없다.
    반환되는 `id`를 프론트에서 들고 있다가 리뷰/제보에 쓰면 된다.
    """
    return user


@router.get("/config", summary="인증 설정 상태 + 프론트 초기화 값")
def auth_config():
    """프론트에서 '왜 401이 뜨지?' 할 때 먼저 확인하는 엔드포인트.

    supabase_url / supabase_anon_key 는 공개 값이라 그대로 내려줘도 된다.
    프론트가 이 두 값을 하드코딩하지 않고 여기서 받아가면, 팀이 Supabase
    프로젝트를 갈아끼워도 프론트 코드를 안 고쳐도 된다.
    """
    return {
        "supabase_url": settings.supabase_url or None,
        "supabase_anon_key": settings.supabase_anon_key or None,
        # 지도 렌더링용 공개키. 프론트가 하드코딩하지 않고 여기서 받아간다.
        "kakao_js_key": settings.kakao_js_key or None,
        "auth_required": settings.auth_required,
        "jwks_url": settings.jwks_url if settings.supabase_url else None,
        "legacy_hs256_secret_set": bool(settings.supabase_jwt_secret),
        "ready": bool(settings.supabase_url and settings.supabase_anon_key),
        "hint": (
            "auth_required=false 면 토큰 없이도 호출됩니다(개발 모드). "
            "발표 전에는 .env 에서 AUTH_REQUIRED=true 로 바꾸세요."
        ),
    }
