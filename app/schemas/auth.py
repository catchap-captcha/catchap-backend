from datetime import date

from pydantic import BaseModel, EmailStr, Field, field_validator


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    # 로그인 탭에서 고른 역할 구분. 주면 계정 역할과 일치해야 한다(불일치 403 — 학부모 탭에서
    # 교사 계정이 교사로 로그인되던 혼선 차단). 'org'는 기관 그룹(관리자/학년부장/교사) 전체.
    # 안 주면 하위호환으로 계정 역할 그대로 로그인(구 클라이언트).
    role: str | None = None
    email: str
    password: str
    # 5회 이상 실패해 캡차가 요구된 뒤, 메인 캡차(forest)를 통과하고 받은 단일사용 토큰.
    captcha_token: str | None = None
    # CatChap Guard(성원·민서 캡차)로 전환했을 때만 채워진다. 그 캡차의 토큰은
    # 캡차 서버에 물어봐야 유효해지는데, POST /api/verify-token 이 발급 당시의
    # session_id·purpose 와 대조하므로 토큰만으로는 검증이 실패한다.
    captcha_session_id: str | None = None
    captcha_purpose: str | None = None
    # 공개 로그인 폼(/login)에서 온 요청이면 True — 운영자(ops)는 여기서 인증하지 않고
    # 강사(instructor)만 허용한다(운영자 분리, 2026-07-20). 운영자는 전용 /ops/login에서만
    # 로그인한다(고권한 내부 계정을 공개 로그인 공격면에 노출하지 않기 위함).
    public: bool = False


class StudentLoginRequest(BaseModel):
    # 미지정 시 아이디로 기관 자동 판별 (여러 기관에 같은 아이디가 있으면 기관 선택 요구)
    organization_id: str | None = None
    student_login_id: str
    password: str
    captcha_token: str | None = None  # 캡차 요구 후 forest 캡차 통과 토큰(단일사용)
    # CatChap Guard(성원·민서 캡차)로 전환했을 때만 채워진다. 그 캡차의 토큰은
    # 캡차 서버에 물어봐야 유효해지는데, POST /api/verify-token 이 발급 당시의
    # session_id·purpose 와 대조하므로 토큰만으로는 검증이 실패한다.
    captcha_session_id: str | None = None
    captcha_purpose: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class EmailSendRequest(BaseModel):
    email: EmailStr
    # guardian = 만 14세 미만 가입의 보호자(법정대리인) 동의 코드 — 기존 계정 이메일도 허용
    purpose: str = Field(default="signup", pattern="^(signup|reset|guardian)$")
    # 계정용 이메일(학부모/교사/기관 가입)이면 발송 전 중복 검사 (학생 가입의 보호자 이메일은 False)
    for_account: bool = False


class CheckStudentIdRequest(BaseModel):
    student_login_id: str = Field(min_length=1, max_length=50)


class EmailVerifyRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)
    purpose: str = Field(default="signup", pattern="^(signup|reset|guardian)$")


class RegisterParentRequest(BaseModel):
    name: str
    email: EmailStr
    phone: str | None = None
    password: str = Field(min_length=8)
    email_code: str


class RegisterTeacherRequest(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(min_length=8)
    # 초대 링크로 가입하면 invite_token이 이메일 소유를 이미 증명하므로 코드 생략 가능
    email_code: str = ""
    organization_id: str
    teacher_code: str
    invite_token: str | None = None


class RegisterStudentRequest(BaseModel):
    name: str
    # 이메일 가입 전환(2026-07-16): 기관 경유 가입에서만 사용 — 이메일 가입은 생략.
    # 필드를 지우지 않고 옵셔널로 남겨 기관 경유 가입을 되살릴 수 있게 한다.
    organization_id: str | None = None
    org_code: str | None = None
    email: EmailStr
    email_code: str
    # 미지정 시 이메일(소문자·strip)이 로그인 아이디가 된다.
    student_login_id: str | None = Field(default=None, min_length=3)
    # 학부모(RegisterParentRequest)와 동일 8자 기준으로 통일 (종전 4자)
    password: str = Field(min_length=8)
    # 연령 분기(signup_age_01): 만 14세 미만은 보호자(법정대리인) 이메일 동의 필수.
    # 생년월일은 신규 가입 필수 — 성인/미성년 판정과 이후 데이터 처리 근거.
    birth_date: date
    guardian_email: EmailStr | None = None
    guardian_email_code: str | None = None


class RegisterOrgRequest(BaseModel):
    org_name: str
    org_type: str = "초등학교"
    business_number: str | None = None
    address: str | None = None
    contact_name: str
    contact_email: EmailStr
    contact_phone: str | None = None
    # 기관 등록은 '신청서'다 — 신청 단계에선 비밀번호·이메일 인증을 받지 않는다.
    # 관리자 계정 자격증명은 운영자 승인 시 발급된다(ops.approve_request → 임시 비번 1회 노출).
    # 프론트 신청 폼은 빈 문자열("")을 보내므로 빈 값은 None으로 정규화한다.
    password: str | None = None
    email_code: str | None = None
    expected_students: str | None = None
    plan_interest: str | None = None

    @field_validator("password", "email_code", "business_number", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("password")
    @classmethod
    def _password_min_len(cls, v):
        # 비번을 함께 받는 검증 흐름에서만 최소 길이를 강제(신청서 흐름은 None이라 통과).
        if v is not None and len(v) < 8:
            raise ValueError("비밀번호는 8자 이상이어야 합니다.")
        return v


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=8)


# 학생 비밀번호 재설정 — 학생은 users 테이블에 없어서 위의 재설정 흐름을 탈 수 없었다.
# 학생 식별자는 이메일이 아니라 student_login_id 이므로 별도 스키마를 둔다.
class StudentPasswordResetRequest(BaseModel):
    student_login_id: str = Field(min_length=1, max_length=255)


class StudentPasswordResetConfirm(BaseModel):
    student_login_id: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=8)


class OrgCodeVerifyRequest(BaseModel):
    organization_id: str
    code: str


class JoinCodeVerifyRequest(BaseModel):
    code: str


class MeStudent(BaseModel):
    student_login_id: str
    student_code: str
    nickname: str
    class_id: str | None
    class_name: str | None
    grade_band: str
    avatar: dict
    coins: int
    level: int
    age: int | None = None


class MeResponse(BaseModel):
    id: str
    role: str
    name: str
    email: str | None
    phone: str | None = None
    organization_id: str | None
    organization_name: str | None
    student: MeStudent | None = None
    must_change_password: bool = False  # 학생 비번 초기화 후 True → 강제 변경
    managed_grade: int | None = None  # 학년부장(grade_head)의 담당 학년 (그 외 None)


# --- 소셜 로그인(카카오·네이버·구글) ---
class SocialAuthorizeResponse(BaseModel):
    provider: str
    authorize_url: str
    # 프론트는 이 값을 저장할 필요가 없다(서명 토큰이라 서버가 검증한다). provider가
    # 콜백으로 돌려주는 state를 그대로 다시 보내면 된다.
    state: str


class SocialCallbackRequest(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=1, max_length=4096)


class SocialSignupRequest(BaseModel):
    """소셜 신규 가입 마무리 — 콜백이 준 signup_token + 생년월일.

    birth_date는 provider가 생년월일을 안 준 경우(needs_birth_date=true)에만 필요하다.
    provider가 준 값이 있으면 서버가 토큰 안의 값을 쓰고 이 필드는 무시한다."""

    signup_token: str = Field(min_length=1, max_length=4096)
    birth_date: date | None = None
    nickname: str | None = Field(default=None, max_length=50)


class SocialProfilePreview(BaseModel):
    email: str | None = None
    nickname: str | None = None
    birth_date: str | None = None
    needs_birth_date: bool = False


class SocialStudentBrief(BaseModel):
    id: str
    nickname: str
    student_code: str


class SocialLoginResponse(BaseModel):
    """콜백·가입 공통 응답.

    status=logged_in  → tokens 사용(로그인 완료)
    status=signup_required → signup_token·profile로 가입 화면을 띄운 뒤 /auth/social/signup 호출
    """

    status: str  # logged_in | signup_required
    provider: str
    tokens: TokenPair | None = None
    signup_token: str | None = None
    profile: SocialProfilePreview | None = None
    student: SocialStudentBrief | None = None
    linked_now: bool = False  # 기존 계정에 이번 요청으로 연결됐는가
    is_new_account: bool = False
