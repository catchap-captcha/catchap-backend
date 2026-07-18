"""기타 API — 문의 접수(무인증) + CAPTCHA 챌린지 stub.

(학생/학부모 AI 챗은 학생 게임화·학부모 은퇴 0718로 제거됨 — 그와 함께 딸려온
require_parent·check_parent_child·chat 스키마 import도 정리했다. git 이력 참고.)"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Inquiry
from app.schemas.misc import InquiryCreate
from app.services import auth_service

router = APIRouter(tags=["misc"])


@router.post("/inquiries")
def create_inquiry(req: InquiryCreate, request: Request, db: Session = Depends(get_db)):
    # 무인증 문의폼 스팸/폭주 방지 — IP 기준 시간당 상한
    ip = request.client.host if request.client else "unknown"
    auth_service.rate_limit(db, f"inquiryip:{ip}", limit=20)
    inquiry = Inquiry(
        inquiry_type=req.inquiry_type,
        name=req.name,
        affiliation=req.affiliation,
        email=req.email,
        content=req.content,
    )
    db.add(inquiry)
    db.commit()
    return {"ok": True, "inquiry_id": inquiry.id, "status": inquiry.status}














@router.get("/captcha/challenge")
def captcha_challenge():
    """메인 CAPTCHA API는 다음 단계 — 200 stub."""
    return {
        "status": "stub",
        "message": "CAPTCHA API는 다음 단계에서 구현됩니다",
        "challenge_id": None,
    }
