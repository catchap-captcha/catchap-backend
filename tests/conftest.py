"""테스트 픽스처 — SQLite in-memory로 빠르게 검증 (메인 DB는 MySQL).

임시 SQLite는 테스트 전용이며 개발/운영 DB로 사용하지 않는다.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def seed_org(db):
    """기관 + 교사코드 + 학생 1명 기본 세트"""
    from app.core.security import hash_password
    from app.models import ClassRoom, Membership, Organization, StudentProfile, User

    org = Organization(name="테스트초등학교", code="TS-EDU-1000", org_type="초등학교")
    db.add(org)
    db.flush()

    teacher = User(
        email="t1@test.dev",
        password_hash=hash_password("Password123!"),
        name="테스트교사",
        role="teacher",
        organization_id=org.id,
        email_verified_at=__import__("datetime").datetime.utcnow(),
    )
    db.add(teacher)
    db.flush()

    cls = ClassRoom(organization_id=org.id, name="1-1반", grade=1, teacher_id=teacher.id)
    db.add(cls)
    db.flush()

    db.add(
        Membership(
            user_id=teacher.id,
            organization_id=org.id,
            role="teacher",
            status="active",
            teacher_code="T-1111",
        )
    )
    # 미클레임 교사 코드
    db.add(
        Membership(
            user_id=None,
            organization_id=org.id,
            role="teacher",
            status="pending",
            teacher_code="T-2222",
        )
    )

    student = StudentProfile(
        organization_id=org.id,
        class_id=cls.id,
        student_login_id="stu01",
        student_code="CAT-1111",
        password_hash=hash_password("1234"),
        nickname="테스트학생",
        coins=100,
    )
    db.add(student)
    db.commit()
    return {"org": org, "teacher": teacher, "class": cls, "student": student}


def get_email_code(db, email: str) -> str:
    """dry-run 발송된 코드를 DB 해시로 못 꺼내므로, 테스트에서는 서비스 함수를 직접 사용해
    코드 원문을 만들고 해시를 삽입한다."""
    from datetime import datetime, timedelta

    from app.core.security import sha256_hash
    from app.models import EmailVerificationCode

    code = "123456"
    db.add(
        EmailVerificationCode(
            email=email,
            purpose="signup",
            code_hash=sha256_hash(code),
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
    )
    db.commit()
    return code
