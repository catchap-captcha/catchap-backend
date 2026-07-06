from pydantic import BaseModel, Field


class LinkRequest(BaseModel):
    student_code: str = Field(min_length=1, max_length=20)


class ChildSettingsUpdate(BaseModel):
    daily_goal: int = Field(default=5, ge=1, le=50)
    time_limit_enabled: bool = False


class ParentProfileUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
