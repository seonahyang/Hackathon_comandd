"""Supabase Auth 토큰 검증 — 구글/카카오 소셜 로그인.

동작 흐름
---------
1. 프론트(@supabase/supabase-js)가 구글/카카오 로그인 창을 띄운다
2. 로그인 성공하면 Supabase가 access_token(JWT)을 발급한다
3. 프론트는 우리 API를 부를 때 `Authorization: Bearer <token>` 을 붙인다
4. 여기서 그 토큰을 검증하고, users 테이블에 없으면 자동으로 만들어준다

→ 백엔드는 비밀번호를 저장하지도, OAuth 흐름을 처리하지도 않는다.
   Supabase가 신원을 보증하고 우리는 서명만 확인한다.

검증 방식 2가지 (프로젝트 생성 시점에 따라 다름)
-----------------------------------------------
· 신규 프로젝트(2025-10 이후): 비대칭 키. JWKS 공개키로 검증 → SUPABASE_URL만 있으면 됨
· 레거시 프로젝트: 대칭 키(HS256). Settings > API > JWT Secret 필요
둘 다 지원하고, JWT 헤더의 alg를 보고 자동으로 갈라진다.

개발 편의
---------
.env 에 AUTH_REQUIRED=false 를 두면 토큰 없이도 호출된다.
프론트 로그인이 아직 안 붙었을 때 소은·유경님이 막히지 않도록 하는 스위치.
발표 전에는 반드시 true 로 돌려놓을 것.
"""

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import User

bearer = HTTPBearer(auto_error=False)

_jwks_client: jwt.PyJWKClient | None = None


def _get_jwks_client() -> jwt.PyJWKClient:
    """JWKS 공개키는 자주 안 바뀌므로 클라이언트를 재사용(내부 캐시 있음)."""
    global _jwks_client
    if _jwks_client is None:
        if not settings.supabase_url:
            raise HTTPException(500, "SUPABASE_URL 이 설정되지 않았습니다 (.env 확인)")
        _jwks_client = jwt.PyJWKClient(settings.jwks_url, cache_keys=True)
    return _jwks_client


def decode_token(token: str) -> dict:
    """Supabase JWT 검증 후 클레임 반환. 실패 시 401."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            f"토큰 형식이 올바르지 않습니다: {e}") from e

    alg = header.get("alg", "")
    opts = {"verify_aud": True}

    try:
        if alg == "HS256":
            if not settings.supabase_jwt_secret:
                raise HTTPException(
                    500,
                    "이 프로젝트는 레거시 HS256 토큰을 씁니다. "
                    ".env 에 SUPABASE_JWT_SECRET 를 넣어주세요 "
                    "(Supabase 대시보드 > Settings > API > JWT Secret)",
                )
            claims = jwt.decode(
                token, settings.supabase_jwt_secret,
                algorithms=["HS256"], audience="authenticated", options=opts,
            )
        else:
            signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, signing_key.key,
                algorithms=["ES256", "RS256", "EdDSA"],
                audience="authenticated", options=opts,
            )
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "토큰이 만료됐습니다. 다시 로그인해주세요") from e
    except jwt.InvalidAudienceError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "토큰 대상이 올바르지 않습니다") from e
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            f"토큰 검증 실패: {e}") from e

    if not claims.get("sub"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "토큰에 사용자 식별자가 없습니다")
    return claims


def _profile_from_claims(claims: dict) -> dict:
    """구글/카카오가 넘겨주는 프로필 모양이 조금씩 달라서 여기서 흡수한다."""
    meta = claims.get("user_metadata") or {}
    app_meta = claims.get("app_metadata") or {}
    email = claims.get("email") or meta.get("email")

    nickname = (
        meta.get("full_name") or meta.get("name")
        or meta.get("nickname") or meta.get("preferred_username")
        or (email.split("@")[0] if email else None)
        or "게스트"
    )
    return {
        "supabase_uid": claims["sub"],
        "email": email,
        "nickname": str(nickname)[:40],
        "avatar_url": meta.get("avatar_url") or meta.get("picture"),
        "provider": app_meta.get("provider") or "unknown",
    }


def upsert_user(db: Session, claims: dict) -> User:
    """처음 로그인한 유저는 여기서 자동 생성된다 (별도 회원가입 API 불필요)."""
    profile = _profile_from_claims(claims)

    user = db.query(User).filter(User.supabase_uid == profile["supabase_uid"]).one_or_none()
    if user is None and profile["email"]:
        # 소셜 로그인 이전에 만들어둔 계정이 있으면 이메일로 연결
        user = db.query(User).filter(User.email == profile["email"]).one_or_none()

    if user is None:
        user = User(is_workationer=True, point_balance=0, **profile)
        db.add(user)
    else:
        user.supabase_uid = profile["supabase_uid"]
        user.provider = profile["provider"]
        if profile["avatar_url"]:
            user.avatar_url = profile["avatar_url"]
        if profile["email"]:
            user.email = profile["email"]

    db.commit()
    db.refresh(user)
    return user


def get_current_user(
    request: Request,
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    """로그인 필수 엔드포인트에 붙이는 의존성."""
    if cred is None or not cred.credentials:
        if not settings.auth_required:
            return _dev_user(db, request)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "로그인이 필요합니다. Authorization: Bearer <supabase access_token> 헤더를 붙여주세요",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return upsert_user(db, decode_token(cred.credentials))


def get_optional_user(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User | None:
    """로그인 안 해도 되지만, 했으면 개인화하고 싶은 엔드포인트용."""
    if cred is None or not cred.credentials:
        return None
    try:
        return upsert_user(db, decode_token(cred.credentials))
    except HTTPException:
        return None


def _dev_user(db: Session, request: Request) -> User:
    """AUTH_REQUIRED=false 일 때만 쓰이는 개발용 가짜 로그인.

    X-Dev-User-Id 헤더로 특정 유저를 지정할 수 있다 (제보 2인 합의 테스트용).
    """
    dev_id = request.headers.get("X-Dev-User-Id")
    if dev_id and dev_id.isdigit():
        user = db.get(User, int(dev_id))
        if user:
            return user

    user = db.query(User).filter(User.email == "dev@jeju.local").one_or_none()
    if user is None:
        user = User(nickname="개발용유저", email="dev@jeju.local",
                    provider="dev", is_workationer=True)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user
