import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import (
    create_access_token,
    create_refresh_token,
    generate_email_code,
    hash_password,
    sha256_hash,
    verify_password,
)
from app.email.smtp import render_template, send_email
from app.models import (
    ClassRoom,
    EmailVerificationCode,
    Membership,
    Organization,
    OrgRegistrationRequest,
    RefreshToken,
    StudentJoinCode,
    StudentProfile,
    Subscription,
    User,
)
from app.schemas import auth as s

EMAIL_CODE_TTL_MINUTES = 5
CAPTCHA_FAIL_THRESHOLD = 5  # 이 횟수 이상 연속 실패하면 캡차 요구


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- 로그인 실패 카운터 (5회 이상 실패 → 캡차, 성공 → 리셋) ---
def _throttle_row(db: Session, identifier: str):
    from app.models import LoginThrottle

    row = db.query(LoginThrottle).filter(LoginThrottle.identifier == identifier).first()
    if row is None:
        row = LoginThrottle(identifier=identifier, fail_count=0)
        db.add(row)
        db.flush()
    return row


def _record_fail(db: Session, identifier: str) -> int:
    row = _throttle_row(db, identifier)
    row.fail_count += 1
    db.commit()
    return row.fail_count


def _reset_fails(db: Session, identifier: str) -> None:
    row = _throttle_row(db, identifier)
    if row.fail_count:
        row.fail_count = 0
    db.commit()


def captcha_required(db: Session, identifier: str) -> bool:
    from app.models import LoginThrottle

    row = db.query(LoginThrottle).filter(LoginThrottle.identifier == identifier).first()
    return bool(row and row.fail_count >= CAPTCHA_FAIL_THRESHOLD)


def _login_failed(db: Session, identifier: str, message: str) -> HTTPException:
    """실패 기록 + 5회 이상이면 captcha_required 플래그를 포함한 401 반환."""
    count = _record_fail(db, identifier)
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail={
            "message": message,
            "captcha_required": count >= CAPTCHA_FAIL_THRESHOLD,
            "fail_count": count,
        },
    )


def _require_captcha_if_needed(db: Session, identifier: str, captcha_token: str | None) -> None:
    """5회 이상 실패한 identifier면, 메인 캡차(forest) 통과 토큰을 요구·소비한다.

    자격 검증 '전에' 막는다 — 토큰이 없거나 무효면 401(captcha_required)로 즉시 거부하되
    실패 카운트는 올리지 않는다(캡차 미완료로 하드락까지 밀려 정당 사용자가 잠기는 것 방지).
    유효 토큰은 단일 사용으로 소비되므로, 자격이 또 틀리면 다음 시도엔 새 캡차가 필요하다.
    """
    if not captcha_required(db, identifier):
        return
    from app.models import LoginThrottle
    from app.services import forest_captcha as fc

    if fc.service.consume_token(captcha_token):
        return  # 유효 토큰 소비 — 이 시도를 자격 검증으로 진행 허용
    row = db.query(LoginThrottle).filter(LoginThrottle.identifier == identifier).first()
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail={
            "message": "보안 확인(캡차)을 완료해 주세요.",
            "captcha_required": True,
            "fail_count": row.fail_count if row else CAPTCHA_FAIL_THRESHOLD,
        },
    )


HARD_LOCK_THRESHOLD = 10  # 이 횟수 이상 실패 시 실제 잠금(플래그가 아니라 차단)
LOCK_WINDOW_SECONDS = 900  # 15분 — 이 시간 지나면 자동 해제


def _check_locked(db: Session, identifier: str) -> None:
    """H1: 무제한 시도 방지 — HARD_LOCK_THRESHOLD 도달 시 창(window) 동안 실제 차단(429).

    창이 지나면 카운터를 리셋해 자동 해제(정당 사용자가 영구 잠기지 않도록).
    """
    from app.models import LoginThrottle

    row = db.query(LoginThrottle).filter(LoginThrottle.identifier == identifier).first()
    if row is None or row.fail_count < HARD_LOCK_THRESHOLD:
        return
    # updated_at은 로컬 시각(Timestamps)으로 저장되므로 창 비교도 로컬(datetime.now)로 맞춘다
    last = row.updated_at or row.created_at
    if last and (datetime.now() - last).total_seconds() < LOCK_WINDOW_SECONDS:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": "로그인 시도가 너무 많아요. 잠시 후(약 15분) 다시 시도해 주세요.",
                "locked": True,
            },
        )
    row.fail_count = 0  # 창 경과 → 자동 해제
    db.commit()


# --- 무인증/저비용 엔드포인트 레이트리밋 (이메일/IP 기준, LoginThrottle 재사용) ---
def rate_limit(db: Session, identifier: str, limit: int, window_seconds: int = 3600) -> None:
    """window_seconds 창에서 limit회를 넘으면 429.

    LoginThrottle 행(fail_count)을 카운터로 재사용한다. 마지막 요청 이후 창이 지나면
    카운터를 리셋(슬라이딩) — 정당 사용자가 영구 차단되지 않도록. identifier는
    "emailsend:", "verifyorg:" 등 로그인 실패 카운터와 겹치지 않게 네임스페이스를 준다.
    """
    row = _throttle_row(db, identifier)
    last = row.updated_at or row.created_at
    if last and (datetime.now() - last).total_seconds() >= window_seconds:
        row.fail_count = 0
    if row.fail_count >= limit:
        db.commit()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"message": "요청이 너무 잦아요. 잠시 후 다시 시도해 주세요.", "rate_limited": True},
        )
    row.fail_count += 1
    db.commit()


def issue_tokens(db: Session, subject_id: str, role: str, subject_type: str) -> s.TokenPair:
    access = create_access_token(subject_id, role)
    refresh, expires_at = create_refresh_token(subject_id)
    db.add(
        RefreshToken(
            user_id=subject_id,
            subject_type=subject_type,
            token_hash=sha256_hash(refresh),
            expires_at=expires_at.replace(tzinfo=None),
        )
    )
    db.commit()
    return s.TokenPair(access_token=access, refresh_token=refresh)


def login(db: Session, req: s.LoginRequest) -> s.TokenPair:
    identifier = f"user:{req.email.strip().lower()}"
    _check_locked(db, identifier)  # H1: 과도한 실패 시 실제 차단
    _require_captcha_if_needed(db, identifier, req.captcha_token)  # 5회+ 실패 → 메인 캡차 요구
    user = db.query(User).filter(User.email == req.email.strip().lower()).first()
    if user is None or not verify_password(req.password, user.password_hash):
        raise _login_failed(db, identifier, "이메일 또는 비밀번호가 올바르지 않습니다.")
    # 역할은 계정(이메일 유일)에서 판별한다 — 클라이언트가 보낸 req.role은 무시.
    # 운영자(ops)는 일반 로그인 폼으로 인증할 수 없다 — 전용 경로(/auth/ops-login)만 허용.
    # 존재 여부를 흘리지 않도록 자격 오류와 동일한 메시지로 거부하되, 실패 카운트는
    # 올리지 않는다 — 그렇지 않으면 알려진 ops 이메일로 이 엔드포인트를 두드려
    # ops 계정을 잠그는 교차 락아웃(DoS)이 가능하다.
    if user.role == "ops":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"message": "이메일 또는 비밀번호가 올바르지 않습니다.", "captcha_required": False},
        )
    if user.status == "disabled":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="비활성화된 계정입니다.")
    if user.email_verified_at is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="이메일 인증이 완료되지 않았습니다.")
    _assert_org_approved(db, user)  # 기관 승인 게이트 (ops 승인 전 로그인 차단)
    _reset_fails(db, identifier)
    # last_login_at은 사용자 노출 시각 → created_at과 같은 로컬(KST) 규약. _now()는 토큰용 UTC.
    user.last_login_at = datetime.now()
    db.commit()
    return issue_tokens(db, user.id, user.role, "user")


def _assert_org_approved(db: Session, user: User) -> None:
    """기관 소속 역할(org_admin/teacher/grade_head)은 기관이 승인(active)된 뒤에만 로그인.

    register_org는 기관·관리자·멤버십을 pending으로 만들고, ops 승인 시 active로 전환한다.
    승인 전에는 로그인 자체를 막아야 한다(과거엔 pending이어도 로그인됐다).
    자격증명은 이미 확인했으므로 실패 카운트는 올리지 않고 403(승인 대기)로 거부한다.
    """
    if user.role not in ("org_admin", "teacher", "grade_head"):
        return
    org = db.get(Organization, user.organization_id) if user.organization_id else None
    if org is None or org.status != "active":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="기관 승인 대기 중이에요. 운영팀 승인 후 로그인할 수 있어요.",
        )
    # 멤버십이 있으면 그 상태도 active 여야 한다(없는 계정도 있어 존재할 때만 검사).
    m = (
        db.query(Membership)
        .filter(
            Membership.user_id == user.id,
            Membership.organization_id == user.organization_id,
        )
        .first()
    )
    if m is not None and m.status != "active":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="기관 승인 대기 중이에요. 운영팀 승인 후 로그인할 수 있어요.",
        )


def ops_login(db: Session, req: s.LoginRequest) -> s.TokenPair:
    """운영자 전용 로그인 — 숨겨진 경로(/ops/login → /auth/ops-login)에서만 호출.

    일반 사용자 계정으로는 여기서 토큰을 받을 수 없고(존재 여부도 흘리지 않음),
    운영자 계정은 일반 로그인 폼(/auth/login)으로는 인증되지 않는다.
    """
    identifier = f"user:{req.email.strip().lower()}"
    _check_locked(db, identifier)  # H1: 과도한 실패 시 실제 차단
    _require_captcha_if_needed(db, identifier, req.captcha_token)  # 5회+ 실패 → 메인 캡차 요구
    user = db.query(User).filter(User.email == req.email.strip().lower()).first()
    if (
        user is None
        or user.role != "ops"
        or not verify_password(req.password, user.password_hash)
    ):
        raise _login_failed(db, identifier, "이메일 또는 비밀번호가 올바르지 않습니다.")
    if user.status == "disabled":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="비활성화된 계정입니다.")
    _reset_fails(db, identifier)
    user.last_login_at = datetime.now()  # 사용자 노출 시각 → 로컬(KST) 규약
    db.commit()
    return issue_tokens(db, user.id, user.role, "user")


def student_login(db: Session, req: s.StudentLoginRequest) -> s.TokenPair:
    _check_locked(db, f"student:{req.student_login_id.strip()}")  # H1: 과도한 실패 시 차단
    _require_captcha_if_needed(
        db, f"student:{req.student_login_id.strip()}", req.captcha_token
    )  # 5회+ 실패 → 메인 캡차 요구
    # 탈퇴/비활성 학생은 로그인 차단 (B2) — 성인 로그인과 동일 정책
    query = db.query(StudentProfile).filter(
        StudentProfile.student_login_id == req.student_login_id.strip(),
        StudentProfile.status != "disabled",
    )
    if req.organization_id:
        query = query.filter(StudentProfile.organization_id == req.organization_id)

    # 아이디+비밀번호가 함께 일치하는 계정으로 판별 — 아이디가 여러 기관에 있어도
    # 비밀번호가 하나에만 맞으면 바로 로그인 (기관 선택 불필요)
    matched = [
        st for st in query.limit(5).all() if verify_password(req.password, st.password_hash)
    ]

    identifier = f"student:{req.student_login_id.strip()}"
    if not matched:
        raise _login_failed(db, identifier, "아이디 또는 비밀번호가 올바르지 않습니다.")

    if len(matched) > 1:
        # 아이디+비밀번호까지 동일한 계정이 여러 기관에 존재 — 비밀번호를 증명한
        # 사용자에게만 후보 기관을 보여주고 원클릭 선택하게 한다.
        orgs = {o.id: o.name for o in db.query(Organization).filter(
            Organization.id.in_([st.organization_id for st in matched])
        )}
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "message": "여러 기관에 같은 계정이 있어요. 소속 기관을 눌러 주세요.",
                "candidates": [
                    {
                        "organization_id": st.organization_id,
                        "organization_name": orgs.get(st.organization_id, ""),
                    }
                    for st in matched
                ],
            },
        )

    student = matched[0]
    _reset_fails(db, identifier)
    student.last_login_at = datetime.now()  # 사용자 노출 시각 → 로컬(KST) 규약
    db.commit()
    return issue_tokens(db, student.id, "student", "student")


def refresh_tokens(db: Session, refresh_token: str) -> s.TokenPair:
    from jwt import PyJWTError

    from app.core.security import decode_token

    try:
        payload = decode_token(refresh_token)
    except PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="refresh token이 유효하지 않습니다.")
    if payload.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="refresh token이 아닙니다.")

    token_hash = sha256_hash(refresh_token)
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if row is None or row.revoked_at is not None or row.expires_at < _now():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="만료되었거나 사용할 수 없는 토큰입니다.")

    # 회전: 기존 토큰 폐기 후 새로 발급
    row.revoked_at = _now()
    db.commit()

    subject_id = payload["sub"]
    if row.subject_type == "student":
        # 탈퇴/비활성 학생은 refresh 토큰으로도 재발급 불가 (B3)
        student = db.get(StudentProfile, subject_id)
        if student is None or student.status == "disabled":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="학생 계정을 사용할 수 없습니다.")
        return issue_tokens(db, subject_id, "student", "student")
    user = db.get(User, subject_id)
    if user is None or user.status == "disabled":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="사용자를 찾을 수 없습니다.")
    return issue_tokens(db, subject_id, user.role, "user")


def logout(db: Session, subject_id: str) -> None:
    db.query(RefreshToken).filter(
        RefreshToken.user_id == subject_id, RefreshToken.revoked_at.is_(None)
    ).update({"revoked_at": _now()})
    db.commit()


# --- 이메일 인증 (6자리 코드) ---
def send_email_code(db: Session, email: str, purpose: str, for_account: bool = False) -> None:
    # 계정용 이메일(학부모/교사/기관 가입)은 발송 전에 중복을 먼저 알려준다
    if purpose == "signup" and for_account:
        if db.query(User).filter(User.email == email.strip().lower()).first():
            raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 가입된 이메일입니다.")
    # 발송 폭주/스팸 방지: 이메일 기준 시간당 발송 상한 (IP 기준 상한은 엔드포인트에서)
    rate_limit(db, f"emailsend:{email.strip().lower()}", limit=8, window_seconds=3600)
    code = generate_email_code()
    db.add(
        EmailVerificationCode(
            email=email.strip().lower(),
            purpose=purpose,
            code_hash=sha256_hash(code),
            expires_at=_now() + timedelta(minutes=EMAIL_CODE_TTL_MINUTES),
        )
    )
    db.commit()
    template = "password_reset.html" if purpose == "reset" else "verify_email.html"
    subject = (
        "[CatChap] 비밀번호 재설정 인증 코드" if purpose == "reset" else "[CatChap] 이메일 인증 코드"
    )
    html = render_template(template, code=code, name=email.split("@")[0])
    send_email(db, email, subject, html)


def _find_valid_code(db: Session, email: str, code: str, purpose: str) -> EmailVerificationCode:
    row = (
        db.query(EmailVerificationCode)
        .filter(
            EmailVerificationCode.email == email.strip().lower(),
            EmailVerificationCode.purpose == purpose,
            EmailVerificationCode.code_hash == sha256_hash(code),
            EmailVerificationCode.used_at.is_(None),
        )
        .order_by(EmailVerificationCode.created_at.desc())
        .first()
    )
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="인증 코드가 올바르지 않습니다.")
    if row.expires_at < _now():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="인증 코드가 만료되었어요. 다시 받아주세요.")
    return row


def verify_email_code(db: Session, email: str, code: str, purpose: str) -> None:
    # B2: 6자리 코드 무제한 대입 차단 (이메일+목적 기준 잠금)
    ident = f"emailcode:{email.strip().lower()}:{purpose}"
    _check_locked(db, ident)
    try:
        row = _find_valid_code(db, email, code, purpose)
    except HTTPException:
        _record_fail(db, ident)
        raise
    _reset_fails(db, ident)
    row.verified_at = _now()
    db.commit()


def _consume_verified_code(db: Session, email: str, code: str, purpose: str) -> None:
    """가입/재설정 확정 시 1회 사용 처리 (재사용 방지)"""
    row = _find_valid_code(db, email, code, purpose)
    row.used_at = _now()
    db.commit()


def _ensure_email_unused(db: Session, email: str) -> None:
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 가입된 이메일입니다.")


# --- 회원가입 4종 ---
def register_parent(db: Session, req: s.RegisterParentRequest) -> User:
    email = req.email.strip().lower()
    _ensure_email_unused(db, email)
    _consume_verified_code(db, email, req.email_code, "signup")
    user = User(
        email=email,
        password_hash=hash_password(req.password),
        name=req.name,
        phone=req.phone,
        role="parent",
        email_verified_at=_now(),
    )
    db.add(user)
    db.commit()
    return user


def _assign_pending_class(db: Session, org_id: str, membership: Membership, user_id: str) -> None:
    """교사 초대 시 예약된 담당 반(pending_class)에 담임/보조로 연결. 반이 없으면 생성."""
    from app.models import ClassRoom
    from app.utils.helpers import parse_grade

    cname = (membership.pending_class or "").strip()
    if not cname:
        return
    cls = (
        db.query(ClassRoom)
        .filter(ClassRoom.organization_id == org_id, ClassRoom.name == cname)
        .first()
    )
    if cls is None:
        cls = ClassRoom(organization_id=org_id, name=cname, grade=parse_grade(cname), status="active")
        db.add(cls)
        db.flush()
    if membership.position == "보조":
        cls.assistant_teacher_id = user_id
    else:  # 담임(기본)
        # 담임은 반당 1명 — 가입 시점에 이미 다른(해제 안 된) 담임이 있으면 덮어쓰지 않는다.
        # (예약한 반을 남이 먼저 맡은 경우) 배정만 건너뛰고 가입은 정상 완료 → 관리자가 정리.
        if cls.teacher_id and cls.teacher_id != user_id:
            existing = db.get(User, cls.teacher_id)
            if existing is not None and existing.status != "disabled":
                return
        cls.teacher_id = user_id


def register_teacher(db: Session, req: s.RegisterTeacherRequest) -> User:
    email = req.email.strip().lower()
    membership = (
        db.query(Membership)
        .filter(
            Membership.organization_id == req.organization_id,
            Membership.teacher_code == req.teacher_code.strip().upper(),
        )
        .first()
    )
    if membership is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="교사 개별 코드가 올바르지 않습니다.")

    # 초대 시 이메일을 입력했으면 add_teacher가 pending(placeholder) 계정을 미리 만들어 둔다.
    # 그 경우 새 계정을 만들지 않고 이 placeholder를 '클레임'(비번·이름 설정, 활성화)해야
    # self-가입이 409로 막히지 않는다. 이미 활성 계정이면(코드 재사용) 409.
    placeholder = db.get(User, membership.user_id) if membership.user_id else None
    if placeholder is not None and placeholder.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 사용된 교사 코드입니다.")

    # 이메일 중복 검사 — 클레임할 placeholder 자신은 제외(자기 이메일과의 충돌 방지)
    dup = db.query(User).filter(User.email == email)
    if placeholder is not None:
        dup = dup.filter(User.id != placeholder.id)
    if dup.first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 가입된 이메일입니다.")

    # 초대 링크로 가입하면 초대 메일 수신으로 이메일 소유가 이미 증명됐으므로 인증코드를 생략한다.
    # 그 외(코드로 직접 가입)에는 종전대로 인증된 이메일 코드를 1회 소비한다.
    from app.services import invite_service

    invited = invite_service.check_invite_token(
        db,
        req.invite_token,
        email=email,
        organization_id=req.organization_id,
        teacher_code=req.teacher_code,
    )
    if not invited:
        _consume_verified_code(db, email, req.email_code, "signup")
    if placeholder is not None:
        placeholder.email = email
        placeholder.password_hash = hash_password(req.password)
        placeholder.name = req.name
        placeholder.role = "teacher"
        placeholder.status = "active"
        placeholder.organization_id = req.organization_id
        placeholder.email_verified_at = _now()
        user = placeholder
    else:
        user = User(
            email=email,
            password_hash=hash_password(req.password),
            name=req.name,
            role="teacher",
            organization_id=req.organization_id,
            email_verified_at=_now(),
        )
        db.add(user)
        db.flush()
        membership.user_id = user.id
    # position(담임/교과 등 담당 직책)은 초대는 None, 직접추가는 관리자가 지정한 값 그대로 둔다.
    # (초대는 placeholder User에 이름을 보관하므로 position에 이름이 섞이지 않는다.)
    membership.status = "active"
    membership.joined_at = _now()
    # 초대 시 예약된 담당 반이 있으면 가입 시점에 자동 배정(담임/보조)
    if membership.pending_class:
        _assign_pending_class(db, req.organization_id, membership, user.id)
        membership.pending_class = None
    db.commit()
    return user


def student_id_available(db: Session, login_id: str) -> bool:
    """학생 아이디 전역 중복 확인 (전 기관 대상).

    이미 가입한 학생(student_profiles)뿐 아니라 아직 미사용인 가입 코드에 예약된
    아이디(student_join_codes.login_id)와도 겹치면 안 된다 — 활성화 시점 충돌 방지.
    """
    login_id = login_id.strip()
    if len(login_id) < 3:
        return False
    used_by_student = (
        db.query(StudentProfile).filter(StudentProfile.student_login_id == login_id).first()
    )
    reserved_by_code = (
        db.query(StudentJoinCode).filter(StudentJoinCode.login_id == login_id).first()
    )
    return used_by_student is None and reserved_by_code is None


def suggest_student_ids(db: Session, requested: str, n: int = 4) -> list[str]:
    """중복된 아이디에 대해 사용 가능한 대안을 추천 — 아이가 중복으로 여러 번 막히지 않도록.

    요청 아이디의 어간(끝 숫자 제거)에 작은 번호를 붙여 이미 쓰인 것과 겹치지 않는 후보를 만든다.
    이미 쓰인 아이디는 어간 prefix LIKE 한 번으로 모아 파이썬에서 걸러 쿼리 수를 최소화한다.
    """
    requested = (requested or "").strip().lower()
    if len(requested) < 2:
        return []
    stem = re.sub(r"\d+$", "", requested) or requested  # 끝 숫자 제거한 어간
    stem = stem[:20]
    if len(stem) < 2:
        return []
    like = stem + "%"
    taken: set[str] = set()
    for (lid,) in (
        db.query(StudentProfile.student_login_id)
        .filter(StudentProfile.student_login_id.like(like))
        .all()
    ):
        if lid:
            taken.add(lid.strip().lower())
    for (lid,) in (
        db.query(StudentJoinCode.login_id).filter(StudentJoinCode.login_id.like(like)).all()
    ):
        if lid:
            taken.add(lid.strip().lower())
    out: list[str] = []
    i = 1
    while len(out) < n and i <= 200:
        cand = f"{stem}{i}"
        if len(cand) >= 3 and cand not in taken:
            out.append(cand)
        i += 1
    return out


def register_student(db: Session, req: s.RegisterStudentRequest) -> StudentProfile:
    org = db.get(Organization, req.organization_id)
    if org is None or org.code != req.org_code.strip().upper():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="기관 코드가 올바르지 않습니다.")
    _assert_org_code_not_expired(org)  # 만료된 코드로는 가입 불가

    # 학생 아이디는 전역 유일 (기관 무관) — 가입 화면의 '중복 확인'과 동일 기준
    if not student_id_available(db, req.student_login_id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 사용 중인 아이디예요. 다른 아이디를 골라 주세요.")

    _consume_verified_code(db, req.email.strip().lower(), req.email_code, "signup")

    student = StudentProfile(
        organization_id=org.id,
        student_login_id=req.student_login_id.strip(),
        student_code=_generate_student_code(db),
        password_hash=hash_password(req.password),
        nickname=req.name,
        coins=0,
        level=1,
    )
    db.add(student)
    db.commit()
    return student


# 혼동 문자(0/O, 1/I/L) 제외 고엔트로피 알파벳 — onboarding_service와 통일.
# CAT-XXXXXX (30^6 ≈ 7.3억) → 과거 CAT-1000~9999(9000개) 한계·무한루프 위험 제거.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _generate_student_code(db: Session) -> str:
    """학생 코드 생성 — 시도 횟수 상한(무한루프 제거). 충돌 시 재시도, 초과 시 500."""
    for _ in range(50):
        code = "CAT-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        if not db.query(StudentProfile).filter(StudentProfile.student_code == code).first():
            return code
    raise HTTPException(
        status.HTTP_500_INTERNAL_SERVER_ERROR, detail="학생 코드 생성에 실패했어요. 다시 시도해 주세요."
    )


def _generate_org_code(db: Session, name: str) -> str:
    prefix = "".join(c for c in name if c.isascii() and c.isalnum())[:2].upper() or "CC"
    while True:
        code = f"{prefix}-EDU-{secrets.randbelow(9000) + 1000}"
        if not db.query(Organization).filter(Organization.code == code).first():
            return code


def register_org(db: Session, req: s.RegisterOrgRequest) -> Organization:
    email = req.contact_email.strip().lower()
    _ensure_email_unused(db, email)
    if req.business_number:
        if (
            db.query(Organization)
            .filter(Organization.business_number == req.business_number)
            .first()
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 등록된 고유번호입니다.")

    # 신청 단계 이메일 인증은 선택 — 코드를 함께 보냈을 때만 소비(검증). 신청서 흐름에선 생략.
    if req.email_code:
        _consume_verified_code(db, email, req.email_code, "signup")

    request = OrgRegistrationRequest(
        org_name=req.org_name,
        org_type=req.org_type,
        business_number=req.business_number,
        address=req.address,
        contact_name=req.contact_name,
        contact_email=email,
        contact_phone=req.contact_phone,
        expected_students=req.expected_students,
        plan_interest=req.plan_interest,
    )
    db.add(request)

    # 운영진 승인 대기: 기관·관리자 계정은 만들되 status=pending으로 두고,
    # ops가 승인(approve)해야 active가 되어 로그인·이용 가능. (승인 흐름: ops.py)
    org = Organization(
        name=req.org_name,
        code=_generate_org_code(db, req.org_name),
        org_type=req.org_type,
        status="pending",
        contact_email=email,
        contact_phone=req.contact_phone,
        address=req.address,
        business_number=req.business_number,
        code_expires_at=_now() + timedelta(days=365),
    )
    db.add(org)
    db.flush()

    # 신청서는 pending 유지 — 승인 시 approved로 전환
    request.organization_id = org.id

    # 신청서 흐름: 관리자 계정은 만들되 로그인 불가(pending) 상태로 둔다.
    # 비번을 함께 받았으면(검증 흐름) 그대로 쓰고, 아니면 사용 불가한 임시 해시를 넣는다.
    # email_verified_at 이 None 이면 '자격증명 미발급' 표식 — 승인 시 임시 비번을 발급한다.
    has_password = bool(req.password)
    admin = User(
        email=email,
        password_hash=hash_password(req.password if has_password else secrets.token_urlsafe(24)),
        name=req.contact_name,
        phone=req.contact_phone,
        role="org_admin",
        status="pending",
        organization_id=org.id,
        email_verified_at=_now() if has_password else None,
    )
    db.add(admin)
    db.flush()
    db.add(
        Membership(
            user_id=admin.id,
            organization_id=org.id,
            role="org_admin",
            status="pending",
            joined_at=_now(),
        )
    )

    # 요금제 연결 (관심 요금제 → 구독, 기본 Basic)
    from app.models import Plan

    plan_key = (req.plan_interest or "basic").lower()
    plan = db.query(Plan).filter(Plan.key == plan_key).first() or (
        db.query(Plan).filter(Plan.key == "basic").first()
    )
    if plan:
        db.add(Subscription(organization_id=org.id, plan_id=plan.id))

    db.commit()
    return org


# --- 비밀번호 재설정 ---
def password_reset_request(db: Session, email: str) -> None:
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    # 계정 존재 여부를 노출하지 않기 위해 항상 성공 응답, 존재할 때만 발송
    if user:
        send_email_code(db, email, "reset")


def password_reset_confirm(db: Session, req: s.PasswordResetConfirm) -> None:
    email = req.email.strip().lower()
    # B2: 6자리 재설정 코드 무제한 대입 → 계정 탈취 차단 (이메일 기준 잠금)
    ident = f"reset:{email}"
    _check_locked(db, ident)
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        _record_fail(db, ident)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="인증 코드가 올바르지 않습니다.")
    try:
        _consume_verified_code(db, email, req.code, "reset")
    except HTTPException:
        _record_fail(db, ident)
        raise
    _reset_fails(db, ident)
    user.password_hash = hash_password(req.new_password)
    db.commit()
    logout(db, user.id)  # 모든 기기 로그아웃


# --- 코드 확인 ---
def _assert_org_code_not_expired(org: Organization) -> None:
    """기관 코드가 만료됐으면 가입 차단 (연 1회 갱신 정책)."""
    if org.code_expires_at is not None and org.code_expires_at < datetime.utcnow():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="기관 코드가 만료되었어요. 기관 담당자에게 새 코드를 요청해 주세요.",
        )


def verify_org_code(db: Session, organization_id: str, code: str) -> Organization | None:
    org = db.get(Organization, organization_id)
    if org and org.code == code.strip().upper():
        _assert_org_code_not_expired(org)
        return org
    return None


def verify_teacher_code(db: Session, organization_id: str, code: str) -> bool:
    membership = (
        db.query(Membership)
        .filter(
            Membership.organization_id == organization_id,
            Membership.teacher_code == code.strip().upper(),
            Membership.user_id.is_(None),
        )
        .first()
    )
    return membership is not None


def get_me(db: Session, principal) -> s.MeResponse:
    if principal.kind == "student":
        st: StudentProfile = principal.student
        org = db.get(Organization, st.organization_id)
        cls = db.get(ClassRoom, st.class_id) if st.class_id else None
        return s.MeResponse(
            id=st.id,
            role="student",
            name=st.nickname,
            email=None,
            organization_id=st.organization_id,
            organization_name=org.name if org else None,
            must_change_password=bool(getattr(st, "must_change_password", False)),
            student=s.MeStudent(
                student_login_id=st.student_login_id,
                student_code=st.student_code,
                nickname=st.nickname,
                class_id=st.class_id,
                class_name=cls.name if cls else None,
                grade_band=st.grade_band,
                avatar=st.avatar or {},
                coins=st.coins,
                level=st.level,
                age=st.age,
            ),
        )
    user: User = principal.user
    org = db.get(Organization, user.organization_id) if user.organization_id else None
    # 학년부장이면 담당 학년을 함께 내려 화면 범위 표기("N학년 담당")에 사용
    managed_grade = None
    if user.role == "grade_head" and user.organization_id:
        from app.models import Membership

        m = (
            db.query(Membership)
            .filter(
                Membership.user_id == user.id,
                Membership.organization_id == user.organization_id,
                Membership.status != "disabled",
            )
            .first()
        )
        managed_grade = m.managed_grade if m else None
    return s.MeResponse(
        id=user.id,
        role=user.role,
        name=user.name,
        email=user.email,
        phone=user.phone,
        organization_id=user.organization_id,
        organization_name=org.name if org else None,
        managed_grade=managed_grade,
        must_change_password=bool(getattr(user, "must_change_password", False)),
    )
