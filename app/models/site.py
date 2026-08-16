from datetime import datetime

from sqlalchemy import CHAR, JSON, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class Site(Base, UUIDPk, Timestamps):
    """CAPTCHA API 연동 사이트 (기관 대시보드 'API·사이트 상태' 위젯의 데이터)"""

    __tablename__ = "sites"

    organization_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("organizations.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(150))
    domain: Mapped[str] = mapped_column(String(255))
    allowed_origins: Mapped[dict] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="active")


class ApiKey(Base, UUIDPk, Timestamps):
    """site_key는 공개, secret_key는 발급 시 1회만 노출 — hash만 저장."""

    __tablename__ = "api_keys"

    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    site_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("sites.id"), index=True)
    site_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    secret_key_hash: Mapped[str] = mapped_column(String(64))
    # 제품 구분: 'captcha'(메인 봇차단) | 'edu'(교육형). edu는 subject로 과목 세분화.
    product: Mapped[str] = mapped_column(String(20), default="captcha")
    subject: Mapped[str | None] = mapped_column(String(20), nullable=True)  # edu 전용 과목
    # 1st-party(우리 인앱) 키만 요청별 과목 오버라이드(?subject=) 허용 — 한 키로 6과목 게임화면.
    # 외부 판매 키는 False → 발급 과목에 고정(구매 안 한 과목 접근 차단).
    first_party: Mapped[bool] = mapped_column(default=False)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 발급 메모(예: 우리학교 홈페이지)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|disabled
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SiteQuestion(Base, UUIDPk, Timestamps):
    """고객사가 직접 넣는 캡차 문항 — 그 사이트 키로 오는 캡차에만 섞여 나온다.

    ★왜 '섞어서' 인가 — 자기 문항만 내면 몇 개 안 되는 문제가 돌고 돌아 봇이 외운다.
      기본 문제(우리가 그때그때 만드는 그림·셈)와 섞어야 예측이 어렵다.
      문항이 하나도 없거나 전부 내려간 사이트는 자동으로 기본 문제만 나온다 —
      ★자기 문항 때문에 캡차가 멈추는 일은 없다.

    정답(answer)은 이 표에만 있고 화면으로 내려가지 않는다. 채점은 우리 서버가 한다.
    """

    __tablename__ = "site_questions"

    site_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("sites.id"), index=True)
    # 조회·수정 권한 확인용(사이트를 한 번 더 타지 않게 비정규화) — api_keys 와 같은 방식
    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    prompt: Mapped[str] = mapped_column(String(300))  # 문제 (예: 우리 회사 로고 색은?)
    # 보기 [{"id": "o1", "text": "파랑"}, …] — id 는 answer 와 맞춘다
    options: Mapped[dict] = mapped_column(JSON, default=list)
    answer: Mapped[str] = mapped_column(String(20))  # 정답 보기의 id
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|disabled


class ApiUsageLog(Base, UUIDPk, Timestamps):
    __tablename__ = "api_usage_logs"
    # 기관 API 사용량 기간 집계 가속용 (migration ce50a1b2c3d4)
    __table_args__ = (Index("ix_aul_org_created", "organization_id", "created_at"),)

    organization_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    site_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True, index=True)
    # 키별·과목별 사용량 집계용 (migration b2c3d4e5f6a7). 과거 로그는 NULL.
    api_key_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True, index=True)
    product: Mapped[str | None] = mapped_column(String(20), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(20), nullable=True)
    endpoint: Mapped[str] = mapped_column(String(150))
    method: Mapped[str] = mapped_column(String(10))
    status_code: Mapped[int] = mapped_column(default=200)
    latency_ms: Mapped[int] = mapped_column(default=0)


class CaptchaConsumedToken(Base, UUIDPk, Timestamps):
    """캡차 1회용 토큰 소비 장부 (challenge nonce · verdict jti).

    무상태 서명 토큰의 리플레이 차단은 인메모리로는 멀티워커/재시작에 무효 →
    (kind, token_id) UNIQUE로 원자적 소비(INSERT 충돌 시 이미 사용됨).
    """

    __tablename__ = "captcha_consumed_tokens"
    __table_args__ = (
        UniqueConstraint("kind", "token_id", name="uq_captcha_consumed"),
        # 만료 행 청소(purge_expired_consumed_tokens)가 expires_at 범위로 지운다.
        # 인덱스가 없으면 지울 때마다 풀스캔 + 정렬이라, 이 표가 커질수록 청소가 느려진다.
        Index("ix_captcha_consumed_expires", "expires_at"),
    )

    kind: Mapped[str] = mapped_column(String(20), index=True)  # challenge | verdict
    token_id: Mapped[str] = mapped_column(String(64), index=True)
    # ★만료 뒤에는 쓸모가 없다 — 토큰 자체가 exp 로 먼저 거절되므로 리플레이 차단에도
    #   더는 필요 없다. 안 지우면 계속 쌓인다(2026-08-03 실측 29,001행 · 14MB).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CaptchaSetting(Base, UUIDPk, Timestamps):
    """캡차설정 화면: 종류 on/off + 라운드당 개수 + 순서 셔플"""

    __tablename__ = "captcha_settings"

    organization_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("organizations.id"), unique=True, index=True
    )
    active_types: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # {image_select, word_select, drag, arithmetic}
    round_count: Mapped[int] = mapped_column(default=2)
    shuffle: Mapped[bool] = mapped_column(default=True)
