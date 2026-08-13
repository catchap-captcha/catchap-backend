"""포인터 움직임 요약을 받아 남기는 경로.

여기서 지키는 것은 둘이다.

  ① 관측이 본래 기능을 막지 않는다 — 값이 이상해도 로그인은 된다
  ② 좌표는 어디에도 남지 않는다 — 표에 그런 칸이 없다

②가 특히 중요하다. 마우스 궤적은 그것만으로 사람이 구분되고(2026-08-12 실측: 같은
사람 4명 전원 식별), 그래서 요약만 받기로 한 것이다. 나중에 누군가 "좌표도 같이
넣자" 고 할 때 이 시험이 먼저 깨져야 한다.
"""

from app.models import MotionSample

MOTION = {"n": 42, "dist": 1.5, "span": 0.8, "turns": 7, "micro": 0.31, "pauses": 3, "gaps": 0.62}


def _login(client, body):
    return client.post("/api/v1/auth/public-login", json=body)


def test_login_records_motion(client, db, seed_org):
    res = _login(client, {"student_login_id": "stu01", "password": "1234", "motion": MOTION})
    assert res.status_code == 200

    row = db.query(MotionSample).filter(MotionSample.surface == "login").one()
    assert (row.n, row.turns, row.pauses) == (42, 7, 3)
    assert round(row.gaps, 2) == 0.62
    # 로그인은 인증 **전**이라 누구인지 모른다. 그 화면은 분포만 본다.
    assert row.subject_id is None


def test_motion_is_optional(client, db, seed_org):
    """옛 프론트가 안 보내도 로그인은 그대로 된다 — 받는 쪽을 먼저 배포해도 안전하다."""
    res = _login(client, {"student_login_id": "stu01", "password": "1234"})
    assert res.status_code == 200
    assert db.query(MotionSample).count() == 0


def test_bad_motion_does_not_break_login(client, db, seed_org):
    """값이 범위를 벗어나면 그 요청만 무시된다. 관측 때문에 로그인이 막히면 안 된다."""
    bad = {**MOTION, "micro": 5.0}  # micro 는 0~1 비율이다
    res = _login(client, {"student_login_id": "stu01", "password": "1234", "motion": bad})
    assert res.status_code == 422, res.text
    # 스키마에서 걸리므로 아무것도 안 남는다.
    assert db.query(MotionSample).count() == 0


def test_no_movement_is_not_recorded(client, db, seed_org):
    """움직임이 없는 구간은 남기지 않는다.

    강의를 집중해서 보는 사람이 대부분 여기 해당한다. 남기면 표의 대부분이 0 으로
    채워져 분포를 볼 때 방해가 되고, '안 움직였다' 는 사실은 판정에 쓰지 않기로 했다.
    """
    empty = {"n": 0, "dist": 0, "span": 0, "turns": 0, "micro": 0, "pauses": 0, "gaps": 0}
    res = _login(client, {"student_login_id": "stu01", "password": "1234", "motion": empty})
    assert res.status_code == 200
    assert db.query(MotionSample).count() == 0


def test_table_has_no_coordinate_columns():
    """좌표를 담을 칸이 아예 없어야 한다. 요약만 받기로 한 결정을 코드로 고정한다."""
    names = {c.name for c in MotionSample.__table__.columns}
    forbidden = {"x", "y", "points", "events", "path", "trace", "coords", "raw"}
    assert not (names & forbidden), names


def test_off_mode_records_nothing(client, db, seed_org, monkeypatch):
    """설정으로 멈출 수 있어야 한다 — 프론트는 계속 보내되 아무것도 안 남는다.

    강의를 보는 내내 관측하는 일이라, 개인정보 처리방침·고지 여부가 정해지기 전에
    멈춰야 할 수도 있다. 그때 코드를 되돌리거나 이미지를 다시 굽는 것은 과하다.
    """
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "MOTION_COLLECT_MODE", "off", raising=False)

    res = _login(client, {"student_login_id": "stu01", "password": "1234", "motion": MOTION})
    # 멈춘 상태에서도 로그인은 그대로 된다.
    assert res.status_code == 200
    assert db.query(MotionSample).count() == 0
