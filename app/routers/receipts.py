"""영수증 인증 — CLOVA OCR 로 매장명·결제금액을 읽는다.

왜 필요한가 (심사 질문 대비)
---------------------------
적립금이 걸린 리뷰는 '가본 사람'만 써야 한다. 안 그러면 집에서 지도만 보고
외곽 매장 리뷰를 양산해 3,700P씩 긁어가는 게 가능해진다. 영수증은 방문을
증명하는 가장 싼 수단이고, 덤으로 결제금액 기반 캐시백까지 준다.
"""

import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Cafe
from ..services import clova_ocr as ocr

router = APIRouter(prefix="/api/receipts", tags=["receipts (영수증 인증)"])

# Vercel 서버리스의 요청 본문 상한이 4.5MB 라, 그보다 크게 잡아봐야
# 우리 코드에 닿기 전에 플랫폼이 413 으로 끊는다. 4MB 로 맞춘다.
# (프론트가 업로드 전에 1600px 로 줄이므로 보통 300KB 안팎이다)
MAX_BYTES = 4 * 1024 * 1024


def _norm(s: str) -> str:
    """상호명 비교용 정규화. 공백·괄호·지점명 표기 차이를 걷어낸다."""
    s = re.sub(r"\(.*?\)", "", s or "")
    return re.sub(r"[\s\-_·.,]", "", s).lower()


def _match_store(receipt_name: str | None, cafe_name: str) -> bool:
    """영수증 상호와 매장명이 같은 곳인지.

    영수증에는 법인명이 찍히는 경우가 많아(예: '카페보롬왓' vs '주식회사 보롬왓')
    완전일치를 요구하면 대부분 실패한다. 한쪽이 다른 쪽을 포함하면 통과로 본다.
    """
    if not receipt_name:
        return False
    a, b = _norm(receipt_name), _norm(cafe_name)
    if not a or not b:
        return False
    return a in b or b in a


@router.get("/config", summary="영수증 인증 사용 가능 여부 (프론트가 버튼을 켤지 판단)")
def receipt_config():
    return {
        "enabled": ocr.is_configured(),
        "cashback_rate": settings.receipt_cashback_rate,
        "max_amount": settings.receipt_max_amount,
        "hint": None if ocr.is_configured()
        else "CLOVA_OCR_URL / CLOVA_OCR_SECRET 을 .env 에 넣으면 켜집니다",
    }


@router.post("", summary="영수증 사진 업로드 → 매장명·금액 인식")
async def scan(
    file: UploadFile = File(..., description="영수증 사진 (jpg/png)"),
    cafe_id: int | None = Form(None, description="검증할 매장 id (선택)"),
    amount: int | None = Form(None, description="수동 입력 금액. OCR 실패 시 대체"),
    db: Session = Depends(get_db),
):
    """이미지를 CLOVA 로 보내 파싱 결과를 돌려준다.

    매칭에 실패해도 400 을 내지 않는다. 영수증 상호는 실제로 자주 다르고,
    여기서 막아버리면 정상 이용자가 리뷰를 못 쓴다. 판단 재료만 내려주고
    최종 처리는 프론트/운영 정책에 맡긴다.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "빈 파일입니다")
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, f"파일이 너무 큽니다 (최대 {MAX_BYTES // 1024 // 1024}MB)")

    try:
        parsed = ocr.scan_receipt(raw, file.filename or "receipt.jpg")
    except (ocr.OCRUnreadable, ocr.OCRUnavailable) as e:
        # OCR 이 실패해도 사용자가 금액을 직접 적었으면 그걸로 진행한다.
        #
        # 영수증 인식은 편의 기능이지 관문이 아니다. 조명·구김·프린터 상태에 따라
        # 실패율이 꽤 되는데, 그때마다 캐시백을 못 받게 하면 정상 이용자만 손해다.
        # 수동 입력분은 store_name 이 없으므로 나중에 운영에서 구분해 검수할 수 있다.
        if amount and 0 < amount <= settings.receipt_max_amount:
            parsed = {"store_name": None, "total_price": amount,
                      "paid_at": None, "model": "manual"}
        elif isinstance(e, ocr.OCRUnreadable):
            raise HTTPException(422, str(e)) from e
        else:
            # 우리 설정·네트워크 문제. 사용자 탓이 아니므로 문구를 구분한다.
            raise HTTPException(503, str(e)) from e

    cashback = int(parsed["total_price"] * settings.receipt_cashback_rate)

    matched = None
    cafe_name = None
    if cafe_id is not None:
        cafe = db.get(Cafe, cafe_id)
        if not cafe:
            raise HTTPException(404, "카페를 찾을 수 없습니다")
        cafe_name = cafe.name
        matched = _match_store(parsed["store_name"], cafe.name)

    return {
        **parsed,
        "cashback": cashback,
        "cashback_rate": settings.receipt_cashback_rate,
        "cafe_name": cafe_name,
        "store_matched": matched,
        "manual": parsed.get("model") == "manual",
        "message": (
            "금액을 직접 입력했어요"
            if parsed.get("model") == "manual" else
            "영수증을 확인했어요"
            if matched is not False else
            f"영수증 상호({parsed['store_name']})가 선택한 매장과 달라요. "
            "다른 매장 영수증이면 적립이 취소될 수 있어요"
        ),
    }
