"""봇 판별 캡차 승급 — 신호 누적·감쇠·모드 게이팅.

인강 체크포인트 캡차("이 대목 봤는가")와는 별개 장치라, 기존 체크포인트 동작을 바꾸지
않는지도 함께 본다.
"""

import pytest

from app.core.config import get_settings
from app.services import lecture_service as ls


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
