"""온보딩(학생 가입코드·학부모 초대코드) 서비스.

설계: docs/onboarding-security-design.md
- 코드는 원문 저장 금지 · sha256만 저장, 발급 시 1회 노출.
- 로그인 아이디는 학교 발급·전역 유일. 별명은 자유(중복 허용).
- 학부모 연결은 초대 코드 소비로만 (B1 무단 연결 해소).
"""

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from app.core.security import hash_password, sha256_hash
from app.models import (
    Organization,
    ParentInviteCode,
    ParentStudentLink,
    StudentJoinCode,
    StudentProfile,
)

# 혼동 문자(0/O, 1/I/L) 제외한 고엔트로피 알파벳
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _seg(n: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def _unique_login_id(db: Session, org: Organization) -> str:
    """학교 발급·전역 유일 로그인 아이디."""
    prefix = (org.code or "stu").split("-")[0].lower()[:6] or "stu"
    for _ in range(50):
        cand = f"{prefix}-{_seg(4).lower()}"
        used = (
            db.query(StudentProfile).filter(StudentProfile.student_login_id == cand).first()
            or db.query(StudentJoinCode).filter(StudentJoinCode.login_id == cand).first()
        )
        if not used:
            return cand
    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="아이디 생성 실패")


def _unique_student_code(db: Session) -> str:
    for _ in range(50):
        code = f"CAT-{_seg(6)}"
        if not db.query(StudentProfile).filter(StudentProfile.student_code == code).first():
            return code
    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="코드 생성 실패")


def generate_join_codes(
    db: Session,
    organization_id: str,
    count: int,
    class_label: str | None = None,
    class_id: str | None = None,
    created_by: str | None = None,
    expires_days: int = 30,
    names: list[str] | None = None,
    genders: list[str | None] | None = None,
) -> list[dict]:
    """학생 슬롯 N개에 대한 1회용 가입 코드 발급. 코드 원문은 이 응답에서만 노출.

    names: 기관이 입력한 학생 실명(슬롯 순서대로). 활성화 시 real_name 으로 복사됨.
    genders: 기관(선생님)이 입력한 성별(슬롯 순서대로, male|female|other). 활성화 시 복사 —
             아이가 스스로 고르지 않는다.
    """
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="기관을 찾을 수 없습니다.")
    count = max(1, min(50, count))
    out: list[dict] = []
    for i in range(count):
        login_id = _unique_login_id(db, org)
        raw = f"JOIN-{_seg(4)}-{_seg(4)}"
        real_name = (names[i].strip()[:100] if names and i < len(names) and names[i] and names[i].strip() else None)
        g = genders[i] if genders and i < len(genders) else None
        gender = g if g in ("male", "female", "other") else None
        db.add(
            StudentJoinCode(
                organization_id=organization_id,
                class_id=class_id,
                login_id=login_id,
                code_hash=sha256_hash(raw),
                class_label=class_label,
                real_name=real_name,
                gender=gender,
                expires_at=_now() + timedelta(days=expires_days),
                created_by=created_by,
            )
        )
        out.append({"login_id": login_id, "join_code": raw, "class_label": class_label, "real_name": real_name, "gender": gender})
    db.commit()
    return out


def reissue_join_code(db: Session, jc: StudentJoinCode) -> dict:
    """미가입(미사용) 학생 코드를 새 코드로 재발급 — 학생이 코드를 잊었을 때.
    login_id·실명·반·성별은 유지하고 code_hash만 새로 바꾼다(옛 코드는 즉시 무효). 만료 30일 연장.
    호출부(엔드포인트)에서 소유·권한·미사용 여부를 이미 검증했다고 가정한다. commit은 호출부에서."""
    raw = f"JOIN-{_seg(4)}-{_seg(4)}"
    jc.code_hash = sha256_hash(raw)
    jc.expires_at = _now() + timedelta(days=30)
    return {
        "login_id": jc.login_id,
        "join_code": raw,
        "class_label": jc.class_label,
        "real_name": jc.real_name,
    }


def check_join_code(db: Session, code: str) -> dict:
    """가입 코드를 소비하지 않고 상태만 확인(아이디/비번 입력 전에 먼저 막기 위함).
    reason: ok | empty | not_found | used | expired.
    activate_student의 판정과 동일해야 최종 제출에서 다른 결과가 나오지 않는다."""
    code = (code or "").strip().upper()
    if not code:
        return {"valid": False, "reason": "empty"}
    row = (
        db.query(StudentJoinCode)
        .filter(StudentJoinCode.code_hash == sha256_hash(code))
        .first()
    )
    if row is None:
        return {"valid": False, "reason": "not_found"}
    if row.used_at is not None:
        return {"valid": False, "reason": "used"}
    if row.expires_at and row.expires_at < _now():
        return {"valid": False, "reason": "expired"}
    return {"valid": True, "reason": "ok"}


def activate_student(
    db: Session, code: str, student_login_id: str, nickname: str, password: str
) -> tuple[StudentProfile, StudentJoinCode]:
    """가입 코드로 학생 계정 활성화 — 학생이 로그인 아이디·별명·비밀번호를 정하면 완료(이메일 없음).

    로그인 아이디는 학생이 직접 입력하고, 전역 유일해야 한다(가입한 학생·미사용 코드 예약분 모두와
    겹치면 안 됨). 중복 확인과 활성화 사이에 선점될 수 있어 여기서 다시 검증한다.
    """
    code = (code or "").strip().upper()
    student_login_id = (student_login_id or "").strip()
    nickname = (nickname or "").strip()
    if not code or not student_login_id or not nickname or not password:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="코드·아이디·별명·비밀번호를 입력해 주세요."
        )
    if len(student_login_id) < 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="아이디는 3자 이상이어야 해요.")
    row = (
        db.query(StudentJoinCode)
        .filter(StudentJoinCode.code_hash == sha256_hash(code))
        .first()
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="가입 코드가 올바르지 않아요.")
    if row.used_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 사용된 코드예요.")
    if row.expires_at and row.expires_at < _now():
        raise HTTPException(status.HTTP_410_GONE, detail="코드가 만료됐어요. 선생님께 새 코드를 받아 주세요.")

    # 학생이 고른 아이디 전역 유일성 재검증 (자기 코드에 예약된 아이디는 제외)
    taken = (
        db.query(StudentProfile)
        .filter(StudentProfile.student_login_id == student_login_id)
        .first()
        or db.query(StudentJoinCode)
        .filter(StudentJoinCode.login_id == student_login_id, StudentJoinCode.id != row.id)
        .first()
    )
    if taken:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="이미 사용 중인 아이디예요. 다른 아이디를 골라 주세요."
        )

    profile = StudentProfile(
        organization_id=row.organization_id,
        class_id=row.class_id,
        student_login_id=student_login_id,  # 학생이 직접 정함(전역 유일)
        student_code=_unique_student_code(db),
        password_hash=hash_password(password),
        nickname=nickname,  # 자유 중복 허용
        real_name=row.real_name,  # 기관 입력 실명 (교사·기관 화면 전용)
        gender=row.gender,  # 기관(선생님) 입력 성별 — 아이가 고르지 않음
        status="good",
    )
    db.add(profile)
    db.flush()
    row.used_at = _now()
    row.student_id = profile.id
    row.login_id = student_login_id  # 기록 일관성 — 학생이 고른 아이디로 갱신(교사 화면 반영)
    db.commit()
    return profile, row


def issue_parent_invite(
    db: Session, student_id: str, organization_id: str, created_by: str | None = None, expires_days: int = 14
) -> str:
    """학생 1명에 귀속된 고엔트로피 학부모 초대 코드 발급(원문 1회 노출)."""
    raw = f"LINK-{_seg(4)}-{_seg(4)}"
    db.add(
        ParentInviteCode(
            student_id=student_id,
            organization_id=organization_id,
            code_hash=sha256_hash(raw),
            expires_at=_now() + timedelta(days=expires_days),
            max_uses=2,
            used_count=0,
            created_by=created_by,
        )
    )
    db.commit()
    return raw


def consume_parent_invite(db: Session, parent_user_id: str, code: str) -> ParentStudentLink:
    """초대 코드로만 자녀 연결 (B1 해소). 검증: hash·미폐기·미만료·잔여횟수·중복연결."""
    code = (code or "").strip().upper()
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="초대 코드를 입력해 주세요.")
    row = (
        db.query(ParentInviteCode)
        .filter(ParentInviteCode.code_hash == sha256_hash(code))
        .first()
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="초대 코드가 올바르지 않아요.")
    if row.revoked_at is not None:
        raise HTTPException(status.HTTP_410_GONE, detail="해제된 초대 코드예요.")
    if row.expires_at and row.expires_at < _now():
        raise HTTPException(status.HTTP_410_GONE, detail="초대 코드가 만료됐어요.")
    if row.used_count >= row.max_uses:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 사용 횟수를 모두 쓴 코드예요.")

    existing = (
        db.query(ParentStudentLink)
        .filter(
            ParentStudentLink.parent_user_id == parent_user_id,
            ParentStudentLink.student_id == row.student_id,
            ParentStudentLink.status == "approved",
        )
        .first()
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 연결된 자녀예요.")

    # 자녀 수 상한 없음 — 다자녀 가정 제한(구 2명 상한)은 제품 결정으로 제거(0713).
    # 남용 방어는 초대코드 자체의 1회용·만료·max_uses가 담당한다.

    # 잔여 횟수 원자적 확보 — 동시에 여러 학부모가 같은 코드를 소비해도 max_uses 초과 불가.
    # (기존 read-modify-write `used_count += 1`은 lost update로 초과 링크가 생겼다.)
    # 행 잠금 UPDATE라 동시 요청은 직렬화되고, 링크 INSERT가 실패하면 같은 트랜잭션이라 함께 롤백된다.
    claimed = db.execute(
        sa_update(ParentInviteCode)
        .where(
            ParentInviteCode.id == row.id,
            ParentInviteCode.used_count < ParentInviteCode.max_uses,
        )
        .values(used_count=ParentInviteCode.used_count + 1)
    )
    if claimed.rowcount == 0:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 사용 횟수를 모두 쓴 코드예요.")

    now = _now()
    link = ParentStudentLink(
        parent_user_id=parent_user_id,
        student_id=row.student_id,
        organization_id=row.organization_id,
        status="approved",  # 초대 코드(학교가 그 가정에만 전달)를 신뢰 근거로 승인
        requested_at=now,
        approved_at=now,
    )
    db.add(link)
    db.commit()
    return link
