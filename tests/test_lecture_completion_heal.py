"""완주 자가치유 — 영상 밖(≥duration) 낡은 체크포인트 예약이 하트비트에서 풀려
status=done이 되는지. (버그: duration 축소로 핀이 영상 밖에 남으면 게이트가 영영 안 떠
완주 불가 → 문제은행 영구 잠금. next_checkpoint_sec is None일 때만 재예약하던 탓에
자가치유가 안 됐다.)"""
from datetime import timedelta

from app.models import Lecture, LectureWatchProgress
from tests.test_captcha_api import _instructor
from tests.test_lectures import (
    _add_question,
    _hb,
    _progress_row,
    _session_token,
    _student_token,
    _upload_lecture,
    auth,
    media_dir,  # noqa: F401 (pytest 픽스처 — 이름으로 사용)
)


def test_out_of_range_checkpoint_self_heals_to_done(client, db, seed_org, media_dir):
    ops_tok = _instructor(client, db)
    lec = _upload_lecture(client, ops_tok, duration=600).json()
    _add_question(client, ops_tok, lec["id"], position=50)  # 영상 안 유효 핀
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))  # cp=50 예약
    st = _session_token(client, tok, lec["id"])

    # duration을 40으로 축소 → 핀 50이 '영상 밖'이 된다(운영자 길이 수정 재현).
    # 예약은 여전히 50에 묶여 있고, 학생은 새 길이 끝(40)까지 봤다.
    lec_obj = db.get(Lecture, lec["id"])
    lec_obj.duration_sec = 40
    row = _progress_row(db, lec["id"])
    assert row.next_checkpoint_sec == 50  # 축소 전 예약이 도달 불가 핀에 묶임
    row.watched_max_sec = 40
    row.updated_at = row.updated_at - timedelta(seconds=120)  # allowed 여유
    db.commit()

    # 하트비트 한 번 — 자가치유로 예약이 풀리고 완주(done) 처리돼야 한다
    r = _hb(client, tok, lec["id"], 40, st=st)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["next_checkpoint_sec"] is None, body
    assert body["status"] == "done", body

    # DB에도 done이 남아 문제은행 완주 잠금이 풀린다
    healed = db.get(LectureWatchProgress, row.id)
    assert healed.status == "done"


def test_in_range_checkpoint_not_falsely_healed(client, db, seed_org, media_dir):
    """유효한 '영상 안' 미통과 예약은 자가치유 대상이 아니다 — 정상 잠금이 유지돼야(오힐 방지)."""
    ops_tok = _instructor(client, db)
    lec = _upload_lecture(client, ops_tok, duration=600).json()
    _add_question(client, ops_tok, lec["id"], position=50)
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))  # cp=50
    st = _session_token(client, tok, lec["id"])

    row = _progress_row(db, lec["id"])
    assert row.next_checkpoint_sec == 50  # 영상 안 유효 핀(50 < 600)
    row.watched_max_sec = 30  # 아직 핀 앞
    row.updated_at = row.updated_at - timedelta(seconds=10)
    db.commit()

    r = _hb(client, tok, lec["id"], 30, st=st)
    body = r.json()
    assert body["next_checkpoint_sec"] == 50  # 건드리지 않음
    assert body["status"] != "done"
