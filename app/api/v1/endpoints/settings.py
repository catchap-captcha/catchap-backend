"""설정 API — 역할 공통 (UserSetting subject_type/subject_id 기준)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.permissions import Principal, get_current_principal
from app.core.security import hash_password, verify_password
from app.db.session import get_db
from app.models import (
    LearningAttempt,
    Notification,
    ParentStudentLink,
    RefreshToken,
    StudentProgress,
    UserSetting,
)
from app.schemas.settings import ChangePasswordRequest, SettingsSave
from app.utils.helpers import audit

router = APIRouter(prefix="/settings", tags=["settings"])

# 역할별 기본 설정 (설정.dc.html / 마이페이지 화면 기본값)
DEFAULT_SETTINGS = {
    "student": {
        "display": {"eye": True, "dark": False, "reduce": False, "color": False},
        "notify": {"remind": True, "badge": True, "weekly": False},
        "sound": {"sfx": True, "voice": True},
        "font": 1,
    },
    "parent": {
        "notif": {"weekly": True, "teacher": True, "complete": True, "recommend": False, "badge": True},
        "channels": {"push": True, "email": True, "sms": False},
        "privacy": {"analytics": True, "marketing": False},
        "twofa": True,
    },
    "teacher": {
        "notif": {"complete": True, "missing": True, "risk": True, "parent": True, "weekly": True},
        "channels": {"push": True, "email": True},
        "prefs": {"period": "week", "nameMode": "real", "sensitivity": "mid", "theme": "light"},
        "twofa": True,
    },
    "org_admin": {"autopay": True, "twofa": True},
    "ops": {},
}


def _subject(principal: Principal) -> tuple[str, str]:
    return ("student" if principal.kind == "student" else "user", principal.id)


def _get_row(db: Session, principal: Principal) -> UserSetting | None:
    subject_type, subject_id = _subject(principal)
    return (
        db.query(UserSetting)
        .filter(UserSetting.subject_type == subject_type, UserSetting.subject_id == subject_id)
        .first()
    )


@router.get("/me")
def get_settings(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    from app.services.stats import D

    row = _get_row(db, principal)
    settings = (
        row.settings
        if row is not None and row.settings
        else DEFAULT_SETTINGS.get(principal.role, {})
    )
    # device: '로그인된 기기' 표시 문구 (세션 위치 테이블 없음 — stat_blobs(D) 수정 가능)
    return {"settings": settings, "device": D.LOGIN_DEVICE_NOTE}


@router.put("/me")
def save_settings(
    req: SettingsSave,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    subject_type, subject_id = _subject(principal)
    row = _get_row(db, principal)
    before = row.settings if row else None
    if row is None:
        row = UserSetting(subject_type=subject_type, subject_id=subject_id, settings=req.settings)
        db.add(row)
    else:
        row.settings = req.settings
    audit(
        db,
        action="settings.update",
        actor_user_id=principal.id,  # 학생 self-service도 actor 기록(ops 조회는 익명코드 표시)
        organization_id=principal.organization_id,
        target_type="user_setting",
        target_id=subject_id,
        before={"settings": before},
        after={"settings": req.settings},
    )
    db.commit()
    return {"ok": True, "settings": row.settings}


@router.post("/me/change-password")
def change_password(
    req: ChangePasswordRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    target = principal.student if principal.kind == "student" else principal.user
    if target is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="현재 비밀번호가 일치하지 않습니다.")
    forced = bool(getattr(target, "must_change_password", False))
    # 강제 변경(임시 비번 첫 로그인)은 방금 그 비번으로 인증했으므로 현재 비번 재확인을 생략한다.
    if not forced and not verify_password(req.current_password or "", target.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="현재 비밀번호가 일치하지 않습니다.")
    target.password_hash = hash_password(req.new_password)
    if hasattr(target, "must_change_password"):
        target.must_change_password = False  # 변경 완료 → 게이트 해제
    audit(
        db,
        action="settings.change_password",
        actor_user_id=principal.id,  # 학생 self-service도 actor 기록(ops 조회는 익명코드 표시)
        organization_id=principal.organization_id,
        target_type=principal.kind,
        target_id=principal.id,
    )
    db.commit()
    return {"ok": True}


@router.post("/me/logout-all")
def logout_all(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    now = datetime.utcnow()
    subject_type = "student" if principal.kind == "student" else "user"
    revoked = 0
    rows = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == principal.id,
            RefreshToken.subject_type == subject_type,
            RefreshToken.revoked_at.is_(None),
        )
        .all()
    )
    for token in rows:
        token.revoked_at = now
        revoked += 1
    db.commit()
    return {"ok": True, "revoked": revoked}


@router.get("/me/export")
def export_data(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    row = _get_row(db, principal)
    payload: dict = {
        # KST(컨테이너 TZ) 벽시계 — created_at 등 다른 사용자 노출 시각과 동일 규약
        "exported_at": datetime.now().isoformat(),
        "subject_type": principal.kind,
        "settings": row.settings if row else {},
    }
    if principal.kind == "student":
        s = principal.student
        payload["profile"] = {
            "nickname": s.nickname,
            "age": s.age,
            "student_code": s.student_code,
            "coins": s.coins,
            "level": s.level,
        }
        payload["progress"] = [
            {"subject": p.subject, "chapters_done": p.chapters_done, "accuracy": p.accuracy}
            for p in db.query(StudentProgress).filter(StudentProgress.student_id == s.id).all()
        ]
        payload["attempts"] = db.query(LearningAttempt).filter(
            LearningAttempt.student_id == s.id
        ).count()
    else:
        u = principal.user
        payload["profile"] = {"name": u.name, "email": u.email, "phone": u.phone, "role": u.role}
        if u.role == "parent":
            payload["children_links"] = [
                {"student_id": l.student_id, "status": l.status}
                for l in db.query(ParentStudentLink)
                .filter(ParentStudentLink.parent_user_id == u.id)
                .all()
            ]
        payload["notifications"] = (
            db.query(Notification).filter(Notification.user_id == u.id).count()
        )
    return payload


@router.delete("/me/account")
def delete_account(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    target = principal.student if principal.kind == "student" else principal.user
    target.status = "disabled"
    now = datetime.utcnow()
    subject_type = "student" if principal.kind == "student" else "user"
    for token in (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == principal.id,
            RefreshToken.subject_type == subject_type,
            RefreshToken.revoked_at.is_(None),
        )
        .all()
    ):
        token.revoked_at = now
    audit(
        db,
        action="settings.account_delete",
        actor_user_id=principal.id,  # 학생 self-service도 actor 기록(ops 조회는 익명코드 표시)
        organization_id=principal.organization_id,
        target_type=principal.kind,
        target_id=principal.id,
        after={"status": "disabled"},
    )
    db.commit()
    return {"ok": True, "status": "disabled"}
