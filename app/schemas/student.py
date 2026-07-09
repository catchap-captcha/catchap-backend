from pydantic import BaseModel, Field


class PurchaseRequest(BaseModel):
    item_id: str


class AvatarRequest(BaseModel):
    avatar: dict


class StudentProfileUpdate(BaseModel):
    nickname: str | None = Field(default=None, max_length=50)
    age: int | None = Field(default=None, ge=3, le=13)
    # 성별은 학생 본인이 바꾸지 않는다 — 선생님(명단/코드 생성)이 관리


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
    replay: bool = False  # 전날 복습/다시풀기 — 오늘 완료 처리·코인 지급 없음
    # 오늘의퀴즈(습관) 상태 갱신 여부. 전체학습 주간 챕터 플레이는 daily=False로 두어
    # 코인·정답률(숙련도)은 반영하되 오늘의퀴즈 done/연속도전은 건드리지 않는다(학습·습관 분리).
    daily: bool = True
    # 행동 데이터 (아동용 캡차 학습 재료): {solve_time_ms, retry_count, trace:[[t,x,y],...], box:{w,h}}
    # 궤적이 있으면 서버가 지표를 직접 계산한다 (captcha_service.record_behavior_event)
    behavior: dict | None = None


class ConceptReadRequest(BaseModel):
    concept_id: str  # chapter UUID 또는 '국어-1' 형태 디자인 키
