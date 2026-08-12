"""Vercel 서버리스 진입점.

Vercel 은 api/ 아래의 .py 파일을 확실하게 함수로 만든다.
(app/main.py 를 자동 감지해주길 기대했다가 정적 사이트로 처리돼 전 경로 404 가 났다)

여기서는 app 을 가져오기만 하고, 실제 코드는 전부 app/ 아래에 있다.
로컬 개발은 그대로 `uvicorn app.main:app --reload` 를 쓴다.
"""

import sys
from pathlib import Path

# Vercel 은 api/ 를 기준으로 실행하므로 프로젝트 루트를 import 경로에 넣는다.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

# Vercel 파이썬 런타임이 찾는 이름들. 어느 쪽을 보든 같은 앱을 준다.
handler = app
application = app
