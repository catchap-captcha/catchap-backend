"""운영자 AI 모델 선택(#26) — 실제 LLM 호출에 쓰는 모델의 런타임 설정.

주의: 이 테이블은 기관 콘솔에 표시만 하는 카탈로그(`ModelVersion`, `/ops/ai-models`)와
다르다. `ModelVersion`은 '보여주기'용이고, 여기 `AiModelConfig`는 문항 생성·자기검증이
**실제로 호출하는 모델**을 고르는 운영 설정이다(두 개를 섞으면 안 됨).

2슬롯(사용자 결정 2026-07-19): 생성(generate)·검증(verify) 용도에 각각 모델을 지정한다.
자동 스왑 = 슬롯에 지정된 모델이 Off이거나 호출 실패면 다른 켜진 모델로 자동 대체
(파이프라인이 통째로 죽지 않게). 토큰 사용량·추정 비용은 모델별로 누적한다.

슬롯 배정은 이 테이블이 아니라 system_settings(ai_slot_generate·ai_slot_verify)에 모델
id 포인터로 둔다 — **같은 모델을 두 슬롯에 함께 쓸 수 있게**(예: 생성·검증 둘 다 opus).
슬롯을 이 행의 컬럼으로 두면 한 모델이 한 슬롯만 가질 수 있어 그 흔한 선택을 막는다.
"""

from sqlalchemy import BigInteger, Boolean, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class AiModelConfig(Base, UUIDPk, Timestamps):
    __tablename__ = "ai_model_configs"

    provider: Mapped[str] = mapped_column(String(60))  # 회사 — 예: Anthropic, OpenAI
    model_id: Mapped[str] = mapped_column(String(120))  # 실제 API 모델 문자열(예: claude-opus-4-8)
    name: Mapped[str] = mapped_column(String(100))  # 운영자용 표시 이름
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # On/Off — 꺼지면 슬롯·스왑 대상 제외
    # 추정 비용 단가($/100만 토큰) — 실비용이 아니라 운영자 참고용 추정치(모델사 공시가 입력)
    cost_in_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_out_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # 누적 토큰(응답 usage 합산) — 추정 비용 = tokens/1e6 * 단가
    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_out: Mapped[int] = mapped_column(BigInteger, default=0)
