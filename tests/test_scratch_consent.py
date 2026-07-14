"""필기 원본 보존 동의(scratch_retain) — 보호자 동의 → 보존, 철회 → 파기 대상. (사용자 결정 0714)

동의 주체는 법정대리인(보호자). Consent(type=scratch_retain)로 증빙 기록하고, 기존 미파기
레코드의 consent_retain을 즉시 갱신한다. 새 레코드는 저장 시점 동의로 결정된다.
"""
from datetime import datetime

from app.models import Consent, ScratchRecord, StudentProfile
from app.services import scratch_access
from app.services.privacy_service import anonymize_student


_SEQ = [0]


def _student(db, org="org-x"):
    _SEQ[0] += 1
    n = _SEQ[0]
    s = StudentProfile(
        real_name="보존이", nickname="보존이", student_login_id=f"retain_stu_{n}",
        student_code=f"CAT-RET{n:03d}", password_hash="x", organization_id=org, status="good",
    )
    db.add(s)
    db.flush()
    return s


def _rec(db, sid, org="org-x", retain=False, purged=False):
    r = ScratchRecord(
        student_id=sid, organization_id=org, subject="수학", content_id="q1",
        strokes=[{"color": "#000", "width": 3, "points": [[0, 1, 1]]}],
        stroke_count=1, distance_px=1, first_write_ms=0, draw_ms=0,
        purged=purged, consent_retain=retain,
    )
    db.add(r)
    db.flush()
    return r


def test_grant_consent_marks_existing_and_writes_record(db):
    s = _student(db)
    r = _rec(db, s.id)
    assert scratch_access.has_retain_consent(db, s.id) is False

    scratch_access.set_retain_consent(db, s.id, s.organization_id, "parent-1", True)
    db.commit()

    assert scratch_access.has_retain_consent(db, s.id) is True
    db.refresh(r)
    assert r.consent_retain is True  # 기존 미파기 레코드 즉시 반영
    # 증빙 Consent 1건(활성)
    n = db.query(Consent).filter(
        Consent.student_id == s.id, Consent.consent_type == "scratch_retain",
        Consent.withdrawn_at.is_(None),
    ).count()
    assert n == 1


def test_consent_idempotent_and_withdraw(db):
    s = _student(db)
    scratch_access.set_retain_consent(db, s.id, s.organization_id, "parent-1", True)
    scratch_access.set_retain_consent(db, s.id, s.organization_id, "parent-1", True)  # 중복 grant
    db.commit()
    active = db.query(Consent).filter(
        Consent.student_id == s.id, Consent.consent_type == "scratch_retain",
        Consent.withdrawn_at.is_(None),
    ).count()
    assert active == 1  # 멱등 — 중복 활성 동의 생기지 않음

    scratch_access.set_retain_consent(db, s.id, s.organization_id, "parent-1", False)  # 철회
    db.commit()
    assert scratch_access.has_retain_consent(db, s.id) is False


def test_consented_record_survives_withdrawal_purge(db):
    """보존 동의된 레코드는 탈퇴 시에도 원본 유지, 미동의는 파기."""
    s = _student(db)
    consented = _rec(db, s.id, retain=True)
    plain = _rec(db, s.id, retain=False)
    db.commit()

    assert anonymize_student(db, s) is True
    db.commit()
    db.refresh(consented)
    db.refresh(plain)
    assert consented.purged is False and consented.strokes  # 동의 → 유지
    assert plain.purged is True and plain.strokes == []      # 미동의 → 파기
