"""소셜 로그인 오케스트레이션 — 계정 연결·신규 가입 판정·연령 게이트.

provider HTTP 규격은 services/social_auth.py가, 여기서는 "이 소셜 계정을 우리 어느
학생으로 볼 것인가"만 결정한다.

흐름(인가 코드 그랜트):
  1) GET  /auth/social/{provider}/authorize  → 서버가 state를 서명 발급 + authorize URL
  2) 사용자가 provider 화면에서 동의 → redirect_uri 로 code·state 반환
  3) POST /auth/social/{provider}/callback   → 토큰 교환·프로필 조회 후 셋 중 하나
       · logged_in       이미 연결된 소셜 계정 → 바로 토큰 발급
       · logged_in       검증된 이메일이 기존 학생과 일치 → 그 계정에 연결하고 토큰 발급
       · signup_required 신규 → signup_token(15분)만 주고 **계정은 아직 만들지 않는다**
  4) POST /auth/social/signup                → 생년월일 확인 후 계정 생성 + 토큰 발급

★왜 신규 가입을 두 단계로 나눴나 (이 설계의 핵심):
이 서비스는 만 14세 미만이면 보호자 동의 없이는 가입할 수 없다(register_student의
GUARDIAN_CONSENT_AGE 게이트). 그런데 소셜 provider는 생년월일을 안 줄 수 있다
(구글은 아예 없고, 카카오·네이버는 선택 동의다). 콜백에서 곧바로 계정을 만들면 생년월일
없는 계정이 생겨 **연령 게이트가 조용히 우회된다.** 그래서 콜백은 서명된 signup_token만
주고, 생년월일을 받은 뒤에야 계정을 만든다 — 반쯤 만들어진 계정도, 지웠다 만드는 보정도
생기지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import jwt
from fastapi import HTTPException, status
from jwt import PyJWTError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import SocialAccount, StudentProfile, User
from app.schemas import auth as s
from app.services import auth_service
from app.services.social_auth import (
    PROVIDER_LABELS,
    PROVIDERS,
    SocialAuthError,
    SocialProfile,
    build_provider,
    is_configured,
)

# state 유효기간 — provider 동의 화면에 머무는 시간(사람이 버튼 누르는 시간)만 있으면 된다.
STATE_TTL_SECONDS = 600  # 10분
# 신규 가입 토큰 — 생년월일 입력 화면 체류 시간.
SIGNUP_TOKEN_TTL_SECONDS = 900  # 15분

# 소셜 전용 계정의 password_hash 자리값. bcrypt 형식이 아니므로 verify_password가
# 항상 False를 돌려준다(ValueError → False) — 즉 이 값으로는 어떤 비밀번호로도
# 로그인할 수 없다. NULL을 쓰지 않는 이유는 컬럼이 NOT NULL이고, 빈 문자열은 '설정 안 함'과
# '빈 비밀번호'를 구별하기 어렵기 때문이다.
UNUSABLE_PASSWORD = "!social-login-only"


def _now() -> datetime:
    return datetime.now()  # 로컬(KST) naive — created_at 규약과 통일


def has_password(student: StudentProfile) -> bool:
    """비밀번호 로그인이 가능한 계정인가 — 소셜 연결 해제 가능 여부 판정에 쓴다."""
    return bool(student.password_hash) and student.password_hash != UNUSABLE_PASSWORD


# ---------------------------------------------------------------- redirect_uri 허용목록
def allowed_redirect_uris(settings=None) -> list[str]:
    """콜백을 받을 수 있는 프론트 주소 목록.

    허용목록이 없으면 오픈 리다이렉트가 된다 — 공격자가 자기 사이트를 redirect_uri로 넣어
    인가 코드를 가로챌 수 있다. 설정이 비어 있으면 FRONTEND_URL 기반 기본값만 허용한다.
    """
    settings = settings or get_settings()
    raw = (getattr(settings, "SOCIAL_REDIRECT_URIS", "") or "").strip()
    if raw:
        return [u.strip() for u in raw.split(",") if u.strip()]
    front = (settings.FRONTEND_URL or "").rstrip("/")
    return [f"{front}/auth/social/callback"] if front else []


def _resolve_redirect_uri(requested: str | None) -> str:
    """요청한 redirect_uri를 허용목록과 대조 — 미지정이면 첫 번째 기본값."""
    allowed = allowed_redirect_uris()
    if not allowed:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="소셜 로그인 콜백 주소가 설정되지 않았어요.",
        )
    if not requested:
        return allowed[0]
    uri = requested.strip()
    if uri not in allowed:
        # 어떤 주소가 허용인지 알려 주지 않는다(설정 탐색 방지).
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="허용되지 않은 콜백 주소예요.")
    return uri


# ---------------------------------------------------------------- 서명 토큰
def _sign(payload: dict, token_type: str, ttl: int) -> str:
    settings = get_settings()
    body = dict(payload)
    body.update(
        {
            "type": token_type,
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(seconds=ttl),
        }
    )
    return jwt.encode(body, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _decode(token: str, token_type: str, expired_message: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=expired_message) from None
    except PyJWTError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="소셜 로그인 정보가 올바르지 않아요."
        ) from None
    if payload.get("type") != token_type:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="소셜 로그인 정보가 올바르지 않아요."
        )
    return payload


def sign_state(provider: str, redirect_uri: str) -> str:
    """CSRF 방지용 state — 서버 비밀키로 서명해 위조를 막는다.

    별도 저장소 없이 서명만으로 무결성을 보장하고, redirect_uri를 안에 담아 콜백에서
    같은 값을 강제한다(콜백 파라미터로 온 주소를 그대로 믿지 않는다). 재사용 방지는
    provider가 발급하는 인가 코드가 1회용이라는 성질에 의존한다 — 코드 없이는 state만
    재사용해도 아무것도 얻지 못한다.
    """
    return _sign({"provider": provider, "redirect_uri": redirect_uri}, "social_state", STATE_TTL_SECONDS)


def verify_state(state: str, provider: str) -> str:
    payload = _decode(state, "social_state", "로그인 시간이 만료됐어요. 다시 시도해 주세요.")
    if payload.get("provider") != provider:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="소셜 로그인 정보가 올바르지 않아요.")
    return str(payload.get("redirect_uri") or "")


def _sign_signup_token(profile: SocialProfile) -> str:
    return _sign(
        {
            "provider": profile.provider,
            "uid": profile.provider_user_id,
            "email": profile.email,
            "email_verified": profile.email_verified,
            "nickname": profile.nickname,
            "birth": profile.birth_date.isoformat() if profile.birth_date else None,
        },
        "social_signup",
        SIGNUP_TOKEN_TTL_SECONDS,
    )


# ---------------------------------------------------------------- 1단계: authorize
def authorize(provider: str, redirect_uri: str | None) -> dict:
    _assert_supported(provider)
    uri = _resolve_redirect_uri(redirect_uri)
    state = sign_state(provider, uri)
    adapter = _adapter(provider)
    return {"provider": provider, "authorize_url": adapter.authorize_url(uri, state), "state": state}


def _assert_supported(provider: str) -> None:
    if provider not in PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="지원하지 않는 소셜 로그인이에요.")
    if not is_configured(get_settings(), provider):
        # 가짜 성공 금지 — 키가 없는데 버튼만 동작하는 척하지 않는다.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{PROVIDER_LABELS[provider]} 로그인이 아직 설정되지 않았어요.",
        )


def _adapter(provider: str, client: httpx.Client | None = None):
    try:
        return build_provider(get_settings(), provider, client=client)
    except SocialAuthError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message) from exc


def available_providers() -> list[dict]:
    """프론트가 어떤 버튼을 그릴지 판단할 목록 — 설정된 provider만 enabled=true."""
    settings = get_settings()
    return [
        {"provider": p, "label": PROVIDER_LABELS[p], "enabled": is_configured(settings, p)}
        for p in PROVIDERS
    ]


# ---------------------------------------------------------------- 2단계: callback
def callback(
    db: Session,
    provider: str,
    *,
    code: str,
    state: str,
    client: httpx.Client | None = None,
) -> dict:
    _assert_supported(provider)
    redirect_uri = verify_state(state, provider)
    adapter = _adapter(provider, client)
    try:
        profile = adapter.login(code, redirect_uri, state)
    except SocialAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc

    link = (
        db.query(SocialAccount)
        .filter(
            SocialAccount.provider == provider,
            SocialAccount.provider_user_id == profile.provider_user_id,
        )
        .first()
    )
    if link is not None:
        if link.user_id:
            # 콘솔 계정 연결 — 본인이 로그인한 상태에서 직접 연결한 행만 여기 온다.
            # (이메일 일치로는 절대 만들어지지 않는다 — connect 경로에서만 생성)
            user = db.get(User, link.user_id)
            if user is None or user.status == "disabled":
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, detail="이용할 수 없는 계정이에요. 고객센터로 문의해 주세요."
                )
            return _console_login_result(db, user, link, profile)
        student = db.get(StudentProfile, link.student_id)
        if student is None or student.status == "disabled":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="이용할 수 없는 계정이에요. 고객센터로 문의해 주세요."
            )
        return _login_result(db, student, link, profile, linked_now=False)

    # 아직 연결이 없다 — 검증된 이메일이면 같은 이메일의 기존 학생에 붙인다.
    if profile.email:
        existing = (
            db.query(StudentProfile)
            .filter(
                func.lower(StudentProfile.student_login_id) == profile.email,
                StudentProfile.status != "disabled",
            )
            .first()
        )
        console_user = (
            db.query(User.id).filter(func.lower(User.email) == profile.email).first() is not None
        )
        if console_user:
            # ★콘솔 계정은 이메일이 같아도 자동으로 붙이지 않는다. 고권한 계정을 외부 IdP에
            # 여는 결정은 본인이 인증된 상태에서 명시적으로 해야 한다 — 그렇게 만들어진
            # 연결(link.user_id)이 있으면 위에서 이미 로그인 처리됐다. 여기 온다는 것은
            # 아직 연결한 적이 없다는 뜻이므로, 연결 방법을 안내한다.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=(
                    "이 이메일은 콘솔 계정으로 사용 중이에요. 이메일로 로그인한 뒤 "
                    "설정에서 소셜 계정을 연결하면 다음부터 소셜 로그인을 쓸 수 있어요."
                ),
            )
        if existing is not None and not profile.email_verified:
            # provider가 이메일 소유를 확인해 주지 않았다 — 자동 연결하면 남의 계정을
            # 가로챌 수 있다. 본인 확인이 된 경로(비밀번호 로그인)로 들어와 연결하게 한다.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=(
                    f"이미 가입된 이메일이에요. 기존 방식으로 로그인한 뒤 계정 설정에서 "
                    f"{PROVIDER_LABELS[provider]} 계정을 연결해 주세요."
                ),
            )
        if existing is not None:
            new_link = _create_link(db, profile, student_id=existing.id)
            return _login_result(db, existing, new_link, profile, linked_now=True)

    return {
        "status": "signup_required",
        "provider": provider,
        "signup_token": _sign_signup_token(profile),
        "profile": {
            "email": profile.email,
            "nickname": profile.nickname,
            "birth_date": profile.birth_date.isoformat() if profile.birth_date else None,
            # true면 프론트가 생년월일 입력을 받아야 한다(연령 게이트 판정에 필수)
            "needs_birth_date": profile.birth_date is None,
        },
    }


def _create_link(
    db: Session,
    profile: SocialProfile,
    *,
    student_id: str | None = None,
    user_id: str | None = None,
) -> SocialAccount:
    """연결 행 생성 — student_id 와 user_id 중 정확히 하나만 채운다(모델 주석의 불변식)."""
    if bool(student_id) == bool(user_id):
        raise ValueError("student_id 와 user_id 중 정확히 하나만 지정해야 한다")
    link = SocialAccount(
        student_id=student_id,
        user_id=user_id,
        provider=profile.provider,
        provider_user_id=profile.provider_user_id,
        email=profile.email,
        email_verified=profile.email_verified,
    )
    db.add(link)
    try:
        db.flush()
    except IntegrityError:
        # 동시 콜백(더블클릭·중복 탭) — 상대가 만든 연결을 그대로 쓴다.
        db.rollback()
        existing = (
            db.query(SocialAccount)
            .filter(
                SocialAccount.provider == profile.provider,
                SocialAccount.provider_user_id == profile.provider_user_id,
            )
            .first()
        )
        if existing is None:
            raise
        return existing
    return link


def _login_result(
    db: Session,
    student: StudentProfile,
    link: SocialAccount,
    profile: SocialProfile,
    *,
    linked_now: bool,
) -> dict:
    now = _now()
    link.last_login_at = now
    # provider 쪽에서 이메일을 바꿨거나 뒤늦게 동의했으면 사본을 갱신한다(로그인 판정과 무관).
    if profile.email and link.email != profile.email:
        link.email = profile.email
        link.email_verified = profile.email_verified
    student.last_login_at = now
    db.commit()
    tokens = auth_service.issue_tokens(db, student.id, "student", "student")
    return {
        "status": "logged_in",
        "provider": profile.provider,
        "tokens": tokens,
        "linked_now": linked_now,
        "student": {
            "id": student.id,
            "nickname": student.nickname,
            "student_code": student.student_code,
        },
    }


def _console_login_result(
    db: Session, user: User, link: SocialAccount, profile: SocialProfile
) -> dict:
    """콘솔 계정(운영자·강사 등) 소셜 로그인 — 연결된 계정만 여기 도달한다.

    학생과 달리 **가입 경로가 없다**: 연결된 계정이 없으면 callback 이 400 으로 안내하고
    끝난다. 콘솔 계정은 소셜로 새로 만들어지지 않는다(권한을 자동으로 부여하지 않는다).
    토큰의 role 은 계정의 실제 역할이라, 로그인하면 각자 콘솔로 들어간다."""
    now = _now()
    link.last_login_at = now
    if profile.email and link.email != profile.email:
        link.email = profile.email
        link.email_verified = profile.email_verified
    db.commit()
    return {
        "status": "logged_in",
        "provider": profile.provider,
        "tokens": auth_service.issue_tokens(db, user.id, user.role, "user"),
        "linked_now": False,
        "student": None,
    }


# ---------------------------------------------------------------- 3단계: signup
def signup(db: Session, req: s.SocialSignupRequest) -> dict:
    """소셜 신규 가입 — 생년월일을 확인한 뒤에야 계정을 만든다."""
    payload = _decode(
        req.signup_token, "social_signup", "가입 시간이 만료됐어요. 다시 로그인해 주세요."
    )
    provider = str(payload.get("provider") or "")
    uid = str(payload.get("uid") or "")
    if provider not in PROVIDERS or not uid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="소셜 로그인 정보가 올바르지 않아요.")

    # 토큰에 담긴 생년월일(provider 제공)이 있으면 그것을 쓰고, 없으면 사용자가 입력한 값.
    token_birth = payload.get("birth")
    birth = date.fromisoformat(token_birth) if token_birth else req.birth_date
    if birth is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="생년월일을 입력해 주세요.")

    today = datetime.now().date()
    if birth > today or birth.year < 1900:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="생년월일이 올바르지 않습니다.")
    if auth_service._age_on(today, birth) < auth_service.GUARDIAN_CONSENT_AGE:
        # 보호자 동의(이메일 코드) 절차가 소셜 흐름에는 없다 — 게이트를 우회시키느니
        # 이메일 가입으로 정직하게 돌려보낸다.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "만 14세 미만은 보호자(법정대리인) 동의가 필요해요. "
                "이메일 가입으로 보호자 동의를 받아 진행해 주세요."
            ),
        )

    # 동시 요청으로 이미 만들어졌으면 그 계정으로 로그인시킨다(중복 계정 방지).
    link = (
        db.query(SocialAccount)
        .filter(SocialAccount.provider == provider, SocialAccount.provider_user_id == uid)
        .first()
    )
    if link is not None:
        student = db.get(StudentProfile, link.student_id)
        if student is not None and student.status != "disabled":
            student.last_login_at = _now()
            db.commit()
            return {
                "status": "logged_in",
                "provider": provider,
                "tokens": auth_service.issue_tokens(db, student.id, "student", "student"),
                "linked_now": False,
                "student": {
                    "id": student.id,
                    "nickname": student.nickname,
                    "student_code": student.student_code,
                },
            }

    email = (payload.get("email") or None) and str(payload["email"]).strip().lower()
    # 로그인 아이디: 이메일이 있으면 이메일(기존 가입과 동일 규약), 없으면 provider 식별자로
    # 합성한다 — 카카오는 이메일 제공이 선택이라 없는 사용자가 실제로 있다.
    login_id = auth_service.normalize_login_id(email or f"{provider}_{uid}")
    if auth_service.login_identifier_taken(db, login_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="이미 가입된 이메일이에요. 기존 방식으로 로그인한 뒤 계정 설정에서 연결해 주세요.",
        )

    nickname = (req.nickname or payload.get("nickname") or "").strip()[:50] or "학습자"
    student = StudentProfile(
        organization_id=None,  # 소셜 가입은 항상 무소속(개인 학습자)
        student_login_id=login_id,
        student_code=auth_service._generate_student_code(db),
        password_hash=UNUSABLE_PASSWORD,  # 비밀번호 로그인 불가(소셜 전용 계정)
        nickname=nickname,
        birth_date=birth,
        coins=0,
        level=1,
        last_login_at=_now(),
    )
    db.add(student)
    try:
        db.flush()
        db.add(
            SocialAccount(
                student_id=student.id,
                provider=provider,
                provider_user_id=uid,
                email=email,
                email_verified=bool(payload.get("email_verified")),
                last_login_at=_now(),
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="이미 가입된 계정이에요. 다시 로그인해 주세요."
        ) from None

    return {
        "status": "logged_in",
        "provider": provider,
        "tokens": auth_service.issue_tokens(db, student.id, "student", "student"),
        "linked_now": True,
        "is_new_account": True,
        "student": {
            "id": student.id,
            "nickname": student.nickname,
            "student_code": student.student_code,
        },
    }


# ---------------------------------------------------------------- 연결 관리(로그인 후)
def _subject_filter(principal):
    """이 주체의 연결 행을 고르는 조건 — 학생이면 student_id, 콘솔 계정이면 user_id."""
    if principal.kind == "student":
        return SocialAccount.student_id == principal.id
    return SocialAccount.user_id == principal.id


def _subject_has_password(principal) -> bool:
    """비밀번호 로그인이 가능한 주체인가 — 마지막 연결 해제를 허용할지의 판단 근거.

    콘솔 계정은 항상 비밀번호로 들어올 수 있다(소셜 전용 콘솔 계정은 만들어지지 않는다).
    소셜 전용은 학생만 존재한다."""
    if principal.kind == "student":
        return has_password(principal.student)
    return True


def connections(db: Session, principal) -> dict:
    rows = (
        db.query(SocialAccount)
        .filter(_subject_filter(principal))
        .order_by(SocialAccount.created_at)
        .all()
    )
    return {
        "has_password": _subject_has_password(principal),
        "connections": [
            {
                "provider": r.provider,
                "label": PROVIDER_LABELS.get(r.provider, r.provider),
                "email": r.email,
                "connected_at": r.created_at.isoformat() if r.created_at else None,
                "last_login_at": r.last_login_at.isoformat() if r.last_login_at else None,
            }
            for r in rows
        ],
        "available": available_providers(),
    }


def connect(
    db: Session,
    principal,
    provider: str,
    *,
    code: str,
    state: str,
    client: httpx.Client | None = None,
) -> dict:
    """로그인한 상태에서 소셜 계정을 추가 연결한다(계정 설정 화면).

    학생·콘솔 계정 모두 이 경로를 쓴다. 콜백과 달리 '누구의 계정인가'가 이미 정해져 있으므로
    이메일 검증 여부를 따지지 않는다 — 본인이 로그인한 채로 본인의 소셜 계정에 동의한 것이기
    때문이다. ★콘솔 계정이 소셜 로그인을 쓸 수 있는 **유일한** 통로가 여기다(자동 연결 없음)."""
    _assert_supported(provider)
    redirect_uri = verify_state(state, provider)
    adapter = _adapter(provider, client)
    try:
        profile = adapter.login(code, redirect_uri, state)
    except SocialAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc

    existing = (
        db.query(SocialAccount)
        .filter(
            SocialAccount.provider == provider,
            SocialAccount.provider_user_id == profile.provider_user_id,
        )
        .first()
    )
    mine = existing is not None and (
        existing.student_id == principal.id or existing.user_id == principal.id
    )
    if existing is not None and not mine:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"이 {PROVIDER_LABELS[provider]} 계정은 다른 계정에 연결돼 있어요.",
        )
    if existing is None:
        if db.query(SocialAccount).filter(
            _subject_filter(principal), SocialAccount.provider == provider
        ).first() is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"이미 {PROVIDER_LABELS[provider]} 계정이 연결돼 있어요.",
            )
        if principal.kind == "student":
            _create_link(db, profile, student_id=principal.id)
        else:
            _create_link(db, profile, user_id=principal.id)
        db.commit()
    return connections(db, principal)


def disconnect(db: Session, principal, provider: str) -> dict:
    link = (
        db.query(SocialAccount)
        .filter(_subject_filter(principal), SocialAccount.provider == provider)
        .first()
    )
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="연결된 계정이 없어요.")
    others = (
        db.query(SocialAccount)
        .filter(_subject_filter(principal), SocialAccount.id != link.id)
        .count()
    )
    if others == 0 and not _subject_has_password(principal):
        # 마지막 로그인 수단을 끊으면 계정에 다시 못 들어온다 — 비밀번호부터 만들게 한다.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="마지막 로그인 수단이에요. 비밀번호를 먼저 설정한 뒤 연결을 해제해 주세요.",
        )
    db.delete(link)
    db.commit()
    return connections(db, principal)
