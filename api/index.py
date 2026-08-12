"""Vercel 서버리스 진입점.

Vercel 은 이 파일에서 `app` 을 찾아 ASGI 앱으로 띄운다.
실제 코드는 전부 app/ 아래에 있고, 여기는 연결만 한다.

로컬 개발은 지금처럼 `uvicorn app.main:app --reload` 를 그대로 쓰면 된다.
이 파일은 배포에서만 쓰인다.
"""

import sys
from pathlib import Path

# Vercel 은 api/ 를 기준으로 실행하므로 프로젝트 루트를 import 경로에 넣어준다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402,F401
