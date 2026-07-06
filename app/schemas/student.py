from pydantic import BaseModel, Field


class PurchaseRequest(BaseModel):
    item_id: str


class AvatarRequest(BaseModel):
    avatar: dict


class StudentProfileUpdate(BaseModel):
    nickname: str | None = Field(default=None, max_length=50)
    age: int | None = Field(default=None, ge=3, le=13)


class AttemptCreate(BaseModel):
    subject: str
    chapter_no: int | None = Field(default=None, ge=1, le=1000)
    content_id: str | None = None
    result: str = Field(default="correct", pattern="^(correct|incorrect)$")
    # 클라이언트 자기신고 값 — 랭킹/집계 오염 방지 위해 상한 고정 (서버 채점은 교육 API 단계)
    score: int = Field(default=0, ge=0, le=1000)
    solve_time_ms: int = Field(default=0, ge=0, le=3_600_000)
    retry_count: int = Field(default=0, ge=0, le=100)
    estimated_reason: str | None = Field(default=None, max_length=200)
    completed: bool = False  # true면 오늘의퀴즈 해당 과목 완료 처리


class ConceptReadRequest(BaseModel):
    concept_id: str  # chapter UUID 또는 '국어-1' 형태 디자인 키
