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


class TeacherUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    email: EmailStr | None = None
    class_name: str | None = Field(default=None, max_length=50)
    role: str | None = Field(default=None, pattern="^(담임|교과|보조)$")


class CaptchaSettingsUpdate(BaseModel):
    active_types: dict  # {image_select, word_select, drag, arithmetic}
    round_count: int = Field(default=2, ge=1, le=4)
    shuffle: bool = True
