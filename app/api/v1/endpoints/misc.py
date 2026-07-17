"""기타 API — 문의(무인증), AI 챗 stub, CAPTCHA 챌린지 stub."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.permissions import Principal, check_parent_child, require_parent, require_student
from app.db.session import get_db
from app.models import Inquiry, StudentProfile
from app.schemas.misc import InquiryCreate, ParentChatRequest, StudentChatRequest
from app.services import auth_service
from app.services.stats import D  # DB(stat_blobs) 우선, design_data fallback

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
