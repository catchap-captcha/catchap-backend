from pydantic import BaseModel, Field


class AddStudentByCode(BaseModel):
    student_code: str = Field(min_length=1, max_length=20)


class ClassStudentUpdate(BaseModel):
    nickname: str | None = Field(default=None, max_length=50)
    real_name: str | None = Field(default=None, max_length=100)  # 학교용 실명 (교사가 관리)
    age: int | None = Field(default=None, ge=3, le=13)
    gender: str | None = Field(default=None, pattern="^(male|female|other|)$")  # 교사 관리(빈문자=해제)
    status: str | None = Field(default=None, pattern="^(좋음|학습 뜸함|도움 필요|good|inactive|needs_help)$")


class FamilyMessageCreate(BaseModel):
    student_ids: list[str]
    message: str = Field(min_length=1, max_length=200)


class TeacherProfileUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
    position: str | None = Field(default=None, max_length=50)
