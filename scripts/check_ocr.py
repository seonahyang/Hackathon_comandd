"""CLOVA OCR 진단 — 어디서 막히는지 한 번에 짚는다.

    python -m scripts.check_ocr                  # 연결·인증만 점검
    python -m scripts.check_ocr 영수증.jpg        # 실제 이미지로 끝까지 확인

'OCR 오류'라는 한 줄만 보고는 원인을 못 고른다. 후보가 다섯 가지다.
  1) URL 을 잘못 복사했다 (일반 OCR 도메인 vs 영수증 도메인)
  2) Secret 이 틀렸다 (401)
  3) APIGW 연동을 안 했다 (404)
  4) 도메인 모델이 '영수증'이 아니라 '일반'이다 → 금액을 못 찾는다
  5) 네트워크에서 막힌다 (사내망·방화벽)
이 스크립트가 다섯 개를 순서대로 갈라준다.
"""

import base64
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.services import clova_ocr as ocr  # noqa: E402

OK, NO, WARN = "  [OK]", "  [실패]", "  [주의]"


def line(t=""):
    print(t)


def head(t):
    print(f"\n{'=' * 62}\n  {t}\n{'=' * 62}")


# ── 1. 설정 ────────────────────────────────────────────────────────────
head("1. .env 설정")

url = (settings.clova_ocr_url or "").strip()
secret = (settings.clova_ocr_secret or "").strip()

if not url:
    line(f"{NO} CLOVA_OCR_URL 이 비어 있습니다")
    sys.exit(1)
if not secret:
    line(f"{NO} CLOVA_OCR_SECRET 이 비어 있습니다")
    sys.exit(1)

line(f"  URL    : {url}")
line(f"  Secret : {secret[:4]}…{secret[-4:]} (길이 {len(secret)})")

# ── 2. URL 형태 판정 ───────────────────────────────────────────────────
head("2. URL 이 어떤 종류인가")

is_apigw = "apigw.ntruss.com" in url
is_legacy = "clovaocr-api-kr.ncloud.com" in url
has_receipt_path = "/document/receipt" in url
has_general_path = url.rstrip("/").endswith("/general")

if is_apigw and has_receipt_path:
    line(f"{OK} APIGW 영수증(Document OCR) 엔드포인트 — 이게 정답입니다")
elif is_apigw and has_general_path:
    line(f"{WARN} APIGW 이지만 '일반 OCR(general)' 엔드포인트입니다")
    line("       영수증 전용 도메인을 따로 만들어야 금액·상호가 구조화돼서 옵니다.")
    line("       콘솔 > CLOVA OCR > 도메인 생성 시 모델을 'Document OCR - 영수증'으로 선택")
elif is_apigw:
    line(f"{WARN} APIGW 인데 경로 끝에 /document/receipt 도 /general 도 없습니다")
    line("       콘솔의 'APIGW Invoke URL' 을 끝까지 복사했는지 확인하세요.")
elif is_legacy:
    line(f"{NO} 구버전(legacy) 엔드포인트입니다: clovaocr-api-kr.ncloud.com/external/v1/…")
    line("       이 주소는 '일반 OCR' 전용이라 영수증 구조화 응답이 오지 않습니다.")
    line("       → 금액을 못 찾아서 '결제 금액을 찾지 못했습니다' 가 뜹니다.")
    line("")
    line("       고치는 법:")
    line("       1. console.ncloud.com > Services > AI Services > CLOVA OCR")
    line("       2. [도메인 생성] — 모델을 반드시 'Document OCR / 영수증' 으로")
    line("       3. 생성된 도메인에서 [APIGW 연동] 까지 완료")
    line("       4. 화면에 뜨는 'APIGW Invoke URL' 을 통째로 복사")
    line("          형태: https://xxxxx.apigw.ntruss.com/custom/v1/00000/xxxx/document/receipt")
    line("       5. 같은 화면의 'Secret Key' 를 CLOVA_OCR_SECRET 에")
else:
    line(f"{WARN} 처음 보는 형태의 URL 입니다. 콘솔 값을 다시 확인하세요.")

if url.startswith("http://"):
    line(f"{WARN} http:// 입니다. Secret 이 헤더에 평문으로 나갑니다.")
    line("       APIGW 주소는 https 를 지원하므로 https:// 로 바꾸세요.")

line(f"\n  코드가 '영수증 모델'로 인식하는가: {ocr.is_receipt_model()}")

# ── 3. 연결 ────────────────────────────────────────────────────────────
head("3. 연결 테스트")

candidates = ocr.endpoints()
line(f"  시도할 주소 {len(candidates)}개: {[c.split('://')[0] for c in candidates]}")

reachable = None
for u in candidates:
    scheme = u.split("://")[0]
    t0 = time.time()
    try:
        r = httpx.post(u, headers={"X-OCR-SECRET": secret},
                       json={"version": "V2", "requestId": "probe",
                             "timestamp": int(time.time() * 1000), "images": []},
                       timeout=httpx.Timeout(20.0, connect=6.0))
        dt = time.time() - t0
        line(f"{OK} {scheme}:// 연결됨 — HTTP {r.status_code} ({dt:.1f}초)")
        reachable = (u, r)
        break
    except httpx.ConnectTimeout:
        line(f"{NO} {scheme}:// 연결 시간초과 ({time.time() - t0:.1f}초) — 이 포트가 안 열려 있습니다")
    except Exception as e:  # noqa: BLE001
        line(f"{NO} {scheme}:// {type(e).__name__}: {str(e)[:110]}")

if not reachable:
    line("\n  어느 주소로도 연결되지 않았습니다.")
    line("  → 사내망/방화벽이거나 URL 이 잘못된 주소입니다.")
    sys.exit(1)

used_url, resp = reachable

# ── 4. 인증 ────────────────────────────────────────────────────────────
head("4. 인증 (Secret)")

if resp.status_code == 401:
    line(f"{NO} 401 — Secret 이 틀렸습니다")
    line("       콘솔의 'Secret Key' 를 복사하세요. API Gateway 의 인증키가 아닙니다.")
    sys.exit(1)
elif resp.status_code == 403:
    line(f"{NO} 403 — 권한 없음. APIGW 연동이 끝났는지 확인하세요.")
    sys.exit(1)
elif resp.status_code == 404:
    line(f"{NO} 404 — 주소가 없습니다. Invoke URL 을 끝까지 복사했는지 확인하세요.")
    sys.exit(1)
elif resp.status_code in (200, 400):
    # 400 은 images 를 비워 보냈으니 정상적인 거절이다 = 인증은 통과했다는 뜻
    line(f"{OK} 인증 통과 (HTTP {resp.status_code} — 빈 요청이라 400 은 정상)")
else:
    line(f"{WARN} 예상 밖 응답 {resp.status_code}: {resp.text[:200]}")

# ── 5. 실제 이미지 ─────────────────────────────────────────────────────
head("5. 실제 영수증 인식")

img = sys.argv[1] if len(sys.argv) > 1 else None
if not img:
    line("  이미지를 안 주셨습니다. 끝까지 확인하려면:")
    line("      python -m scripts.check_ocr 영수증사진.jpg")
    sys.exit(0)

path = Path(img)
if not path.exists():
    line(f"{NO} 파일이 없습니다: {path}")
    sys.exit(1)

data = path.read_bytes()
line(f"  파일: {path.name} ({len(data) / 1024:.0f}KB)")
if len(data) > 4 * 1024 * 1024:
    line(f"{WARN} 4MB 를 넘습니다. Vercel 은 4.5MB 에서 끊습니다.")

body = {
    "version": "V2", "requestId": str(uuid.uuid4()),
    "timestamp": int(time.time() * 1000),
    "images": [{"format": path.suffix.lstrip(".").lower() or "jpg",
                "name": "receipt",
                "data": base64.b64encode(data).decode()}],
}

t0 = time.time()
r = httpx.post(used_url, headers={"X-OCR-SECRET": secret}, json=body,
               timeout=httpx.Timeout(40.0, connect=6.0))
line(f"  HTTP {r.status_code} ({time.time() - t0:.1f}초)")

if r.status_code != 200:
    line(f"{NO} {r.text[:400]}")
    sys.exit(1)

payload = r.json()
image0 = (payload.get("images") or [{}])[0]
line(f"  inferResult : {image0.get('inferResult')}")
line(f"  응답 키     : {list(image0.keys())}")

if image0.get("receipt"):
    line(f"{OK} 영수증 구조화 응답이 왔습니다 (정확도 높음)")
else:
    line(f"{NO} 'receipt' 키가 없습니다 → 일반 OCR 도메인입니다")
    line("       글자 조각만 와서 금액을 추측으로 찾아야 하고, 자주 실패합니다.")
    line("       2번 항목의 안내대로 영수증 도메인을 새로 만드세요.")

line("")
try:
    parsed = ocr.scan_receipt(data, path.name)
    line(f"{OK} 파싱 성공")
    line(f"      모델      : {parsed.get('model')}")
    line(f"      상호      : {parsed.get('store_name')}")
    line(f"      결제금액  : {parsed.get('total_price')}")
    line(f"      결제일시  : {parsed.get('paid_at')}")
except Exception as e:  # noqa: BLE001
    line(f"{NO} 파싱 실패: {type(e).__name__}: {e}")
    line("\n  응답 앞부분 (원인 파악용):")
    line("  " + json.dumps(payload, ensure_ascii=False)[:900])
