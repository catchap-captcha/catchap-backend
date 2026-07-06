from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
    # REPEATABLE READ(기본)는 풀 커넥션의 오래된 스냅샷이 조회에 남아
    # 외부 변경(다른 세션의 커밋)이 늦게 보이는 문제가 있어 READ COMMITTED 사용
    isolation_level="READ COMMITTED",
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
