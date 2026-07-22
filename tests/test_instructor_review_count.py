"""강사 홈 검수 대기 카운트 — 문제은행으로 보낸(payload.bank_placed) draft는 제외해야 한다.

버그: to-bank가 문항을 draft로 강등시키는데, 대시보드가 draft를 전부 검수 대기로 세어
은행에 보낸 문항이 검수 대기로 영영 남았다.
"""
from app.models import LectureQuestion
from tests.test_captcha_api import _instructor
from tests.test_lectures import (  # noqa: F401
    _add_question,
    _upload_lecture,
    auth,
    media_dir,
)


def test_bank_placed_draft_excluded_from_review_count(client, db, media_dir):
    itok = _instructor(client, db)
    lec = _upload_lecture(client, itok).json()
    _add_question(client, itok, lec["id"], position=1, status="draft", prompt="검수 대기 문항")
    _add_question(client, itok, lec["id"], position=2, status="draft", prompt="은행에 보낸 문항")

    # 두 번째 draft를 '문제은행으로 보낸' 상태로 표식(bank_placed) — to-bank의 사후 상태 재현
    rows = db.query(LectureQuestion).filter(LectureQuestion.lecture_id == lec["id"]).all()
    placed = next(r for r in rows if "은행에" in (r.payload or {}).get("prompt", ""))
    placed.payload = {
        **(placed.payload or {}),
        "bank_placed": {"bank_id": "x-1", "at": "2026-07-22T00:00:00"},
    }
    db.commit()

    d = client.get("/api/v1/ops/instructor/dashboard", headers=auth(itok)).json()
    # bank_placed 문항은 검수 대기에서 빠져 1개만 남아야 한다
    assert d["draft_lecture_questions"] == 1, d
    by_lec = {r["lecture_id"]: r["draft_count"] for r in d["draft_by_lecture"]}
    assert by_lec.get(lec["id"]) == 1, d
