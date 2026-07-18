"""강의 문항 → 전체학습 은행 배치 — 형식 변환·가드·런타임 반영.

왜 이 테스트가 까다로운가: 은행 레지스트리(subject_banks.BANKS 등)는 모듈 전역이라
refresh가 다른 테스트의 은행(파일 폴백)을 오염시킨다 — 각 테스트에서 스냅샷/복원한다.
설계 배경: docs/lecture-question-pipeline.md
"""

import contextlib

from app.models import Question
from app.services import subject_banks

from tests.test_captcha_api import _ops, auth
from tests.test_lectures import _upload_lecture, media_dir  # noqa: F401 (fixture 재사용)


@contextlib.contextmanager
def _bank_state_guard():
    """BANKS/_BY_ID/_PLAYABLE 전역을 스냅샷하고 테스트 후 복원 — 테스트 간 오염 방지."""
    snap = (
        dict(subject_banks.BANKS),
        dict(subject_banks._BY_ID),
        dict(subject_banks._PLAYABLE),
    )
    try:
        yield
    finally:
        subject_banks.BANKS.clear()
        subject_banks.BANKS.update(snap[0])
        subject_banks._BY_ID.clear()
        subject_banks._BY_ID.update(snap[1])
        subject_banks._PLAYABLE.clear()
        subject_banks._PLAYABLE.update(snap[2])


def _make_question(client, tok, lec_id, **over):
    body = {
        "position_sec": 0,
        "status": "draft",  # 시점 미배치 초안 — LLM 생성 문항과 같은 상태에서 배치 시나리오 재현
        "prompt": "강의에서 배운 별의 색은?",
        "options": ["파랑", "빨강", "노랑"],
        "answer_index": 1,
        "explain": "강의에서 빨강이라고 했다.",
        **over,
    }
    r = client.post(f"/api/v1/ops/lectures/{lec_id}/questions", json=body, headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()


def test_to_bank_converts_and_hot_reloads(client, db, media_dir):
    """행복 경로: 형식 변환(옵션 객체화·answer=옵션id·말미 order_no) + 런타임 즉시 반영 + 중복 409."""
    with _bank_state_guard():
        tok = _ops(client, db)
        lec = _upload_lecture(client, tok, title="별의 일생", subject="과학").json()
        q = _make_question(client, tok, lec["id"])

        # DB 은행이 비어 있으면(파일 폴백) 정직한 409 — 1행 삽입이 은행 전체를 삼키는 함정 방지
        r = client.post(
            f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}/to-bank", headers=auth(tok)
        )
        assert r.status_code == 409 and "적재" in r.json()["detail"]

        # 은행 시드 존재 상황 재현(기존 문항 1행) → 배치 성공
        db.add(Question(id="sci-seed-1", subject="과학", type="single", order_no=7,
                        playable=True, payload={"id": "sci-seed-1", "type": "single",
                                                "topic": "seed", "stage": 1, "prompt": "p",
                                                "hint": "", "options": [{"id": "o1", "text": "x"}],
                                                "answer": "o1", "explain": "", "playable": True}))
        db.commit()
        r = client.post(
            f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}/to-bank", headers=auth(tok)
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["runtime_visible"] is True

        row = db.get(Question, body["bank_id"])
        assert row is not None and row.subject == "과학" and row.type == "single"
        assert row.order_no == 8  # 과목 말미(기존 max 7 + 1) — 주간 챕터를 안 흔든다
        p = row.payload
        assert p["options"] == [
            {"id": "o1", "text": "파랑"}, {"id": "o2", "text": "빨강"}, {"id": "o3", "text": "노랑"},
        ]
        assert p["answer"] == "o2"  # answer_index=1 → 옵션 id 기반으로 변환
        assert p["topic"] == "별의 일생"  # 출처(강의 제목)가 화면 topic으로

        # 런타임 은행에서 즉시 조회·채점 가능(재기동 불필요 — refresh_from_db의 존재 이유)
        got = subject_banks.get_question("과학", body["bank_id"])
        assert got is not None and got["answer"] == "o2"
        # 정답 유출 방지 규약 준수 — public_question이 필수 키(topic/stage/hint) 크래시 없이 동작
        pub = subject_banks.public_question(got)
        assert "answer" not in pub and pub["prompt"] == "강의에서 배운 별의 색은?"

        # 원 문항에 배치 표식 + 중복 배치 409
        qs = client.get(f"/api/v1/ops/lectures/{lec['id']}/questions", headers=auth(tok)).json()
        mine = next(x for x in qs if x["id"] == q["id"])
        assert mine["bank_placed"]["bank_id"] == body["bank_id"]
        # draft로 배치했으니 강등 없음
        assert body["demoted_from_active"] is False and mine["status"] == "draft"
        r2 = client.post(
            f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}/to-bank", headers=auth(tok)
        )
        assert r2.status_code == 409


def test_to_bank_demotes_active_out_of_captcha_pool(client, db, media_dir):
    """★활성(캡차 출제 중) 문항을 은행에 보내면 draft로 강등돼 캡차 풀에서 빠진다.

    verdict='bank'(봇도 상식으로 풀림) 문항이 활성 캡차로 남으면 약한 검증이 되는
    구멍(skeptic 0718)을 막는다. 캡차 출제는 active만 대상이므로 draft 강등으로 충분."""
    with _bank_state_guard():
        tok = _ops(client, db)
        lec = _upload_lecture(client, tok, title="암석", subject="과학", duration=600).json()
        db.add(Question(id="sci-seed-3", subject="과학", type="single", order_no=1,
                        playable=True, payload={"id": "sci-seed-3", "type": "single",
                                                "topic": "seed", "stage": 1, "prompt": "p",
                                                "hint": "", "options": [{"id": "o1", "text": "x"}],
                                                "answer": "o1", "explain": "", "playable": True}))
        db.commit()
        # 활성(공개) 문항 — 시점 지정 후 active
        q = _make_question(client, tok, lec["id"], position_sec=30, status="active")
        assert q["status"] == "active"

        r = client.post(
            f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}/to-bank", headers=auth(tok)
        )
        assert r.status_code == 200, r.text
        assert r.json()["demoted_from_active"] is True

        qs = client.get(f"/api/v1/ops/lectures/{lec['id']}/questions", headers=auth(tok)).json()
        mine = next(x for x in qs if x["id"] == q["id"])
        assert mine["status"] == "draft"  # 캡차 풀에서 빠짐 — 목록엔 남아 배지 유지
        assert mine["bank_placed"] is not None


def test_to_bank_rejects_unconvertible(client, db, media_dir):
    """은행 single 형식이 못 담는 문항은 정직한 400 — 다답형·이미지."""
    with _bank_state_guard():
        tok = _ops(client, db)
        lec = _upload_lecture(client, tok, title="지층", subject="과학").json()
        db.add(Question(id="sci-seed-2", subject="과학", type="single", order_no=1,
                        playable=True, payload={"id": "sci-seed-2", "type": "single",
                                                "topic": "seed", "stage": 1, "prompt": "p",
                                                "hint": "", "options": [{"id": "o1", "text": "x"}],
                                                "answer": "o1", "explain": "", "playable": True}))
        db.commit()

        multi = _make_question(client, tok, lec["id"], answer_indexes=[0, 1])
        r = client.post(
            f"/api/v1/ops/lectures/{lec['id']}/questions/{multi['id']}/to-bank", headers=auth(tok)
        )
        assert r.status_code == 400 and "다답형" in r.json()["detail"]

        imgq = _make_question(client, tok, lec["id"])
        files = {"file": ("i.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")}
        up = client.post(
            f"/api/v1/ops/lectures/{lec['id']}/questions/{imgq['id']}/images",
            data={"slot": "prompt"}, files=files, headers=auth(tok),
        )
        assert up.status_code == 200, up.text
        r = client.post(
            f"/api/v1/ops/lectures/{lec['id']}/questions/{imgq['id']}/to-bank", headers=auth(tok)
        )
        assert r.status_code == 400 and "이미지" in r.json()["detail"]


def test_bank_lecture_question_gated_by_completion(client, db, seed_org, media_dir):
    """★문제은행 강의 잠금(3단계) — 강의에서 배치한 은행 문항은 그 강의를 완주해야 출제된다.
    강의 무관(기존) 문항은 항상 열림. 비로그인(외부 임베드)은 강의 문항 제외. 진도(split_pool)
    도 잠긴 문항을 뺀다. 배치 payload의 전체 lecture_id가 잠금 판정의 정본."""
    from app.models import LectureWatchProgress
    from app.services import bank_mode

    with _bank_state_guard():
        tok = _ops(client, db)
        lec = _upload_lecture(client, tok, title="별의 일생", subject="과학").json()
        # 은행 시드(강의 무관) — 파일 폴백 409 회피 + '항상 열림' 대조군
        db.add(Question(id="sci-seed-g", subject="과학", type="single", order_no=1,
                        playable=True, payload={"id": "sci-seed-g", "type": "single",
                                                "topic": "seed", "stage": 1, "prompt": "p",
                                                "hint": "", "options": [{"id": "o1", "text": "x"}],
                                                "answer": "o1", "explain": "", "playable": True}))
        db.commit()
        q = _make_question(client, tok, lec["id"])
        r = client.post(
            f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}/to-bank", headers=auth(tok)
        )
        assert r.status_code == 200, r.text
        bank_id = r.json()["bank_id"]

        # 배치된 은행 문항엔 출처 lecture_id(전체)가 있다 — 잠금 판정 정본
        placed = subject_banks.get_question("과학", bank_id)
        assert placed["lecture_id"] == lec["id"]

        student = seed_org["student"]

        # 완주 전: 강의 문항 잠김 — 그것만 후보로 줘도 None, 은행 전체 출제에도 안 나온다
        assert bank_mode.pick_from(db, student, "과학", [bank_id]) is None
        for _ in range(20):
            assert bank_mode.pick_question(db, student, "과학")["id"] != bank_id
        # 강의 무관 시드는 항상 열림(대조군)
        assert bank_mode.pick_from(db, student, "과학", ["sci-seed-g"])["id"] == "sci-seed-g"
        # 진도에서도 잠긴 문항은 빠진다
        u, w, c = bank_mode.split_pool(db, student, "과학")
        assert bank_id not in u + w + c

        # 완주 기록 → 열린다
        db.add(LectureWatchProgress(
            student_id=student.id, lecture_id=lec["id"], watched_max_sec=600,
            next_checkpoint_sec=None, checkpoints_passed=1, status="done",
        ))
        db.commit()
        assert bank_mode.pick_from(db, student, "과학", [bank_id])["id"] == bank_id
        u2, _, _ = bank_mode.split_pool(db, student, "과학")
        assert bank_id in u2  # 이제 '안 푼 문항'으로 보인다

        # 비로그인(student=None) — 완주 정보 없음 → 강의 문항 제외
        assert bank_mode.pick_from(db, None, "과학", [bank_id]) is None
        assert bank_mode.is_unlocked(placed, None) is False
        assert bank_mode.is_unlocked({"id": "x"}, None) is True  # 강의 무관은 항상 열림
