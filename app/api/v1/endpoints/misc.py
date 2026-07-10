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


@router.get("/ai/student-chat/greeting")
def student_chat_greeting(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
):
    """AI 선생님 첫 인사 — 이름/최근 학습(learning_attempts 실데이터) 반영, 문구는 D."""
    from datetime import date, datetime, time

    from app.models import LearningAttempt

    me = principal.student
    today_start = datetime.combine(date.today(), time.min)
    last = (
        db.query(LearningAttempt)
        .filter(
            LearningAttempt.student_id == me.id,
            LearningAttempt.created_at >= today_start,
        )
        .order_by(LearningAttempt.created_at.desc())
        .first()
    )
    if last:
        game = D.GAME_SUBJECTS.get(last.subject, {}).get("gameTitle", f"{last.subject} 학습")
        recent = str(D.AI_TEACHER_GREETING_RECENT).replace("{game}", game)
    else:
        recent = D.AI_TEACHER_GREETING_DEFAULT
    messages = [
        str(m).replace("{n}", me.nickname).replace("{recent}", recent)
        for m in D.AI_TEACHER_GREETING
    ]
    return {"messages": messages, "suggestions": list(D.STUDENT_AI_ANSWERS.keys())}


@router.post("/ai/student-chat")
def student_chat(
    req: StudentChatRequest, principal: Principal = Depends(require_student)
):
    """AI 선생님 stub — 키워드 매핑 응답 (추후 LLM 연동)."""
    message = req.message.strip()
    reply = D.STUDENT_AI_ANSWERS.get(message)
    if reply is None:
        for question, answer in D.STUDENT_AI_ANSWERS.items():
            key = question.replace("요", "").replace("!", "").replace("?", "")
            if key and key[:4] in message:
                reply = answer
                break
    return {
        "reply": reply or D.STUDENT_AI_DEFAULT,
        "suggestions": list(D.STUDENT_AI_ANSWERS.keys()),
    }


def _batchim(word: str) -> bool:
    ch = ord(word[-1]) if word else 0
    return 0xAC00 <= ch <= 0xD7A3 and (ch - 0xAC00) % 28 > 0


def _live_parent_intro(db: Session, child: StudentProfile) -> str | None:
    """자녀 실집계 기반 인사말 — 정적 산문의 하드코딩 수치(89% 등)가
    학부모 홈 KPI(28일 실집계)와 어긋나지 않게 한다. 시도 없으면 None."""
    from datetime import date, timedelta

    from app.services import aggregate

    today = date.today()
    rows = aggregate.attempts(db, student_ids=[child.id], since=today - timedelta(days=28))
    if not rows:
        return None
    ws = today - timedelta(days=today.weekday())
    week_n = sum(1 for r in rows if r.created_at and r.created_at.date() >= ws)
    acc = round(sum(1 for r in rows if r.result == "correct") / len(rows) * 100)
    by_sub: dict[str, list] = {}
    for r in rows:
        by_sub.setdefault(r.subject, []).append(r)
    subs = {
        s: round(sum(1 for r in g if r.result == "correct") / len(g) * 100)
        for s, g in by_sub.items()
        if len(g) >= 3
    }
    nm = child.nickname + ("이는" if _batchim(child.nickname) else "는")
    intro = (
        f"안녕하세요! 이번 주 {nm} 총 {week_n}회 학습했고, "
        f"최근 4주 평균 정답률은 {acc}%예요."
    )
    if subs:
        best = max(subs, key=subs.get)
        intro += f" 특히 {best}({subs[best]}%)에서 꾸준함이 돋보여요."
        weak = min(subs, key=subs.get)
        if weak != best and subs[weak] < 80:
            j = "은" if _batchim(weak) else "는"
            intro += f" {weak}({subs[weak]}%){j} 함께 조금 더 연습하면 좋겠어요."
    intro += " 궁금한 점을 물어보세요!"
    return intro


@router.get("/ai/parent-chat/intro")
def parent_chat_intro(
    child_id: str,
    principal: Principal = Depends(require_parent),
    db: Session = Depends(get_db),
):
    """학부모 상담 AI 첫 인사 — 실집계 기반, 시도 없으면 자녀별 preset (stat_blobs 수정 가능)."""
    check_parent_child(db, principal.id, child_id)
    child = db.get(StudentProfile, child_id)
    if child is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="자녀 정보를 찾을 수 없습니다.")
    preset = D.PARENT_AI_ANSWERS.get(child.nickname, D.PARENT_AI_ANSWERS["하은"])
    intro = _live_parent_intro(db, child) or preset["intro"]
    return {"intro": intro, "suggestions": list(preset["answers"].keys())}


@router.post("/ai/parent-chat")
def parent_chat(
    req: ParentChatRequest,
    principal: Principal = Depends(require_parent),
    db: Session = Depends(get_db),
):
    """학부모 상담 AI stub — 자녀별 답변 세트 (추후 LLM 연동)."""
    check_parent_child(db, principal.id, req.child_id)
    child = db.get(StudentProfile, req.child_id)
    if child is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="자녀 정보를 찾을 수 없습니다.")
    preset = D.PARENT_AI_ANSWERS.get(child.nickname, D.PARENT_AI_ANSWERS["하은"])
    message = req.message.strip()
    reply = preset["answers"].get(message)
    if reply is None:
        for question, answer in preset["answers"].items():
            if question[:6] in message:
                reply = answer
                break
    if reply is None:
        reply = (
            f"좋은 질문이에요. {child.nickname}이의 최근 학습 기록을 바탕으로 살펴볼게요. "
            "조금 더 구체적으로 말씀해 주시면 자세히 안내해 드릴 수 있어요. 😊"
        )
    return {
        "reply": reply,
        "intro": _live_parent_intro(db, child) or preset["intro"],
        "suggestions": list(preset["answers"].keys()),
    }


@router.get("/captcha/challenge")
def captcha_challenge():
    """메인 CAPTCHA API는 다음 단계 — 200 stub."""
    return {
        "status": "stub",
        "message": "CAPTCHA API는 다음 단계에서 구현됩니다",
        "challenge_id": None,
    }
