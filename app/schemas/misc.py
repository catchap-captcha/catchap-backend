from pydantic import BaseModel, EmailStr, Field


class InquiryCreate(BaseModel):
    inquiry_type: str = Field(max_length=30)
    name: str = Field(min_length=1, max_length=100)
    affiliation: str | None = Field(default=None, max_length=150)
    email: EmailStr
    content: str = Field(min_length=1, max_length=5000)


class StudentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)


class ParentChatRequest(BaseModel):
    child_id: str
    message: str = Field(min_length=1, max_length=500)
