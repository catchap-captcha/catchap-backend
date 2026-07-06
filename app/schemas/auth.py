from pydantic import BaseModel, EmailStr, Field


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


class StudentLoginRequest(BaseModel):
    # 미지정 시 아이디로 기관 자동 판별 (여러 기관에 같은 아이디가 있으면 기관 선택 요구)
    organization_id: str | None = None
    student_login_id: str
    password: str


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
    email_code: str
    organization_id: str
    teacher_code: str


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
    password: str = Field(min_length=8)
    email_code: str
    expected_students: str | None = None
    plan_interest: str | None = None


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=8)


class OrgCodeVerifyRequest(BaseModel):
    organization_id: str
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
