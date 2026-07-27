from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# 로컬 스모크(SQLite)와 프로덕션(MySQL)을 한 코드로 지원한다.
# SQLite는 'READ COMMITTED' isolation level을 지원하지 않아(유효값: READ UNCOMMITTED/
# SERIALIZABLE/AUTOCOMMIT) 커넥션 체크아웃 시 ArgumentError로 모든 DB 요청이 500난다.
# → SQLite면 기본 격리수준(SERIALIZABLE)을 쓰고 멀티스레드(uvicorn) 접근만 허용하고,
#   MySQL 등에서는 종전대로 READ COMMITTED를 적용한다.
_is_sqlite = settings.DATABASE_URL.startswith("sqlite")
_engine_kwargs: dict = {"pool_pre_ping": True, "pool_recycle": 3600}
if _is_sqlite:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # REPEATABLE READ(기본)는 풀 커넥션의 오래된 스냅샷이 조회에 남아
    # 외부 변경(다른 세션의 커밋)이 늦게 보이는 문제가 있어 READ COMMITTED 사용
    _engine_kwargs["isolation_level"] = "READ COMMITTED"

engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
