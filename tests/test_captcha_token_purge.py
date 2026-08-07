"""만료된 캡차 토큰 소비 기록 청소 — 지워야 할 것만 지우는지 확인한다.

이 표는 "이 토큰은 이미 썼다"는 1회용 기록이다. 안 지우면 무한히 쌓인다
(2026-08-03 프로덕션 실측 29,001행·14MB 중 28,998행이 이미 만료).

★여기서 검사하는 것은 "함수가 돌았다"가 아니라 **어떤 행이 남고 어떤 행이 사라졌나** 이다.
"""

from datetime import datetime, timedelta

from app.models import CaptchaConsumedToken
from app.services import captcha_service as cs
from tests.conftest import TestSession


def _add(db, token_id: str, expires_at):
    db.add(CaptchaConsumedToken(kind="challenge", token_id=token_id, expires_at=expires_at))
    db.commit()


def _ids(db) -> set[str]:
    return {r[0] for r in db.query(CaptchaConsumedToken.token_id).all()}


def test_purge_removes_expired_keeps_valid(db):
    now = datetime.now()
    _add(db, "old", now - timedelta(hours=3))  # 한참 전에 만료 → 지워야 함
    _add(db, "just-expired", now - timedelta(seconds=90))  # 유예(60초) 밖 → 지워야 함
    _add(db, "in-grace", now - timedelta(seconds=10))  # 유예 안 → 남겨야 함
    _add(db, "valid", now + timedelta(minutes=5))  # 아직 유효 → 남겨야 함
    _add(db, "no-exp", None)  # 만료값 없음 → 판단 불가라 남겨야 함

    deleted = cs.purge_expired_consumed_tokens(now=now, session_factory=TestSession)

    assert deleted == 2, f"2개만 지워야 하는데 {deleted}개 지움"
    assert _ids(db) == {"in-grace", "valid", "no-exp"}


def test_purge_respects_grace_period(db):
    """유예가 없으면 아직 유효한 토큰의 기록을 지워 리플레이가 뚫린다.

    expires_at은 발급한 워커의 시계로 찍히고 청소는 다른 워커가 돈다.
    시계가 조금 어긋나도 안전하도록 60초를 남긴다.
    """
    now = datetime.now()
    _add(db, "edge", now - timedelta(seconds=cs._PURGE_GRACE_SECONDS - 5))

    assert cs.purge_expired_consumed_tokens(now=now, session_factory=TestSession) == 0
    assert _ids(db) == {"edge"}


def test_purge_is_batched(db):
    """한 번에 _PURGE_BATCH 개까지만 지운다 — 긴 락으로 사용자 요청을 막지 않기 위해."""
    now = datetime.now()
    for i in range(cs._PURGE_BATCH + 20):
        db.add(
            CaptchaConsumedToken(
                kind="challenge", token_id=f"t{i}", expires_at=now - timedelta(hours=1)
            )
        )
    db.commit()

    assert cs.purge_expired_consumed_tokens(now=now, session_factory=TestSession) == cs._PURGE_BATCH
    assert db.query(CaptchaConsumedToken).count() == 20

    # 다음 번에 나머지를 마저 지운다
    assert cs.purge_expired_consumed_tokens(now=now, session_factory=TestSession) == 20
    assert db.query(CaptchaConsumedToken).count() == 0


def test_purge_on_empty_table_is_noop(db):
    assert cs.purge_expired_consumed_tokens(session_factory=TestSession) == 0


def test_consume_still_blocks_replay_with_purge_running(db, monkeypatch):
    """★청소를 붙였다고 리플레이 차단이 깨지면 안 된다.

    청소가 **매번 실제로 돌게** 해 놓고(확률 1.0) 같은 토큰을 두 번 소비해 본다.
    청소 함수를 흉내로 바꾸면 아무것도 검사하지 못하므로, 진짜 함수에
    테스트 DB만 물려서 돌린다.
    """
    calls: list[int] = []
    real = cs.purge_expired_consumed_tokens

    def purge_on_test_db(now=None, session_factory=None):
        n = real(now=now, session_factory=TestSession)
        calls.append(n)
        return n

    monkeypatch.setattr(cs, "_PURGE_PROBABILITY", 1.0)
    monkeypatch.setattr(cs, "purge_expired_consumed_tokens", purge_on_test_db)

    # 지워질 만한 낡은 행을 하나 깔아 둔다 — 청소가 실제로 일을 하도록
    _add(db, "stale", datetime.now() - timedelta(hours=1))
    exp = (datetime.now() + timedelta(minutes=3)).timestamp()

    assert cs._consume(db, "challenge", "abc", exp) is True  # 최초 사용
    assert cs._consume(db, "challenge", "abc", exp) is False  # 재사용 → 차단

    assert len(calls) == 2, "청소가 두 번 다 돌았어야 한다"
    assert calls[0] == 1, "첫 소비 때 낡은 행 1개를 실제로 지웠어야 한다"
    assert _ids(db) == {"abc"}, "낡은 행은 사라지고 방금 쓴 기록만 남아야 한다"
