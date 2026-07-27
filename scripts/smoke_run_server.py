"""로컬 스모크 테스트 전용 서버 기동 — SQLite(dev_cat.db)로 uvicorn을 띄운다.
smoke_seed.py와 동일한 몽키패치(isolation_level 제거)를 서버 프로세스에도 적용해야
같은 DB 파일을 실제 API 요청에서도 열 수 있다. 리포 파일(app/db/session.py)은 그대로 둔다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy  # noqa: E402

_orig_create_engine = sqlalchemy.create_engine


def _smoke_create_engine(url, *args, **kwargs):
    if str(url).startswith("sqlite") and kwargs.get("isolation_level") == "READ COMMITTED":
        kwargs.pop("isolation_level")
    return _orig_create_engine(url, *args, **kwargs)


sqlalchemy.create_engine = _smoke_create_engine

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
