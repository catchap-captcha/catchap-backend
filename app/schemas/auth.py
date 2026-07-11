from pydantic import BaseModel, EmailStr, Field, field_validator


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    # role은 하위호환용 필드로 더 이상 인증 판단에 쓰지 않는다(무시됨).
    # 역할은 이메일(유일)로 조회된 계정의 값으로 판별한다 — 클라이언트가 고른 '구분'에 의존하지 않음.
    role: str | None = None
    email: str
    password: str
    # 5회 이상 실패해 캡차가 요구된 뒤, 메인 캡차(forest)를 통과하고 받은 단일사용 토큰.
    captcha_token: str | None = None


class StudentLoginRequest(BaseModel):
    # 미지정 시 아이디로 기관 자동 판별 (여러 기관에 같은 아이디가 있으면 기관 선택 요구)
    organization_id: str | None = None
    student_login_id: str
    password: str
    captcha_token: str | None = None  # 캡차 요구 후 forest 캡차 통과 토큰(단일사용)


class RefreshRequest(BaseModel):
    refresh_token: str


class EmailSendRequest(BaseModel):
    email: EmailStr
    purpose: str = Field(default="signup", pattern="^(signup|reset)$")
    # 계정용 이메일(학부모/교사/기관 가입)이면 발송 전 중복 검사 (학생 가입의 보호자 이메일은 False)
    for_account: bool = False


class CheckStudentIdRequest(BaseModel):
    student_login_id: str = Field(min_length=1, max_length=50)


class EmailVerifyRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)
    purpose: str = Field(default="signup", pattern="^(signup|reset)$")


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
    organization_id: str
    org_code: str
    email: EmailStr
    email_code: str
    student_login_id: str = Field(min_length=3)
    password: str = Field(min_length=4)


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
