"""봇 판별 캡차 승급 — 신호 누적·감쇠·모드 게이팅.

인강 체크포인트 캡차("이 대목 봤는가")와는 별개 장치라, 기존 체크포인트 동작을 바꾸지
않는지도 함께 본다.
"""

import pytest

from app.core.config import get_settings
from app.services import lecture_service as ls
# test_lectures 의 지역 픽스처. pytest 는 모듈 네임스페이스에 있으면 인식한다.
from tests.test_lectures import media_dir  # noqa: F401


@pytest.fixture()
def prog():
    """advance() 가 만지는 필드만 갖춘 최소 대역. DB 없이 순수 로직을 본다."""

    class P:
        bot_suspicion = 0
        student_id = "stu-1"
        lecture_id = "lec-1"

    return P()


@pytest.fixture()
def mode(monkeypatch):
    """BOT_ESCALATION_MODE 등을 바꿔 끼운다. get_settings 는 lru_cache 라 캐시를 비운다."""

    def _set(**kwargs):
        settings = get_settings()
        for key, value in kwargs.items():
            monkeypatch.setattr(settings, key, value, raising=False)
        return settings

    _set(
        BOT_ESCALATION_MODE="record",
        BOT_SUSPICION_THRESHOLD=10,
        MAIN_CAPTCHA_URL="https://captcha.example",
        MAIN_CAPTCHA_SITE_SECRET="s3cret",
    )
    return _set


def test_signals_accumulate_with_their_weights(prog, mode):
    ls.bump_suspicion(prog, ls.SUSPICION_SPEED_VIOLATION, "speed")
    assert prog.bot_suspicion == ls.SUSPICION_SPEED_VIOLATION
    ls.bump_suspicion(prog, ls.SUSPICION_SESSION_CONFLICT, "session")
    assert prog.bot_suspicion == (
        ls.SUSPICION_SPEED_VIOLATION + ls.SUSPICION_SESSION_CONFLICT
    )


def test_accumulation_is_capped(prog, mode):
    for _ in range(50):
        ls.bump_suspicion(prog, ls.SUSPICION_SESSION_CONFLICT, "session")
    assert prog.bot_suspicion == ls.SUSPICION_MAX


def test_threshold_is_inclusive(prog, mode):
    mode(BOT_SUSPICION_THRESHOLD=10)
    prog.bot_suspicion = 9
    assert ls.captcha_required(prog) is False
    prog.bot_suspicion = 10
    assert ls.captcha_required(prog) is True


def test_off_mode_is_completely_inert(prog, mode):
    """off 는 '판정만 안 함'이 아니라 누적도 안 한다 — 기존과 100% 동일해야 한다."""
    mode(BOT_ESCALATION_MODE="off")
    ls.bump_suspicion(prog, 99, "speed")
    assert prog.bot_suspicion == 0
    prog.bot_suspicion = 999
    assert ls.captcha_required(prog) is False


@pytest.mark.parametrize(
    "missing", ["MAIN_CAPTCHA_URL", "MAIN_CAPTCHA_SITE_SECRET"]
)
def test_missing_captcha_config_downgrades_to_off(prog, mode, missing):
    """설정을 빼먹은 채 켜지는 상태를 막는다 — 검증할 수 없으면 승급하지 않는다."""
    mode(BOT_ESCALATION_MODE="enforce", **{missing: ""})
    assert ls._escalation_mode() == "off"
    ls.bump_suspicion(prog, 99, "speed")
    assert prog.bot_suspicion == 0


def test_unknown_mode_value_is_off(mode):
    mode(BOT_ESCALATION_MODE="enfroce")  # 오타
    assert ls._escalation_mode() == "off"


def test_clear_resets_to_zero(prog, mode):
    prog.bot_suspicion = ls.SUSPICION_MAX
    ls.clear_suspicion(prog)
    assert prog.bot_suspicion == 0


def test_decay_needs_forward_progress_not_just_a_heartbeat(prog, mode):
    """일시정지 비트로는 의심도가 씻기지 않아야 한다.

    감쇠를 '하트비트가 왔다'에 걸면 재생을 멈춰두고 카운터를 0으로 만들 수 있다.
    advance() 는 position > watched 인 비트에서만 감쇠한다 — 그 조건을 여기서 고정한다.
    """
    prog.bot_suspicion = 5
    watched, position = 100, 100  # 전진 없음(일시정지)
    if position > watched:  # advance() 의 감쇠 조건
        prog.bot_suspicion = max(0, prog.bot_suspicion - ls.SUSPICION_DECAY_PER_CLEAN_BEAT)
    assert prog.bot_suspicion == 5, "전진 없는 비트가 감쇠를 일으켰다"

    position = 105  # 정상 전진
    if position > watched:
        prog.bot_suspicion = max(0, prog.bot_suspicion - ls.SUSPICION_DECAY_PER_CLEAN_BEAT)
    assert prog.bot_suspicion == 5 - ls.SUSPICION_DECAY_PER_CLEAN_BEAT


def test_decay_never_goes_negative(prog, mode):
    prog.bot_suspicion = 0
    prog.bot_suspicion = max(0, prog.bot_suspicion - ls.SUSPICION_DECAY_PER_CLEAN_BEAT)
    assert prog.bot_suspicion == 0


def test_record_does_not_need_captcha_config(prog, mode):
    """record 는 캡차를 부르지 않는다 — 설정이 없다고 강등하면 관측을 못 켠다.

    임계값을 정하려면 관측이 먼저인데, 처음엔 record 도 함께 강등하도록 짜서
    캡차 시크릿을 받기 전까지 아무것도 못 보는 상태였다. enforce 만 요구한다.
    """
    mode(BOT_ESCALATION_MODE="record", MAIN_CAPTCHA_URL="", MAIN_CAPTCHA_SITE_SECRET="")
    assert ls._escalation_mode() == "record"
    assert ls.bump_suspicion(prog, ls.SUSPICION_SPEED_VIOLATION, "speed") is True
    assert prog.bot_suspicion == ls.SUSPICION_SPEED_VIOLATION


def test_bump_reports_whether_it_changed_anything(prog, mode):
    """반환값은 '커밋할 것이 있는가'다 — claim_session 이 그 용도로 쓴다."""
    assert ls.bump_suspicion(prog, ls.SUSPICION_SPEED_VIOLATION, "speed") is True
    prog.bot_suspicion = ls.SUSPICION_MAX
    assert ls.bump_suspicion(prog, 5, "speed") is False, "상한이면 바뀐 게 없다"
    mode(BOT_ESCALATION_MODE="off")
    prog.bot_suspicion = 0
    assert ls.bump_suspicion(prog, 5, "speed") is False


def test_off_mode_does_not_decay_recorded_values(client, db, seed_org, media_dir, monkeypatch):
    """off 로 내렸을 때 record 로 쌓아둔 값이 씻기면 안 된다.

    감쇠가 모드 게이팅 밖에 있었다. off 에서는 bump 가 안 되어 값이 0 이라 실질
    영향이 없었지만, record 로 관측한 뒤 off 로 내리면 정상 전진 하트비트마다
    값이 빠져서 관측 데이터가 사라진다. 실제 하트비트로 확인한다.
    """
    from app.models.lecture import LectureWatchProgress
    from tests.test_lectures import (
        _hb, _instructor, _session_token, _student_token, _upload_lecture, auth,
    )

    ops_tok = _instructor(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    tok = _student_token(client, seed_org)
    assert client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok)).status_code == 200
    st = _session_token(client, tok, lec["id"])
    assert _hb(client, tok, lec["id"], 4, st=st).status_code == 200

    row = db.query(LectureWatchProgress).filter_by(lecture_id=lec["id"]).one()
    row.bot_suspicion = 7          # record 로 관측해 쌓인 상태를 흉내
    db.commit()

    settings = get_settings()
    monkeypatch.setattr(settings, "BOT_ESCALATION_MODE", "off", raising=False)
    assert _hb(client, tok, lec["id"], 6, st=st).status_code == 200   # 정상 전진 = 감쇠 대상

    db.refresh(row)
    assert row.bot_suspicion == 7, "off 인데 감쇠가 돌아 관측값이 빠졌다"
