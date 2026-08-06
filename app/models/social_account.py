from datetime import datetime

from sqlalchemy import CHAR, Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class SocialAccount(Base, UUIDPk, Timestamps):
    """소셜 로그인 연결 — 학생 1명에 provider당 1개.

    왜 별도 테이블인가: student_profiles에 kakao_id·naver_id 컬럼을 늘리는 방식은
    provider가 늘 때마다 스키마가 바뀌고, '한 계정에 여러 소셜을 붙인다'는 구조를 표현하지
    못한다. (provider, provider_user_id)를 유일키로 둔 연결 테이블이 표준 형태다.

    provider access token은 저장하지 않는다 — 프로필을 한 번 읽는 용도로만 쓰고 버린다
    (services/social_auth.py 주석 참고). 그래서 이 테이블에 토큰 컬럼이 없다.

    email은 연결 시점의 provider 이메일 사본이다(감사·CS용). 로그인 판정의 정본은
    student_profiles.student_login_id이고, 이 값이 바뀌어도 연결은 provider_user_id로
    유지된다 — 사용자가 카카오에서 이메일을 바꿔도 우리 계정을 잃지 않는다.
    """

    __tablename__ = "social_accounts"
    __table_args__ = (
        # 같은 소셜 계정이 두 학생에 붙지 못하게 DB에서 막는다(계정 탈취 경로 차단).
        UniqueConstraint("provider", "provider_user_id", name="uq_social_provider_user"),
        # 한 학생이 같은 provider를 두 번 연결하지 못하게.
        UniqueConstraint("student_id", "provider", name="uq_social_student_provider"),
    )

    # 소프트 참조(FK 없이 인덱스만) — 신규 테이블 규약(collation 정합 회피).
    student_id: Mapped[str] = mapped_column(CHAR(36), index=True)
    provider: Mapped[str] = mapped_column(String(20))  # kakao|naver|google
    provider_user_id: Mapped[str] = mapped_column(String(191))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 연결 당시 provider가 '이메일 소유가 확인됐다'고 알려 줬는지 — 자동 연결 근거의 기록.
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
