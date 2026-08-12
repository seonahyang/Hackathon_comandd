"""'Tenant or user not found' 를 뚫는 스크립트 — 맞는 pooler 주소를 찾아준다.

    python -m scripts.find_pooler

왜 필요한가
-----------
Supabase Session pooler 주소는 `aws-<번호>-<리전>.pooler.supabase.com` 형식인데
번호(0/1)와 리전이 프로젝트마다 다르다. 틀린 조합으로 붙으면 DNS도 되고 TCP도
열리는데 서버가 "Tenant or user not found" 로 끊는다. 비밀번호 문제로 착각하기
딱 좋은 에러라, 해커톤에서 여기에 한 시간씩 날린다.

이 스크립트는 후보 주소를 순서대로 찔러보고 실제로 붙는 조합을 알려준다.
찾으면 .env 에 넣을 줄을 그대로 출력한다.

⚠️ 정공법은 대시보드 상단 [Connect] > Session pooler 탭의 URI 를 복사하는 것.
   그게 가능하면 그게 항상 정확하다. 이 스크립트는 그게 여의치 않을 때의 우회로.
"""

import socket
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

# 한국 팀이 실제로 만나는 순서대로. 서울 → 도쿄 → 싱가포르 → 그 외
REGIONS = [
    "ap-northeast-2",   # 서울
    "ap-northeast-1",   # 도쿄
    "ap-southeast-1",   # 싱가포르
    "us-east-1",
    "us-west-1",
    "eu-central-1",
    "ap-south-1",
    "ap-southeast-2",
    "eu-west-1",
    "us-east-2",
    "sa-east-1",
    "ca-central-1",
]
PREFIXES = ["aws-1", "aws-0"]   # 신규 프로젝트가 aws-1 인 경우가 많아 먼저 시도
PORT = 5432                      # Session pooler. 6543은 Transaction pooler


def parse_current() -> tuple[str, str]:
    """현재 .env 의 DATABASE_URL 에서 사용자명과 비밀번호를 꺼낸다."""
    parts = urlsplit(settings.database_url)
    user = unquote(parts.username or "")
    password = unquote(parts.password or "")

    if not user or not password:
        sys.exit(
            "  [실패] .env 의 DATABASE_URL 에서 사용자명/비밀번호를 읽지 못했습니다.\n"
            "         postgresql://postgres.<ref>:<비밀번호>@... 형식인지 확인하세요."
        )

    if "." not in user:
        print("  [경고] 사용자명이 'postgres' 입니다. pooler 는 "
              "'postgres.<프로젝트ref>' 형식을 요구합니다.")
        ref = ""
        if settings.supabase_url:
            ref = settings.supabase_url.split("//")[-1].split(".")[0]
        if ref:
            user = f"postgres.{ref}"
            print(f"         SUPABASE_URL 을 보고 '{user}' 로 자동 보정합니다.")

    return user, password


def try_connect(host: str, user: str, password: str) -> tuple[bool, str]:
    """실제로 붙여본다. (성공여부, 사유) 반환."""
    import psycopg2

    try:
        conn = psycopg2.connect(
            host=host, port=PORT, user=user, password=password,
            dbname="postgres", sslmode="require", connect_timeout=8,
        )
        conn.close()
        return True, "접속 성공"
    except psycopg2.OperationalError as e:
        msg = " ".join(str(e).split())
        low = msg.lower()
        if "tenant or user not found" in low:
            return False, "이 리전에 프로젝트 없음"
        if "password authentication failed" in low:
            # 호스트는 맞는데 비밀번호만 틀린 것 — 아주 중요한 신호
            return False, "★ 호스트는 맞음! 비밀번호가 틀렸습니다"
        if "timeout" in low or "timed out" in low:
            return False, "타임아웃"
        return False, msg[:90]


def main() -> int:
    print("\n" + "=" * 58)
    print("  Supabase pooler 주소 찾기")
    print("=" * 58)

    user, password = parse_current()
    print(f"\n사용자명: {user}")
    print(f"비밀번호: {'*' * len(password)} ({len(password)}자)")
    print(f"포트:     {PORT} (Session pooler)\n")

    try:
        import psycopg2  # noqa: F401
    except ImportError:
        print("  [실패] psycopg2 가 없습니다:  pip install psycopg2-binary")
        return 1

    wrong_password_host = None

    for prefix in PREFIXES:
        for region in REGIONS:
            host = f"{prefix}-{region}.pooler.supabase.com"

            try:
                socket.getaddrinfo(host, PORT, socket.AF_INET)
            except socket.gaierror:
                print(f"  ·  {host:<48} DNS 없음")
                continue

            ok, reason = try_connect(host, user, password)
            mark = "✅" if ok else ("⚠️ " if "★" in reason else "  ·")
            print(f"  {mark} {host:<48} {reason}")

            if ok:
                print("\n" + "=" * 58)
                print("  찾았습니다. .env 의 DATABASE_URL 을 아래로 교체하세요")
                print("=" * 58)
                pw_enc = password.replace("^", "%5E").replace("@", "%40").replace("#", "%23")
                print(f"\nDATABASE_URL=postgresql://{user}:{pw_enc}"
                      f"@{host}:{PORT}/postgres\n")
                print("이어서:  python -m scripts.init_supabase")
                return 0

            if "★" in reason:
                wrong_password_host = host

    print("\n" + "=" * 58)
    if wrong_password_host:
        print(f"  호스트는 {wrong_password_host} 가 맞습니다.")
        print("  비밀번호만 틀렸습니다 → 대시보드 > Settings > Database >")
        print("  Reset database password 로 새로 만든 뒤 .env 에 넣으세요.")
        print("  (특수문자는 URL 인코딩: ^ → %5E, @ → %40, # → %23)")
    else:
        print("  맞는 조합을 못 찾았습니다.")
        print("  대시보드 상단 [Connect] 버튼 > Session pooler 탭 > URI 를")
        print("  통째로 복사해서 .env 의 DATABASE_URL 에 붙여넣으세요.")
        print("  (비밀번호 자리의 [YOUR-PASSWORD] 는 실제 값으로 교체)")
    print("=" * 58)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
