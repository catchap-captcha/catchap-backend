"""강의 체크포인트 보기 셔플(2026-07-22) — 매 출제마다 보기 순서 재배치 + 정답 id 재매핑.

셔플을 '역순'으로 결정적 고정(monkeypatch)해 재배치·재매핑을 검증한다. (test_lectures.py는
정답 위치를 원래 인덱스로 가정하므로 그 모듈에서만 셔플을 끈다 — 여기선 켜서 검증.)
"""
from tests.test_captcha_api import _instructor
from tests.test_lectures import (  # noqa: F401
    _add_question,
    _edu_key,
    _reach_checkpoint,
    _student_token,
    _upload_lecture,
    auth,
    media_dir,
)


def _serve(client, db, seed_org, monkeypatch):
    monkeypatch.setattr("random.shuffle", lambda seq: seq.reverse())  # 역순 고정
    ops_tok = _instructor(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    _add_question(client, ops_tok, lec["id"], answer=2)  # 보기 가/나/다/라, 정답 index=2("다")
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    _reach_checkpoint(client, tok, lec["id"])
    ch = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    ).json()
    return site_key, tok, ch


def test_options_shuffled_and_answer_remapped(client, db, seed_org, media_dir, monkeypatch):
    site_key, tok, ch = _serve(client, db, seed_org, monkeypatch)
    # 역순 → 원래 [가,나,다,라]가 [라,다,나,가]로 재배치, id는 표시 위치(0..3)
    assert [o["text"] for o in ch["options"]] == ["라", "다", "나", "가"], ch["options"]
    # 정답 "다"는 이제 표시 위치 1 — 그 id로 제출하면 통과
    ok = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": ch["challenge_token"], "answer": ["1"]},
        headers={"X-Site-Key": site_key, **auth(tok)},
    ).json()
    assert ok["success"] is True, ok


def test_old_answer_position_now_fails(client, db, seed_org, media_dir, monkeypatch):
    """정답이 옛 위치(원래 index 2)에 고정돼 있지 않음 — 역순 셔플이면 id '2'는 오답."""
    site_key, tok, ch = _serve(client, db, seed_org, monkeypatch)
    bad = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": ch["challenge_token"], "answer": ["2"]},
        headers={"X-Site-Key": site_key, **auth(tok)},
    ).json()
    assert bad["success"] is False, bad
