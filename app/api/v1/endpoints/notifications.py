"""알림 API — 모든 역할 (principal 기준: 학생→student_id, 그 외→user_id)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.permissions import Principal, get_current_principal
from app.db.session import get_db
from app.models import Notification

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _query(db: Session, principal: Principal):
    q = db.query(Notification)
    if principal.kind == "student":
        return q.filter(Notification.student_id == principal.id)
    return q.filter(Notification.user_id == principal.id)


@router.get("")
def list_notifications(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    rows = _query(db, principal).order_by(Notification.created_at.desc()).limit(50).all()
    return [
        {
            "id": n.id,
            "type": n.type,
            "category": n.category,
            "title": n.title,
            "message": n.message,
            "child_id": n.child_id,
            "read_at": n.read_at.isoformat() if n.read_at else None,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in rows
    ]


@router.patch("/read-all")
def mark_all_read(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
):
    now = datetime.utcnow()
    updated = 0
    for n in _query(db, principal).filter(Notification.read_at.is_(None)).all():
        n.read_at = now
        updated += 1
    db.commit()
    return {"ok": True, "updated": updated}


@router.patch("/{notification_id}/read")
def mark_read(
    notification_id: str,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    n = _query(db, principal).filter(Notification.id == notification_id).first()
    if n is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="알림을 찾을 수 없습니다.")
    if n.read_at is None:
        n.read_at = datetime.utcnow()
        db.commit()
    return {"ok": True}
