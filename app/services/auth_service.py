import logging
import re
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
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
    Consent,
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

_log = logging.getLogger(__name__)

EMAIL_CODE_TTL_MINUTES = 5
# 이 횟수 이상 연속 실패하면 캡차 요구.
#
# 이력: 원래 5회였는데, 정상 사용자가 오타 몇 번으로 걸린다는 이유로 8회로 올렸다
# (당시 실측: 캡차 임계를 넘은 식별자 18개 중 17개가 가입도 안 된 오타·탐색 흔적).
# 2026-08-12 에 5회로 되돌린다 — 조성원 결정.
#
# 되돌리는 대가와 이득을 같이 적어둔다.
#   대가  오타로 캡차를 보는 정상 사용자가 늘어난다. 8회로 올렸던 이유가 그대로 돌아온다.
#   이득  캡차가 더 자주 뜬다 = 행동 궤적이 더 모인다. 지금 판별 모델은 사람당 299세션이
#         있어야 오탐 <=1% 를 증명할 수 있는데 실사용 수집이 하루 1건 수준이라 그게 병목이다.
#
# 하드락(10회/15분)은 그대로다. 캡차 구간이 5~9회로 넓어진다.
CAPTCHA_FAIL_THRESHOLD = 5
# 마지막 실패 후 이 시간이 지나면 캡차 요구를 자동 해제한다(카운터 리셋). captcha_required 참고.
CAPTCHA_DECAY_SECONDS = 1800  # 30분


def _now() -> datetime:
    # 로컬(KST) naive — created_at(app/db/base.py `_now`)과 같은 규약.
    # 예전엔 UTC naive였다: 만료 저장·비교가 자기들끼리는 맞았지만 created_at은 KST라
    # 같은 행 안에서 만료가 생성보다 9시간 이르게 보였고, email_verified_at·joined_at은
    # ops.py가 KST로 쓰는 같은 컬럼이라 경로에 따라 9시간 다른 값이 섞였다.
    return datetime.now()


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
    """캡차를 요구할지 판단한다. 창(window)이 지났으면 카운터를 리셋해 자동 해제한다.

    ★왜 창이 필요한가: 종전엔 fail_count가 '성공 로그인' 외에는 절대 줄지 않아서, 5회 실패한
    사용자는 영원히 캡차를 요구받았다. 반면 하드락(10회)은 15분이면 자동 해제된다 —
    **더 많이 틀린 사람이 더 빨리 풀리는 역전**이었다. 게다가 캡차를 풀 수 없는 사용자
    (키보드·스크린리더)에게는 영구 차단이 된다. 무차별 대입은 초 단위로 오므로 30분 창이
    실질 방어를 낮추지 않는다(하드락 10회/15분은 그대로 유지된다).

    같은 '창 지나면 리셋' 패턴이 _check_locked·rate_limit 에 이미 있어 그 관례를 따른다.
    """
    from app.models import LoginThrottle

    row = db.query(LoginThrottle).filter(LoginThrottle.identifier == identifier).first()
    if row is None or row.fail_count < CAPTCHA_FAIL_THRESHOLD:
        return False
    # updated_at은 로컬 시각(Timestamps)으로 저장되므로 창 비교도 로컬(datetime.now)로 맞춘다
    last = row.updated_at or row.created_at
    if last and (datetime.now() - last).total_seconds() >= CAPTCHA_DECAY_SECONDS:
        row.fail_count = 0
        db.commit()
        return False
    return True


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


def _require_captcha_if_needed(
    db: Session,
    identifier: str,
    captcha_token: str | None,
    captcha_session_id: str | None = None,
    captcha_purpose: str | None = None,
) -> None:
    """5회 이상 실패한 identifier면, 메인 캡차(forest) 통과 토큰을 요구·소비한다.

    자격 검증 '전에' 막는다 — 토큰이 없거나 무효면 401(captcha_required)로 즉시 거부하되
    실패 카운트는 올리지 않는다(캡차 미완료로 하드락까지 밀려 정당 사용자가 잠기는 것 방지).
    유효 토큰은 단일 사용으로 소비되므로, 자격이 또 틀리면 다음 시도엔 새 캡차가 필요하다.
    """
    if not captcha_required(db, identifier):
        return
    from app.core.config import get_settings
    from app.models import LoginThrottle

    # CatChap Guard(성원·민서 캡차) 경로. 프론트가 그 캡차를 쓸 때만 session_id 를 함께
    # 보내므로, 그 값이 있으면 Guard 토큰이다 — 우리가 발급한 토큰이 아니라 캡차 서버에
    # 물어봐야 한다. 프론트 플래그가 꺼져 있으면 이 분기는 아예 안 탄다.
    #
    # 설정이 없으면 500 을 낸다. 여기서 기존 경로로 흘려보내면 Guard 토큰을 자체 캡차
    # 토큰으로 검사하게 되고, 사용자는 캡차를 계속 풀어도 못 들어가는 루프에 빠진다.
    # 설정 누락은 우리 잘못이므로 학생 탓처럼 보이는 401 이 아니라 500 이 맞다(강의 쪽과 동일).
    if captcha_session_id:
        from app.clients import main_captcha_client

        try:
            ok = main_captcha_client.verify_token(
                token=captcha_token,
                session_id=captcha_session_id,
                purpose=captcha_purpose or "login",
            )
        except main_captcha_client.MainCaptchaNotConfiguredError:
            _log.error("메인 캡차 설정 누락 — 로그인 캡차가 Guard 인데 검증할 수 없습니다")
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"message": "인증 서버 설정이 준비되지 않았습니다."},
            ) from None
        if ok:
            return
        # 실패는 아래 공통 401 로 떨어진다 — 새 문제를 받아 다시 풀면 된다.

    # 메인 캡차 교체: 플래그가 켜지면 forest 대신 우리 드래그 캡차 토큰을 소비한다(자체 완결).
    # 로그인 요청엔 session_id가 없어 토큰 유효성만으로 소비(발급된 토큰만 존재하므로 안전).
    elif get_settings().DRAG_CAPTCHA_ENABLED:
        from app.services import drag_captcha_service as dc

        if dc.verify_and_consume_token(captcha_token):
            return
    else:
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
    # role을 넘겨 만료를 역할별로 — 운영자 8시간, 그 외 14일. issue_tokens는 로그인·회전
    # 양쪽의 단일 발급 지점이라 여기 한 곳만 고치면 재발급까지 같은 규칙을 탄다.
    refresh, expires_at = create_refresh_token(subject_id, role)
    db.add(
        RefreshToken(
            user_id=subject_id,
            subject_type=subject_type,
            token_hash=sha256_hash(refresh),
            # create_refresh_token은 aware UTC를 준다 — astimezone()으로 로컬(KST)에
            # 맞춘 뒤 naive로 떨어뜨려야 _now()·created_at과 같은 규약이 된다.
            # (replace(tzinfo=None)만 하면 UTC 벽시계가 그대로 남아 9시간 이르게 만료된다)
            expires_at=expires_at.astimezone().replace(tzinfo=None),
        )
    )
    db.commit()
    return s.TokenPair(access_token=access, refresh_token=refresh)


def login(db: Session, req: s.LoginRequest) -> s.TokenPair:
    identifier = f"user:{req.email.strip().lower()}"
    _check_locked(db, identifier)  # H1: 과도한 실패 시 실제 차단
    _require_captcha_if_needed(
        db, identifier, req.captcha_token, req.captcha_session_id, req.captcha_purpose
    )  # 5회+ 실패 → 메인 캡차 요구
    user = db.query(User).filter(User.email == req.email.strip().lower()).first()
    if user is None or not verify_password(req.password, user.password_hash):
        raise _login_failed(db, identifier, "이메일 또는 비밀번호가 올바르지 않습니다.")
    # 역할은 계정(이메일 유일)에서 판별한다 — 클라이언트가 보낸 req.role은 무시.
    # 운영자(ops)·강사(instructor)는 일반 로그인 폼으로 인증할 수 없다 — 전용
    # 경로(/auth/ops-login)만 허용. 존재 여부를 흘리지 않도록 자격 오류와 동일한
    # 메시지로 거부하고, 이 분기(비밀번호는 맞고 역할만 콘솔 계정)는 실패 카운트를
    # 올리지 않는다. 단, 오답 자체는 위의 _login_failed가 공유 카운터(user:{email})를
    # 이미 올린다 — 콘솔 이메일을 아는 공격자의 오답 스팸 잠금은 여기서 못 막는다(기존 속성).
    if user.role in ("ops", "instructor"):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"message": "이메일 또는 비밀번호가 올바르지 않습니다.", "captcha_required": False},
        )
    if user.status == "disabled":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="비활성화된 계정입니다.")
    if user.email_verified_at is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="이메일 인증이 완료되지 않았습니다.")
    _assert_role_matches(user, req.role)  # 로그인 탭(역할 구분)과 계정 역할 일치 강제
    _assert_org_approved(db, user)  # 기관 승인 게이트 (ops 승인 전 로그인 차단)
    _reset_fails(db, identifier)
    # last_login_at은 사용자 노출 시각 → created_at과 같은 로컬(KST) 규약. _now()는 토큰용 UTC.
    user.last_login_at = datetime.now()
    db.commit()
    return issue_tokens(db, user.id, user.role, "user")


# 로그인 탭(역할 구분) → 허용 계정 역할. 'org' 탭은 기관 그룹 전체(교사 포함 — 가입도 기관 탭).
_LOGIN_ROLE_GROUPS: dict[str, set[str]] = {
    "parent": {"parent"},
    "org": {"org_admin", "grade_head", "teacher"},
    "org_admin": {"org_admin"},
    "grade_head": {"grade_head"},
    "teacher": {"teacher"},
}
_ROLE_LABEL = {
    "parent": "학부모", "teacher": "선생님", "org_admin": "기관 관리자", "grade_head": "학년부장",
}


def _assert_role_matches(user: User, req_role: str | None) -> None:
    """선택한 로그인 구분과 계정 역할 일치 강제 — 학부모 탭에서 교사 계정이 교사로
    로그인되던 혼선 차단. 비밀번호는 이미 검증됐으므로(본인) 계정 종류를 알려줘도
    존재 노출이 아니고, 실패 카운트도 올리지 않는다. req_role 미지정은 하위호환 허용."""
    if not req_role:
        return
    allowed = _LOGIN_ROLE_GROUPS.get(req_role)
    if allowed is None:
        return  # 알 수 없는 구분 값은 강제하지 않음 (구 클라이언트 관용)
    if user.role not in allowed:
        label = _ROLE_LABEL.get(user.role, user.role)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"이 계정은 {label} 계정이에요. 알맞은 탭에서 다시 로그인해 주세요.",
        )


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


def _assert_temp_password_not_expired(user: User) -> None:
    """임시 비번(강제 변경 대상)이 만료됐으면 로그인 차단 — 재발급 필요.

    발급 시 password_reset_expires_at을 미래로 심고, 첫 로그인 전 방치되면 그 시각을 지나
    로그인 자체가 막힌다(메일 유출 시 무기한 사용 방지). 비번을 바꾸면(must_change_password
    해제 + 만료 None) 이 검사는 더 이상 걸리지 않는다. 자격 증명은 이미 확인한 뒤라 존재
    노출이 아니다(만료 컬럼이 없는/미설정 계정은 통과 — 운영자 등 기존 동작 무영향)."""
    exp = getattr(user, "password_reset_expires_at", None)
    if user.must_change_password and exp is not None and datetime.now() > exp:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="임시 비밀번호가 만료되었어요. 운영자에게 재발급을 요청해 주세요.",
        )


def ops_login(db: Session, req: s.LoginRequest) -> s.TokenPair:
    """운영자·강사 로그인 — 전용 경로(/ops/login → /auth/ops-login)와 공개 로그인 폼
    (/login → /auth/public-login 통합 폴백) 양쪽 모두에서 호출된다(사용자 결정 2026-07-26:
    운영자·강사도 공개 폼에서 로그인 가능. 종전에는 운영자를 공개 폼에서 제외했으나 지금은
    두 role 모두 이메일+비밀번호만 일치하면 어느 진입구로도 로그인된다).

    학생 계정(StudentProfile)은 이 함수가 아예 조회하지 않는 User 테이블 기반이라, 어느
    경로로 호출되든 학생 자격증명으로는 로그인되지 않는다 — /ops/login에서 학생 계정
    거부는 이 테이블 분리로 이미 보장된다(별도 role 체크 불필요).
    """
    identifier = f"user:{req.email.strip().lower()}"
    _check_locked(db, identifier)  # H1: 과도한 실패 시 실제 차단
    _require_captcha_if_needed(
        db, identifier, req.captcha_token, req.captcha_session_id, req.captcha_purpose
    )  # 5회+ 실패 → 메인 캡차 요구
    allowed_roles = ("ops", "instructor")
    user = (
        db.query(User)
        .filter(User.email == req.email.strip().lower(), User.role.in_(allowed_roles))
        .first()
    )
    # 임시 비밀번호는 이메일에서 복사해 붙여넣는 흐름이라 앞뒤 공백·개행이 섞이기 쉽다.
    # 원문 실패 시 strip본을 한 번 더 대조한다(공백 패딩만 허용 — 보안 영향 무시 수준).
    pw_ok = user is not None and (
        verify_password(req.password, user.password_hash)
        or (req.password.strip() != req.password and verify_password(req.password.strip(), user.password_hash))
    )
    if user is None or not pw_ok:
        raise _login_failed(db, identifier, "이메일 또는 비밀번호가 올바르지 않습니다.")
    if user.status == "disabled":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="비활성화된 계정입니다.")
    _assert_temp_password_not_expired(user)  # 임시 비번 72h 만료 시 로그인 차단(재발급 필요)
    _reset_fails(db, identifier)
    user.last_login_at = datetime.now()  # 사용자 노출 시각 → 로컬(KST) 규약
    db.commit()
    return issue_tokens(db, user.id, user.role, "user")


def student_login(db: Session, req: s.StudentLoginRequest) -> s.TokenPair:
    _check_locked(db, f"student:{req.student_login_id.strip()}")  # H1: 과도한 실패 시 차단
    _require_captcha_if_needed(
        db, f"student:{req.student_login_id.strip()}", req.captcha_token,
        req.captcha_session_id, req.captcha_purpose,
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


def public_login(db: Session, req: s.StudentLoginRequest) -> s.TokenPair:
    """공개 로그인 폼(/login)의 단일 진입 — 학생·강사·운영자를 서버가 판별한다
    (2026-07-20 도입, 2026-07-26: 운영자도 이 폼에서 로그인 가능하도록 확장).

    종전에는 프론트가 학생 로그인 실패 시 강사 로그인을 재시도(try-then-fallback)했다.
    그 라우팅을 서버로 옮겨 요청 1회·일관된 실패처리로 정리한다.

    판별 규칙: 해당 login_id의 (비활성 아님) 학생이 존재하면 학생 경로에 그대로 위임한다
    — 다기관 409·오답 처리·캡차가 기존 student_login과 완전히 동일하다(학생 무영향).
    학생이 아니고 이메일 형태면 운영자·강사 로그인으로 폴백한다(ops_login — 두 role 모두
    허용). 존재 확인은 비밀번호를 대조하지 않으므로(카운터 증가 없음) 그 이메일이 실재
    학생의 실패 카운터를 오염시키지 않는다.
    """
    login_id = req.student_login_id.strip()
    is_student = (
        db.query(StudentProfile.id)
        .filter(
            StudentProfile.student_login_id == login_id,
            StudentProfile.status != "disabled",
        )
        .first()
        is not None
    )
    if is_student or "@" not in login_id:
        # 학생이거나 이메일이 아니면 학생 경로로 — 후자는 통일된 학생 오류로 실패(존재 미노출).
        return student_login(db, req)
    # 학생이 아닌 이메일 → 운영자·강사 로그인. 실패 시 그 오류 그대로 전파.
    return ops_login(
        db,
        s.LoginRequest(
            email=login_id,
            password=req.password,
            captcha_token=req.captcha_token,
            captcha_session_id=req.captcha_session_id,
            captcha_purpose=req.captcha_purpose,
            public=True,
        ),
    )


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
    # 계정용 이메일(학부모/교사/기관 가입)은 발송 전에 중복을 먼저 알려준다.
    # 학생 아이디까지 한 네임스페이스로 본다 — 코드를 보내봐야 가입 단계에서 막힌다.
    if purpose == "signup" and for_account:
        if login_identifier_taken(db, email):
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
        "[CatChap] 비밀번호 재설정 인증 코드"
        if purpose == "reset"
        # 보호자(법정대리인) 동의 코드 — 만 14세 미만 학생 가입 게이트(연령 분기)
        else "[CatChap] 보호자 동의 인증 코드" if purpose == "guardian"
        else "[CatChap] 이메일 인증 코드"
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


# --- 회원가입 — 학생(이메일+연령 게이트)만 live. 학부모/교사/기관은 은퇴(410 스텁) ---
def register_parent(db: Session, req: s.RegisterParentRequest) -> User:
    # 제품 전환(2026-07-18, 학부모 역할 은퇴): 학부모 신규 가입 접수 종료. 만 14세 미만의
    # 법정대리인 동의는 학생 가입 게이트(보호자 이메일 코드 + Consent + guardian_email)가
    # 담당한다 — 보호자 '계정'은 법적 요건이 아니다. 기존 학부모 계정 로그인·데이터는
    # 심층 정리 단계까지 유지. 아래 기존 코드는 이력 보존용으로 남긴다.
    raise HTTPException(
        status.HTTP_410_GONE,
        detail="학부모 가입 접수가 종료되었어요. 만 14세 미만 자녀의 가입 동의는 자녀 가입 화면에서 보호자 이메일 인증으로 진행돼요.",
    )


def register_teacher(db: Session, req: s.RegisterTeacherRequest) -> User:
    # 제품 전환(2026-07-17, 학교 기능 은퇴): 교사 신규 가입 접수 종료.
    # 강의 제작자는 강사(instructor) — 운영자 초대 발급. 종전 클레임 코드는 git 이력 참고.
    raise HTTPException(
        status.HTTP_410_GONE,
        detail="교사 신규 가입 접수가 종료되었어요. CatChap은 개인 학습자 대상 강의 서비스로 전환되었습니다.",
    )


def normalize_login_id(login_id: str) -> str:
    """로그인 식별자 정규화 — 저장·비교 모두 이 값을 쓴다.

    이메일 가입에서 아이디 = 이메일이므로 대소문자는 의미가 없다. 소문자로 통일하지
    않으면 `STUDENT@cat.dev` 가 `student@cat.dev` 와 다른 계정으로 새로 만들어진다
    (SQLite는 비교·UNIQUE 인덱스가 대소문자를 구분한다).
    """
    return login_id.strip().lower()


def login_identifier_taken(db: Session, login_id: str) -> bool:
    """로그인 식별자가 이미 쓰이고 있는지 — 학생·성인 계정을 한 네임스페이스로 본다.

    학생(student_profiles.student_login_id)과 성인 계정(users.email)은 테이블이 다르지만
    로그인 입력창은 하나다. 각자 자기 테이블만 검사하면 같은 이메일로 학생과 강사가
    동시에 존재할 수 있고, 그러면 public_login이 '학생이 있으면 학생 경로'로 판별하므로
    강사·운영자가 자기 계정으로 로그인하지 못한다(실측 재현). 미사용 가입 코드에 예약된
    아이디도 활성화 시점에 충돌하므로 함께 본다.

    비교는 대소문자 무시(func.lower) — MySQL은 기본 collation(_ci)이 이미 그렇게 동작하고,
    SQLite는 구분하므로 여기서 명시적으로 맞춘다.
    """
    key = normalize_login_id(login_id)
    return (
        db.query(StudentProfile.id)
        .filter(func.lower(StudentProfile.student_login_id) == key)
        .first()
        is not None
        or db.query(StudentJoinCode.id).filter(func.lower(StudentJoinCode.login_id) == key).first()
        is not None
        or db.query(User.id).filter(func.lower(User.email) == key).first() is not None
    )


def student_id_available(db: Session, login_id: str) -> bool:
    """학생 아이디 전역 중복 확인 (전 기관 + 성인 계정 이메일까지)."""
    if len(login_id.strip()) < 3:
        return False
    return not login_identifier_taken(db, login_id)


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
    # 성인 계정 이메일과도 겹치면 안 된다(login_identifier_taken과 같은 네임스페이스) —
    # 여기서 빼지 않으면 추천대로 골랐는데 가입에서 409로 막히는 모순이 생긴다.
    for (mail,) in db.query(User.email).filter(User.email.like(like)).all():
        if mail:
            taken.add(mail.strip().lower())
    out: list[str] = []
    i = 1
    while len(out) < n and i <= 200:
        cand = f"{stem}{i}"
        if len(cand) >= 3 and cand not in taken:
            out.append(cand)
        i += 1
    return out


GUARDIAN_CONSENT_AGE = 14  # 만 나이 기준 — 미만이면 법정대리인 동의 필수 (개인정보보호법)


def _age_on(today, birth) -> int:
    """만 나이 — 생일이 안 지났으면 1 뺀다. (date 두 개를 받는다)"""
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def register_student(db: Session, req: s.RegisterStudentRequest) -> StudentProfile:
    """학생 가입 — 이메일 가입 전환(2026-07-16): 학부모 가입과 동일 구성이 기본.

    organization_id를 주면 종전 기관 코드 검증 가입 그대로(기관 경유 가입 부활 대비 유지),
    안 주면 무소속(organization_id=None) 가입. student_login_id 미지정 시
    이메일(소문자·strip)이 로그인 아이디가 된다 — 새 email 컬럼·로그인 경로 변경 없음.

    연령 분기(2026-07-17, 성인+아동 서비스 전환): 생년월일 필수 수집. 만 14세 미만은
    보호자(법정대리인) 이메일로 받은 인증 코드 없이는 가입이 완료되지 않는다 —
    동의 증빙은 Consent(signup_guardian) + guardian_email로 남긴다.
    """
    email = req.email.strip().lower()

    # 생년월일 검증 — 미래·비현실 값 거부. today는 로컬(KST) 규약(datetime.now()).
    today = datetime.now().date()
    birth = req.birth_date
    if birth > today or birth.year < 1900:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="생년월일이 올바르지 않습니다.")
    age = _age_on(today, birth)

    guardian_email: str | None = None
    if age < GUARDIAN_CONSENT_AGE:
        guardian_email = (req.guardian_email or "").strip().lower()
        if not guardian_email or not (req.guardian_email_code or "").strip():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="만 14세 미만은 보호자(법정대리인) 이메일 동의가 필요해요. 보호자 이메일로 받은 인증 코드를 입력해 주세요.",
            )
        if guardian_email == email:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="보호자 이메일은 본인 이메일과 달라야 해요.",
            )

    org = None
    if req.organization_id:
        org = db.get(Organization, req.organization_id)
        if org is None or org.code != (req.org_code or "").strip().upper():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="기관 코드가 올바르지 않습니다.")
        _assert_org_code_not_expired(org)  # 만료된 코드로는 가입 불가

    # 학생 아이디는 전역 유일 (기관 무관, 성인 계정 이메일과도 겹치면 안 됨)
    # — 이메일 가입이면 이메일 자체가 아이디. 저장 전 소문자로 정규화한다.
    login_id = normalize_login_id(req.student_login_id or email)
    if not student_id_available(db, login_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                "이미 사용 중인 아이디예요. 다른 아이디를 골라 주세요."
                if req.student_login_id
                else "이미 가입된 이메일이에요. 로그인하거나 다른 이메일을 사용해 주세요."
            ),
        )

    _consume_verified_code(db, email, req.email_code, "signup")
    # 보호자 코드는 학생 코드 검증 뒤, 계정 생성 전에 소비 — 실패 시 계정이 안 생긴다.
    if age < GUARDIAN_CONSENT_AGE:
        _consume_verified_code(db, guardian_email, req.guardian_email_code, "guardian")

    student = StudentProfile(
        organization_id=org.id if org else None,
        student_login_id=login_id,
        student_code=_generate_student_code(db),
        password_hash=hash_password(req.password),
        nickname=req.name,
        birth_date=birth,
        guardian_email=guardian_email,
        coins=0,
        level=1,
    )
    db.add(student)
    if age < GUARDIAN_CONSENT_AGE:
        db.flush()  # Consent가 student.id를 참조
        db.add(
            Consent(
                student_id=student.id,
                organization_id=org.id if org else None,
                granted_by_user_id=None,  # 보호자 계정 없음 — 증빙은 guardian_email 코드 인증
                consent_type="signup_guardian",
                terms_version="v1",
                granted_at=datetime.now(),
            )
        )
    # 위의 중복 검사와 INSERT 사이에 다른 요청이 같은 아이디를 선점할 수 있다
    # (가입 버튼 더블클릭 등). student_login_id UNIQUE 인덱스가 최종 방어선이므로,
    # 그 위반은 500이 아니라 위와 같은 409로 사용자에게 돌려준다.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="이미 가입된 이메일이에요. 로그인하거나 다른 이메일을 사용해 주세요.",
        ) from None
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
    # 제품 전환(2026-07-17, 학교 기능 은퇴): 기관(학교) 신규 등록 접수 종료. 기존 기관
    # 계정 로그인·데이터는 유지(정리는 3단계). 아래 기존 코드는 이력 보존용으로 남긴다.
    raise HTTPException(
        status.HTTP_410_GONE,
        detail="기관(학교) 신규 등록 접수가 종료되었어요. CatChap은 개인 학습자 대상 강의 서비스로 전환되었습니다.",
    )
    email = req.contact_email.strip()



def password_reset_request(db: Session, email: str) -> None:
    """비밀번호 재설정 코드를 발송한다.

    ★이 함수는 없었다 — 엔드포인트(/auth/password-reset/request)가 부르고 있었는데 정의가
    빠져 있어서 **프로덕션에서 모든 재설정 요청이 AttributeError로 500**이었다(2026-07-27 확인).
    즉 "비밀번호를 잊으셨나요?"가 아무에게도 동작하지 않았다.

    학생도 여기서 함께 처리한다: 학생은 users에 없지만 2026-07-16 이후 가입자는 로그인
    아이디가 곧 이메일이라 같은 주소로 코드를 보낼 수 있다. 사용자가 '나는 학생인가 강사인가'를
    스스로 구분해 다른 화면을 찾아갈 필요가 없도록 한 입구에서 받는다.

    계정 존재 여부를 응답으로 흘리지 않기 위해 어떤 경우에도 조용히 반환한다(호출부는 항상 200).
    """
    email = email.strip().lower()
    # 존재 확인 전에 걸어 계정 열거·발송 폭주를 함께 막는다.
    rate_limit(db, f"pwresetreq:{email}", limit=5, window_seconds=3600)
    known = db.query(User).filter(User.email == email).first() is not None
    if not known:
        known = _student_for_reset(db, email) is not None
    if not known:
        return
    send_email_code(db, email, "reset")


def password_reset_confirm(db: Session, req: s.PasswordResetConfirm) -> None:
    email = req.email.strip().lower()
    # B2: 6자리 재설정 코드 무제한 대입 → 계정 탈취 차단 (이메일 기준 잠금)
    ident = f"reset:{email}"
    _check_locked(db, ident)
    user = db.query(User).filter(User.email == email).first()
    # 학생은 users에 없다 — 같은 이메일의 학생이 있으면 학생 재설정으로 넘긴다(위 발송과 대칭).
    if user is None and _student_for_reset(db, email) is not None:
        student_password_reset_confirm(
            db,
            s.StudentPasswordResetConfirm(
                student_login_id=email, code=req.code, new_password=req.new_password
            ),
        )
        return
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
    # ★로그인 실패 카운터도 함께 푼다 — 비밀번호를 새로 정했는데 캡차 게이트에 막혀 못 들어가면
    # 재설정이 무의미하다. 이메일 코드 소지는 캡차보다 강한 본인 증명이므로 보안 하향이 아니다.
    _reset_fails(db, f"user:{email}")
    db.commit()
    logout(db, user.id)  # 모든 기기 로그아웃


# --- 학생 비밀번호 재설정 ---
# 왜 별도 흐름인가: 학생은 users 테이블이 아니라 student_profiles에 있고 식별자도 이메일이
# 아니라 student_login_id다. 그래서 위의 password_reset_* 을 탈 수 없었고, 결과적으로
# **학생은 비밀번호를 잊으면 복구 수단이 전혀 없었다**(교사·운영자 대행 기능도 없었음).
#
# ★한계(반드시 인지할 것): 학생에게는 이메일 컬럼이 없다. 2026-07-16 이후 가입자는
# 로그인 아이디가 곧 이메일이라 그 주소로 보낼 수 있지만, 그 이전 가입자(실측 56명 중 47명)는
# 보낼 주소가 없다. 그들의 복구 경로는 운영자 임시 비밀번호 발급(ops)뿐이다.
def _student_for_reset(db: Session, login_id: str) -> StudentProfile | None:
    return (
        db.query(StudentProfile)
        .filter(
            StudentProfile.student_login_id == login_id,
            StudentProfile.status != "disabled",
        )
        .first()
    )


def student_password_reset_request(db: Session, login_id: str) -> None:
    """재설정 코드를 학생 이메일(=로그인 아이디)로 발송한다.

    계정 존재 여부를 흘리지 않기 위해 어떤 경우에도 조용히 반환한다(호출부는 항상 200).
    """
    login_id = login_id.strip()
    # 존재 확인 전에 걸어 계정 열거·발송 폭주를 함께 막는다.
    rate_limit(db, f"sturesetreq:{login_id.lower()}", limit=5, window_seconds=3600)
    if "@" not in login_id:  # 이메일 형태가 아니면 보낼 주소가 없다
        return
    if _student_for_reset(db, login_id) is None:
        return
    send_email_code(db, login_id, "reset")


def student_password_reset_confirm(db: Session, req: s.StudentPasswordResetConfirm) -> None:
    login_id = req.student_login_id.strip()
    # 6자리 코드 무제한 대입 차단(아이디 기준 잠금) — User 재설정과 같은 방식.
    ident = f"streset:{login_id.lower()}"
    _check_locked(db, ident)
    student = _student_for_reset(db, login_id) if "@" in login_id else None
    if student is None:
        _record_fail(db, ident)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="인증 코드가 올바르지 않습니다.")
    try:
        _consume_verified_code(db, login_id, req.code, "reset")
    except HTTPException:
        _record_fail(db, ident)
        raise
    _reset_fails(db, ident)
    student.password_hash = hash_password(req.new_password)
    student.must_change_password = False
    # 로그인 실패 카운터도 함께 초기화(위 User 흐름과 같은 이유).
    _reset_fails(db, f"student:{login_id}")
    db.commit()
    logout(db, student.id)  # 모든 기기 로그아웃


# --- 코드 확인 ---
def _assert_org_code_not_expired(org: Organization) -> None:
    """기관 코드가 만료됐으면 가입 차단 (연 1회 갱신 정책)."""
    if org.code_expires_at is not None and org.code_expires_at < _now():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="기관 코드가 만료되었어요. 기관 담당자에게 새 코드를 요청해 주세요.",
        )


def get_me(db: Session, principal) -> s.MeResponse:
    if principal.kind == "student":
        st: StudentProfile = principal.student
        # 이메일 가입 학생은 무소속(organization_id=None) — db.get(None) 예외 방지
        org = db.get(Organization, st.organization_id) if st.organization_id else None
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
