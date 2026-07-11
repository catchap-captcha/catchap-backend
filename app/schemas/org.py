from pydantic import BaseModel, EmailStr, Field


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    org_type: str | None = Field(default=None, max_length=30)
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=255)
    business_number: str | None = Field(default=None, max_length=30)


class TeacherCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr | None = None
    class_name: str | None = Field(default=None, max_length=50)  # "1-2반"
    role: str = Field(default="담임", pattern="^(담임|교과|보조)$")
    teacher_code: str = Field(min_length=1, max_length=20)  # T-xxxx


class TeacherInviteCreate(BaseModel):
    """교사 초대링크 발송 — 이메일로 초대, 클릭 시 기관·교사코드 프리필."""

    email: EmailStr
    name: str | None = Field(default=None, max_length=100)
    role: str = Field(default="teacher", pattern="^(teacher|grade_head)$")
    # 미리 담당 학급 배정(선택) — 가입(코드 클레임) 시 이 반의 담임으로 자동 배정된다.
    class_name: str | None = Field(default=None, max_length=50)


class TeacherUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    email: EmailStr | None = None
    class_name: str | None = Field(default=None, max_length=50)
    role: str | None = Field(default=None, pattern="^(담임|교과|보조)$")


class AppointGradeHead(BaseModel):
    """교사를 학년부장으로 임명 (담당 학년 지정)."""

    grade: int = Field(ge=1, le=12)


class CaptchaSettingsUpdate(BaseModel):
    active_types: dict  # {image_select, word_select, drag, arithmetic}
    round_count: int = Field(default=2, ge=1, le=4)
    shuffle: bool = True
