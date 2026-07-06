"""DesignOverlay — design_data 상수를 DB(stat_blobs) 값으로 덮어쓰는 프록시.

엔드포인트에서 `from app.services.stats import D` 로 사용하면
`D.TEACHER_DASHBOARD` 등 모든 속성 접근이 DB를 먼저 조회한다.

- DB에 해당 key가 있으면 payload(JSON) 반환 → MySQL에서 값을 바꾸면 화면이 바뀐다
- 없거나 DB 오류(테스트 환경 등)면 design_data 상수로 fallback
- 5초 TTL 캐시로 요청당 과도한 조회 방지
"""

import time
from typing import Any

from app.core.logging_config import get_logger
from app.services import design_data

_TTL_SECONDS = 5.0
_log = get_logger("stats")


class DesignOverlay:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, Any]] = {}

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        now = time.monotonic()
        hit = self._cache.get(name)
        if hit and now - hit[0] < _TTL_SECONDS:
            return hit[1]

        value = self._load(name)
        self._cache[name] = (now, value)
        return value

    def _load(self, name: str) -> Any:
        try:
            from app.db.session import SessionLocal
            from app.models import StatBlob

            with SessionLocal() as db:
                row = (
                    db.query(StatBlob)
                    .filter(StatBlob.key == name, StatBlob.organization_id.is_(None))
                    .first()
                )
                if row is not None:
                    return row.payload
        except Exception as exc:  # noqa: BLE001 — DB 미가용(테스트 등) 시 상수 fallback
            # 조용히 삼키지 않고 debug로 남긴다 (DB 장애를 디자인 상수로 가리는 것 방지)
            _log.debug("stat_blob 조회 실패, design_data fallback: key=%s err=%s", name, exc)
        return getattr(design_data, name)


D = DesignOverlay()


def seed_stat_blobs(db) -> int:
    """design_data의 모든 대문자 상수를 stat_blobs에 저장 (없는 key만 추가, 멱등)."""
    from app.models import StatBlob

    existing = {row.key for row in db.query(StatBlob.key).all()}
    added = 0
    for name in dir(design_data):
        if not name.isupper() or name.startswith("_"):
            continue
        if name in existing:
            continue
        value = getattr(design_data, name)
        if not isinstance(value, (dict, list, str, int, float, bool)):
            continue
        db.add(StatBlob(organization_id=None, key=name, payload=value))
        added += 1
    db.commit()
    return added
