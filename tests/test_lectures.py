"""강의 시청 검증 도메인 — 업로드·하트비트 검증·체크포인트 캡차 게이트·문항 CRUD.

핵심 규약 검증:
- 하트비트 시각 비교는 로컬 naive(_now)만 — utcnow 혼입 시 정상 시청이 전부 클램프되는
  변이 테스트로 판별력을 증명한다(픽스처가 앱 쓰기 경로를 우회하지 않는다).
- meta.lec verify는 학습 적립(코인·LearningAttempt) 완전 비생성.
- 업로드 경로만 전역 1MB 본문 제한의 예외 — 다른 POST는 여전히 413.
"""

import io

import pytest

from app.models import (
    AuditLog,
    CoinTransaction,
    LearningAttempt,
    Lecture,
    LectureCheckpointEvent,
    LectureMaterial,
    LectureQuestion,
    LectureWatchProgress,
    Plan,
    Subscription,
)
from tests.test_captcha_api import _ops, auth


def _student_token(client, seed_org):
    return client.post(
        "/api/v1/auth/student-login",
        json={
            "organization_id": seed_org["org"].id,
            "student_login_id": "stu01",
            "password": "1234",
        },
    ).json()["access_token"]


@pytest.fixture()
def media_dir(tmp_path, monkeypatch):
    """영상 저장 위치를 테스트 임시폴더로 — 리포에 파일을 남기지 않는다."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "LECTURE_MEDIA_DIR", str(tmp_path))
    return tmp_path


def _edu_key(client, db, seed_org, ops_tok, *, first_party=True, subject="국어"):
    """1st-party(또는 외부) edu 키 발급 — Pro 구독 필요(발급 게이트)."""
    org = seed_org["org"]
    pro = Plan(key="Pro", name="Pro", monthly_price=290000, api_quota=100000)
    db.add(pro)
    db.flush()
    db.add(Subscription(organization_id=org.id, plan_id=pro.id, status="active"))
    db.commit()
    r = client.post(
        "/api/v1/ops/api-keys",
        json={
            "organization_id": org.id,
            "product": "edu",
            "subject": subject,
            "first_party": first_party,
        },
        headers=auth(ops_tok),
    )
    assert r.status_code == 200, r.text
    return r.json()["site_key"]


def _upload_lecture(
    client, ops_tok, *, title="분수의 덧셈", subject="국어", duration=600,
    size=2 * 1024 * 1024, filename="v.mp4",
    content_type="video/mp4", order_no=None,
):
    data = {
        "title": title,
        "subject": subject,
        "duration_sec": str(duration),
        "description": "테스트 강의",
    }
    if order_no is not None:
        data["order_no"] = str(order_no)
    return client.post(
        "/api/v1/ops/lectures",
        data=data,
        files={"file": (filename, b"\x00" * size, content_type)},
        headers=auth(ops_tok),
    )


def _start_session(client, tok, lecture_id):
    """재생 시작 — 세션 식별자는 서버가 발급한다(클라 생성값은 어디서도 안 받는다).

    응답의 session_token이 하트비트·스트림의 유일한 세션 증명이다."""
    return client.post(f"/api/v1/lectures/{lecture_id}/session", headers=auth(tok))


def _session_token(client, tok, lecture_id):
    r = _start_session(client, tok, lecture_id)
    assert r.status_code == 200, r.text
    return r.json()["session_token"]


def _hb(client, tok, lecture_id, position, *, st, **extra):
    """시청 하트비트 — 세션은 X-Lecture-Session 서명 토큰(서버 발급)으로만 식별."""
    return client.post(
        f"/api/v1/lectures/{lecture_id}/progress",
        json={"position_sec": position, **extra},
        headers={"X-Lecture-Session": st, **auth(tok)},
    )


def _add_question(
    client, ops_tok, lecture_id, *, position=1, answer=2, answer_indexes=None,
    status="active", prompt="강의에서 설명한 내용은?", content_start=None,
):
    """핀 문항 추가 — 기본은 1초 고정(하트비트 헤드룸 5초 안이라 한 비트로 닿는다)."""
    body = {
        "position_sec": position,
        "prompt": prompt,
        "options": ["가", "나", "다", "라"],
        "answer_index": answer,
        "explain": "강의 앞부분에서 설명했어요.",
        "status": status,
    }
    if content_start is not None:
        body["content_start_sec"] = content_start
    if answer_indexes is not None:
        body["answer_indexes"] = answer_indexes
    r = client.post(
        f"/api/v1/ops/lectures/{lecture_id}/questions",
        json=body,
        headers=auth(ops_tok),
    )
    assert r.status_code == 200, r.text
    return r.json()


# ================================================================ 업로드·본문 상한
def test_upload_over_1mb_passes_but_other_posts_still_413(client, db, seed_org, media_dir):
    """업로드 경로(POST /ops/lectures)만 전역 1MB 예외 — 다른 경로는 여전히 413."""
    tok = _ops(client, db)

    # 2MB 업로드 — 미들웨어(경로 예외) 통과 + 파일이 실제로 최종 경로에 존재
    r = _upload_lecture(client, tok, size=2 * 1024 * 1024)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["video_bytes"] == 2 * 1024 * 1024
    assert (media_dir / f"{body['id']}.mp4").is_file()
    assert not list(media_dir.glob(".upload-*")), "임시파일이 남았다"
    # 감사기록
    assert (
        db.query(AuditLog).filter(AuditLog.action == "lecture.create").count() == 1
    )

    # 같은 크기의 일반 POST는 413 유지 (Content-Length 기반 전역 상한)
    big = client.post(
        "/api/v1/auth/login",
        content=b"x" * 1_100_000,
        headers={"Content-Type": "application/json"},
    )
    assert big.status_code == 413

    # 업로드 경로 하위(문항 생성)도 1MB 상한 유지 — 정확 경로 일치만 예외
    sub = client.post(
        f"/api/v1/ops/lectures/{body['id']}/questions",
        content=b"x" * 1_100_000,
        headers={"Content-Type": "application/json", **auth(tok)},
    )
    assert sub.status_code == 413


def test_upload_rejects_bad_ext_and_content_type(client, db, seed_org, media_dir):
    tok = _ops(client, db)
    r1 = _upload_lecture(client, tok, filename="v.exe", size=100)
    assert r1.status_code == 400
    r2 = _upload_lecture(client, tok, content_type="application/octet-stream", size=100)
    assert r2.status_code == 400
    assert list(media_dir.iterdir()) == []  # 거절된 업로드는 파일을 남기지 않는다


def test_copy_upload_recheck_enforces_limit(tmp_path):
    """Content-Length 위조 대비 — 실제 누적 바이트 재검사로 413 + 임시파일 삭제."""
    from fastapi import HTTPException

    from app.api.v1.endpoints.lectures import _copy_upload_to_tmp

    class _Fake:
        file = io.BytesIO(b"\x00" * 3000)

    tmp = tmp_path / ".upload-x.tmp"
    with pytest.raises(HTTPException) as ei:
        _copy_upload_to_tmp(_Fake(), tmp, limit=1000)
    assert ei.value.status_code == 413
    assert not tmp.exists()


# ================================================================ 하트비트 검증
def test_heartbeat_normal_advance_and_speed_clamp(client, db, seed_org, media_dir):
    """정상 하트비트는 전진, 점프(position 위조)는 wall-clock 상한으로 클램프.

    직전 하트비트는 앱 쓰기 경로(POST /progress → _now() 로컬 naive)로 만들어진다 —
    픽스처가 시간 경로를 우회하면 이 테스트의 통과는 신호가 아니다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    _add_question(client, ops_tok, lec["id"], position=60)  # 60초 핀 → 첫 체크포인트 60
    tok = _student_token(client, seed_org)

    # 상세 진입 → 진행 행 생성(첫 체크포인트 = 핀 60초). 순수 조회 — 세션·stream_url 없음
    d = client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    assert d.status_code == 200, d.text
    assert d.json()["next_checkpoint_sec"] == 60
    assert "stream_url" not in d.json(), "상세는 순수 조회 — 스트림 접근은 세션 발급을 거쳐야 한다"

    st = _session_token(client, tok, lec["id"])

    # 정상 하트비트: 경과 ~0초 + 헤드룸(5초) 안의 전진은 그대로 인정
    r1 = _hb(client, tok, lec["id"], 4, st=st)
    assert r1.status_code == 200
    assert r1.json()["watched_max_sec"] == 4

    # 점프 위조: 직후 position=500 신고 → 경과 ~0초라 4 + 헤드룸 근처로 클램프
    r2 = _hb(client, tok, lec["id"], 500, st=st)
    assert r2.status_code == 200
    assert r2.json()["watched_max_sec"] < 30, "점프가 클램프되지 않았다"
    assert r2.json()["watched_max_sec"] >= 4  # watched는 감소하지 않는다


def test_heartbeat_clamps_at_checkpoint_until_captcha(client, db, seed_org, media_dir):
    """체크포인트(cp)+유예(15초)를 넘어서는 진행은 캡차를 풀기 전까지 정지.

    wall-clock을 backdate해 '충분히 오래 본 것처럼' 만든다 — 예전처럼 하트비트 스팸으로
    올리면 안 된다(스팸 전진은 이제 HEADROOM 미지급으로 막혀 있고, 그 회귀는
    test_rewind_not_undone_by_heartbeat_spam이 고정한다)."""
    from datetime import timedelta

    from app.services.lecture_service import GRACE_SEC

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    _add_question(client, ops_tok, lec["id"])  # 1초 핀 — 낼 문항이 있어야 체크포인트가 잡힌다
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))  # cp=1 예약
    st = _session_token(client, tok, lec["id"])

    # 60초 경과 위장 + position=500 위조 신고 — 클램프 없으면 150초까지 전진했을 것
    row = _progress_row(db, lec["id"])
    row.updated_at = row.updated_at - timedelta(seconds=60)
    db.commit()
    r = _hb(client, tok, lec["id"], 500, st=st)
    last = r.json()["watched_max_sec"]
    assert last == 1 + GRACE_SEC, f"체크포인트 클램프 실패: watched={last}"
    assert r.json()["checkpoint_due"] is True


def test_mutation_utcnow_breaks_normal_watch(client, db, seed_org, media_dir, monkeypatch):
    """변이 테스트(판별력 증명) — 시각 비교를 utcnow로 되돌리면 '정상 시청' 전진이 실패한다.

    utcnow(KST-9h)를 쓰면 경과가 -32400초 → 전진 허용량이 음수 → 정상 하트비트(4초)가
    0으로 클램프된다. 위 test_heartbeat_normal_advance가 4를 단언하므로, 이 테스트가
    0을 관측하면 두 테스트가 함께 '_now 로컬 naive 규약'의 판별력을 증명한다.
    monkeypatch는 테스트 종료 시 자동 원복된다."""
    import time as _time
    from datetime import datetime as _dt

    if _time.timezone == 0:
        pytest.skip("로컬 TZ가 UTC라 utcnow 변이가 무해 — 판별 불가 환경")

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    st = _session_token(client, tok, lec["id"])  # 세션 발급은 변이 주입 전(정상 경로)

    from app.services import lecture_service

    monkeypatch.setattr(lecture_service, "_now", _dt.utcnow)  # 규약 위반을 일부러 주입
    r = _hb(client, tok, lec["id"], 4, st=st)
    assert r.status_code == 200
    # 정상 시청인데 0으로 클램프 — utcnow 혼입이 정상 사용자 오탐을 만든다는 실증
    assert r.json()["watched_max_sec"] == 0


# ================================================================ 체크포인트 캡차 게이트
def _reach_checkpoint(client, tok, lecture_id, cp=1):
    client.get(f"/api/v1/lectures/{lecture_id}", headers=auth(tok))
    st = _session_token(client, tok, lecture_id)
    r = _hb(client, tok, lecture_id, cp, st=st)
    assert r.json()["checkpoint_due"] is True, r.text
    return r.json()


def test_lecture_checkpoint_gate_no_learning_side_effects(client, db, seed_org, media_dir):
    """게이트 전체 흐름 + meta.lec verify는 코인·LearningAttempt를 전혀 만들지 않는다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    _add_question(client, ops_tok, lec["id"], answer=2)  # 1초 핀 — 첫 게이트
    # 두 번째 핀(300초) — 첫 핀 통과 후 재예약이 다음 핀으로 잡히는지 본다
    _add_question(client, ops_tok, lec["id"], position=300, answer=1, prompt="뒤 핀")
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    _reach_checkpoint(client, tok, lec["id"])

    # 챌린지 — 그 강의의 문항, 정답 미노출
    ch = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert ch.status_code == 200, ch.text
    body = ch.json()
    assert body["lecture"] == lec["id"]
    assert "answer" not in body and "answer_index" not in body
    assert len(body["options"]) == 4

    # 정답 제출 → 통과 + 다음 체크포인트 재예약, 학습 적립은 없음
    vr = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": body["challenge_token"], "answer": ["2"]},
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert vr.status_code == 200, vr.text
    res = vr.json()
    assert res["success"] is True
    assert "session" not in res  # _credit_student 미호출의 표면 증거
    assert res["lecture"]["checkpoints_passed"] == 1
    assert res["lecture"]["next_checkpoint_sec"] is not None
    assert res["lecture"]["next_checkpoint_sec"] > 1

    # 코인·학습기록 0건 단언 (오늘의퀴즈·오답노트의 원천인 LearningAttempt 포함)
    sid = seed_org["student"].id
    assert db.query(CoinTransaction).filter(CoinTransaction.student_id == sid).count() == 0
    assert db.query(LearningAttempt).filter(LearningAttempt.student_id == sid).count() == 0

    # 체크포인트 이벤트는 기록됨
    ev = db.query(LectureCheckpointEvent).filter(
        LectureCheckpointEvent.lecture_id == lec["id"]
    ).all()
    assert len(ev) == 1 and ev[0].result == "passed"

    # 통과 후 같은 게이트 재요청 — cp가 재예약돼 아직 미도달 → 409
    ch2 = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert ch2.status_code == 409


def test_lecture_checkpoint_wrong_answer_records_failed(client, db, seed_org, media_dir):
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    _add_question(client, ops_tok, lec["id"], answer=2)
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    _reach_checkpoint(client, tok, lec["id"])

    ch = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    ).json()
    vr = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": ch["challenge_token"], "answer": ["0"]},
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert vr.status_code == 200
    res = vr.json()
    assert res["success"] is False
    assert res["lecture"]["checkpoints_passed"] == 0
    assert res["lecture"]["next_checkpoint_sec"] == 1  # 실패 — 게이트 유지
    ev = db.query(LectureCheckpointEvent).all()
    assert len(ev) == 1 and ev[0].result == "failed"
    # 오답이어도 학습기록·코인은 없다
    assert db.query(LearningAttempt).count() == 0
    assert db.query(CoinTransaction).count() == 0


def _gate_challenge(client, site_key, tok, lecture_id):
    r = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lecture_id}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _gate_verify(client, site_key, tok, token, answer):
    r = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": token, "answer": answer},
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_lecture_checkpoint_fail_cap_rewinds_and_blocks_challenge(
    client, db, seed_org, media_dir
):
    """연속 오답 상한 도달 → watched_max 되감기 → 새 챌린지 409(다시 시청 요구).

    되감기가 없으면 오답 → 새 랜덤 문항 무한 반복으로 풀을 대가 없이 브루트포스할 수
    있다. 상한에 닿으면 watched_max가 cp 아래로 내려가 _lecture_challenge가 새 문항
    발급을 거부한다(다시 시청해 cp까지 올라와야 다음 문항)."""
    from app.services.lecture_service import MAX_CHECKPOINT_FAILS, REWIND_SEC

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    _add_question(client, ops_tok, lec["id"], answer=2)
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    _reach_checkpoint(client, tok, lec["id"])

    # 상한 직전까지 오답 — 게이트 유지, fails 누적, 되감기 없음
    for _ in range(MAX_CHECKPOINT_FAILS - 1):
        ch = _gate_challenge(client, site_key, tok, lec["id"])
        res = _gate_verify(client, site_key, tok, ch["challenge_token"], ["0"])
        assert res["success"] is False
        assert res["lecture"]["next_checkpoint_sec"] == 1
    db.expire_all()
    row = _progress_row(db, lec["id"])
    assert int(row.checkpoint_fails) == MAX_CHECKPOINT_FAILS - 1
    assert int(row.watched_max_sec) >= 1, "상한 전에 되감기면 안 된다"

    # 상한 도달 오답 — 되감기 발동
    ch = _gate_challenge(client, site_key, tok, lec["id"])
    res = _gate_verify(client, site_key, tok, ch["challenge_token"], ["0"])
    assert res["success"] is False
    db.expire_all()
    row = _progress_row(db, lec["id"])
    assert int(row.checkpoint_fails) == 0, "되감기 후 카운터 리셋"
    assert int(row.watched_max_sec) == max(0, 1 - REWIND_SEC), "그 대목 앞으로 되감겨야 한다"
    assert int(row.next_checkpoint_sec) == 1, "같은 체크포인트 유지"

    # 되감겼으니 새 챌린지는 거부 — 다시 시청해야 다음 문항을 받는다
    blocked = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert blocked.status_code == 409, blocked.text


def test_rewind_not_undone_by_heartbeat_spam(client, db, seed_org, media_dir):
    """되감기 직후 back-to-back 하트비트 스팸이 실시청 없이 되감기를 무효화하면 안 된다.

    HEADROOM(5초)이 비트마다 무조건 지급되면 elapsed≈0 스팸 N번이 N×5초를 공짜로 얻어
    30초 되감기가 6번 만에 사라진다(skeptic 실증). 시작 구간(watched<HEADROOM) 밖에서는
    지급하지 않으므로, 스팸으로는 wall-clock×SPEED_FACTOR만큼만 전진해야 한다."""
    from datetime import timedelta

    from app.services.lecture_service import (
        MAX_CHECKPOINT_FAILS,
        REWIND_SEC,
    )

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, duration=1000).json()
    _add_question(client, ops_tok, lec["id"], position=200, answer=2)
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    st = _session_token(client, tok, lec["id"])

    # 200초 핀까지 도달 — 충분한 wall-clock이 지난 것으로 앵커를 backdate
    row = _progress_row(db, lec["id"])
    row.updated_at = row.updated_at - timedelta(seconds=120)
    db.commit()
    r = _hb(client, tok, lec["id"], 200, st=st)
    assert r.json()["checkpoint_due"] is True, r.text

    # 3연속 오답 → 되감기
    for _ in range(MAX_CHECKPOINT_FAILS):
        ch = _gate_challenge(client, site_key, tok, lec["id"])
        _gate_verify(client, site_key, tok, ch["challenge_token"], ["0"])
    db.expire_all()
    row = _progress_row(db, lec["id"])
    rewound_to = 200 - REWIND_SEC
    assert int(row.watched_max_sec) == rewound_to

    # 스팸 6번 — 실 wall-clock이 거의 0이므로 전진도 거의 0이어야 한다
    for _ in range(6):
        r = _hb(client, tok, lec["id"], 200, st=st)
        assert r.status_code == 200, r.text
    state = r.json()
    assert state["checkpoint_due"] is False, "스팸만으로 게이트가 다시 열리면 되감기 무력화"
    # SPEED_FACTOR×수백 ms 오차 여유 — HEADROOM(5초/비트)이 지급되면 rewound+30 이상이 된다
    assert state["watched_max_sec"] <= rewound_to + 3, state

    # 실제 시청(wall-clock 경과)으로는 cp에 다시 닿고, 같은 체크포인트가 재트리거된다
    db.expire_all()
    row = _progress_row(db, lec["id"])
    row.updated_at = row.updated_at - timedelta(seconds=60)
    db.commit()
    r = _hb(client, tok, lec["id"], 200, st=st)
    assert r.json()["checkpoint_due"] is True, "재시청 후 같은 체크포인트 재도전"
    ch = _gate_challenge(client, site_key, tok, lec["id"])
    res = _gate_verify(client, site_key, tok, ch["challenge_token"], ["2"])
    assert res["success"] is True and res["lecture"]["next_checkpoint_sec"] is None


def test_rewind_lands_at_question_content_start(client, db, seed_org, media_dir):
    """문항이 내용 시작 시점을 지정하면 되감기는 폴백(cp-30)이 아니라 정확히 거기로 간다.

    되감기의 목적은 '그 문항이 다루는 대목의 재시청'이고, 대목의 시작은 문항의 속성이다.
    content_start=185(폴백이면 170)로 두 값이 갈리게 해 어느 경로를 탔는지 판별한다."""
    from datetime import timedelta

    from app.services.lecture_service import MAX_CHECKPOINT_FAILS

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, duration=1000).json()
    _add_question(client, ops_tok, lec["id"], position=200, answer=2, content_start=185)
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    st = _session_token(client, tok, lec["id"])

    row = _progress_row(db, lec["id"])
    row.updated_at = row.updated_at - timedelta(seconds=120)
    db.commit()
    assert _hb(client, tok, lec["id"], 200, st=st).json()["checkpoint_due"] is True

    for _ in range(MAX_CHECKPOINT_FAILS):
        ch = _gate_challenge(client, site_key, tok, lec["id"])
        res = _gate_verify(client, site_key, tok, ch["challenge_token"], ["0"])
    assert res["lecture"]["watched_max_sec"] == 185, "지정한 내용 시작이 아니라 폴백으로 되감겼다"
    db.expire_all()
    row = _progress_row(db, lec["id"])
    assert int(row.watched_max_sec) == 185
    assert int(row.next_checkpoint_sec) == 200  # 같은 체크포인트 재도전 유지


def test_rewind_min_boundary_cp1_cs0(client, db, seed_org, media_dir):
    """최소 경계 — cp=1 문항의 유일한 유효 내용 시작(cs=0)이 저장되고 실제로 되감긴다.

    클램프 min(max(0, cs), max(0, cp-1)) = min(0, 0) = 0 < cp=1 — 게이트가 닫히고
    재시청이 강제됨을 경계값에서 고정한다(0 <= cs < position 검증의 하한)."""
    from app.services.lecture_service import MAX_CHECKPOINT_FAILS

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    _add_question(client, ops_tok, lec["id"], position=1, answer=2, content_start=0)
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    _reach_checkpoint(client, tok, lec["id"])

    for _ in range(MAX_CHECKPOINT_FAILS):
        ch = _gate_challenge(client, site_key, tok, lec["id"])
        res = _gate_verify(client, site_key, tok, ch["challenge_token"], ["0"])
    assert res["lecture"]["watched_max_sec"] == 0
    db.expire_all()
    row = _progress_row(db, lec["id"])
    assert int(row.watched_max_sec) == 0 and int(row.next_checkpoint_sec) == 1
    # 되감겼으니 새 발급은 409 — 재시청 강제(무한 재도전 부활 없음)
    blocked = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert blocked.status_code == 409, blocked.text


def test_rewind_clamped_when_question_moved_mid_challenge(client, db, seed_org, media_dir):
    """발급~채점 사이 문항 시점 이동 경합 — 낡은 content_start가 와도 클램프가 cp 앞을 보장.

    챌린지 토큰(qid 봉인) 발급 후 강사가 그 문항을 position=500·content_start=490으로
    옮기면, 뒤늦게 도착한 오답 verify의 rewind_to(490)는 현재 cp(200) 기준으로는 '앞으로
    감기'다. min(rewind_to, cp-1) 클램프가 199로 눌러 watched < cp(재시청 강제)를 지킨다 —
    이 방어선이 완화되면 되감기 없는 무한 재도전이 부활하므로 회귀로 고정한다.
    (cp가 유효하게 남는 전제는 같은 시점의 레거시 중복 핀 — API 가드 이전 데이터를
    직접 삽입으로 재현한다.)"""
    from app.models import LectureQuestion
    from app.services.lecture_service import MAX_CHECKPOINT_FAILS

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, duration=1000).json()
    qa = _add_question(client, ops_tok, lec["id"], position=200, answer=2, content_start=180)
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    st = _session_token(client, tok, lec["id"])

    from datetime import timedelta

    row = _progress_row(db, lec["id"])
    row.updated_at = row.updated_at - timedelta(seconds=120)
    db.commit()
    assert _hb(client, tok, lec["id"], 200, st=st).json()["checkpoint_due"] is True

    # 상한 직전까지 오답 — qid=qa(이 시점의 유일한 문항)
    for _ in range(MAX_CHECKPOINT_FAILS - 1):
        ch = _gate_challenge(client, site_key, tok, lec["id"])
        _gate_verify(client, site_key, tok, ch["challenge_token"], ["0"])
    # 마지막 챌린지를 '이동 전에' 발급해 qid=qa를 토큰에 봉인
    ch = _gate_challenge(client, site_key, tok, lec["id"])

    # 레거시 중복 핀(가드 이전 데이터) 직접 삽입 — 이동 후에도 cp=200이 유효하게 남는다
    db.add(
        LectureQuestion(
            lecture_id=lec["id"],
            position_sec=200,
            payload={"prompt": "레거시 중복 핀", "options": ["가", "나"], "explain": ""},
            answer_index=0,
            source="manual",
            status="active",
        )
    )
    db.commit()
    # 강사가 qa를 500초(내용 시작 490)로 이동 — 예약(200)은 중복 핀 덕에 살아남는다
    r = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{qa['id']}",
        json={"position_sec": 500, "content_start_sec": 490},
        headers=auth(ops_tok),
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    assert int(_progress_row(db, lec["id"]).next_checkpoint_sec) == 200

    # 뒤늦은 오답 verify(3회째) — rewind_to=490(낡음)이지만 클램프가 cp-1=199로 누른다
    res = _gate_verify(client, site_key, tok, ch["challenge_token"], ["0"])
    assert res["lecture"]["watched_max_sec"] == 199, "클램프가 완화되면 앞으로 감기가 된다"
    db.expire_all()
    row = _progress_row(db, lec["id"])
    assert int(row.watched_max_sec) == 199 < 200
    blocked = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert blocked.status_code == 409, blocked.text


def test_content_start_validation_and_clear(client, db, seed_org, media_dir):
    """내용 시작은 출제 시점보다 앞이어야 하고(=cp 이상 되감기는 무한 재도전 부활),
    수정에서 명시적 null이면 지정 해제(폴백 복귀), position만 옮겨 어긋나도 400."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, duration=1000).json()

    def _try_create(cs, position=100):
        return client.post(
            f"/api/v1/ops/lectures/{lec['id']}/questions",
            json={
                "position_sec": position,
                "content_start_sec": cs,
                "prompt": "내용 시작 검증",
                "options": ["가", "나"],
                "answer_index": 0,
                "status": "active",
            },
            headers=auth(ops_tok),
        )

    # 출제 시점과 같거나 뒤·음수 → 400 (같으면 되감기가 없어 재시청 없는 무한 재도전)
    assert _try_create(100).status_code == 400
    assert _try_create(150).status_code == 400
    assert _try_create(-1).status_code == 400
    r = _try_create(40)
    assert r.status_code == 200, r.text
    q = r.json()
    assert q["content_start_sec"] == 40

    # position만 앞으로 옮겨 기존 내용 시작(40)과 어긋나는 조합 — 최종 상태 검증에 걸린다
    r = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}",
        json={"position_sec": 30},
        headers=auth(ops_tok),
    )
    assert r.status_code == 400, r.text
    # 테스트 하네스는 요청 간 세션을 공유한다 — 400으로 중단된 요청의 더티 상태(position=30)를
    # 버려 프로덕션(요청별 세션·미커밋 폐기)과 같은 전제로 되돌린다.
    db.rollback()

    # 명시적 null = 지정 해제(폴백 복귀) — 미전송(변경 없음)과 구분된다
    r = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}",
        json={"content_start_sec": None},
        headers=auth(ops_tok),
    )
    assert r.status_code == 200, r.text
    assert r.json()["content_start_sec"] is None
    # 미전송이면 그대로 유지되는지 — 다시 지정 후 prompt만 수정
    r = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}",
        json={"content_start_sec": 40},
        headers=auth(ops_tok),
    )
    assert r.status_code == 200 and r.json()["content_start_sec"] == 40
    r = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}",
        json={"prompt": "프롬프트만 수정"},
        headers=auth(ops_tok),
    )
    assert r.status_code == 200 and r.json()["content_start_sec"] == 40


def test_pass_inside_grace_still_reserves_adjacent_pin(client, db, seed_org, media_dir):
    """GRACE 유예(cp+15초) 안에 다음 핀이 있어도 통과 후 반드시 예약된다.

    재예약 기준이 watched_max(클램프로 cp+GRACE까지 부풂)면 (cp, cp+GRACE] 안의 핀이
    '이미 겪은 것'으로 오판돼 영구 스킵된다(skeptic 실증 — position을 크게 신고하는
    봇이 강사가 낸 인접 문항 하나를 통째로 우회). 기준을 cp로 바꾸면 예약이 잡히고,
    watched가 이미 그 지점을 지났어도 다음 하트비트가 즉시 게이트를 연다."""
    from datetime import timedelta

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, duration=1000).json()
    _add_question(client, ops_tok, lec["id"], position=3, answer=2)
    _add_question(client, ops_tok, lec["id"], position=10, answer=2, prompt="GRACE 안 인접 핀")
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    st = _session_token(client, tok, lec["id"])

    # 봇 시나리오: position=50 과다 신고 — 클램프가 cp+GRACE(3+15=18)까지 허용
    row = _progress_row(db, lec["id"])
    row.updated_at = row.updated_at - timedelta(seconds=60)
    db.commit()
    r = _hb(client, tok, lec["id"], 50, st=st)
    assert r.json()["checkpoint_due"] is True
    db.expire_all()
    row = _progress_row(db, lec["id"])
    assert int(row.watched_max_sec) == 18, "클램프 상한(cp+GRACE)까지 부푼 상태가 전제"

    # cp=3 통과 — 인접 핀 10이 예약에서 빠지면 안 된다
    ch = _gate_challenge(client, site_key, tok, lec["id"])
    res = _gate_verify(client, site_key, tok, ch["challenge_token"], ["2"])
    assert res["success"] is True
    assert res["lecture"]["next_checkpoint_sec"] == 10, "GRACE 안 인접 핀이 스킵됐다"

    # watched(18)가 이미 10을 지나 있으므로 다음 하트비트가 즉시 게이트를 연다
    r = _hb(client, tok, lec["id"], 18, st=st)
    assert r.json()["checkpoint_due"] is True


def test_reconcile_and_rereserve_respect_passed_pins(client, db, seed_org, media_dir):
    """되감긴 학생의 재도전 예약은 문항 CRUD(정합화)를 견디고, 통과한 핀은 다시 안 잡힌다.

    핀 간격 < REWIND_SEC이면 되감긴 학생은 이미 통과한 앞 핀 아래(watched < 앞 핀 < cp)에
    있다. watched 기준 정합화는 이 유효 예약을 '앞 핀을 건너뛴다'고 오판해 해제하고,
    재예약이 통과한 앞 핀을 다시 잡아 소급 재출제된다(skeptic 실증 — 운영자가 아무
    문항이나 수정하면 재현). 통과 이벤트(LectureCheckpointEvent)가 정본이다."""
    from datetime import timedelta

    from app.services.lecture_service import MAX_CHECKPOINT_FAILS

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, duration=1000).json()
    q100 = _add_question(client, ops_tok, lec["id"], position=100, answer=2, prompt="앞 핀")
    q120 = _add_question(client, ops_tok, lec["id"], position=120, answer=2, prompt="뒤 핀")
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    st = _session_token(client, tok, lec["id"])

    # 핀 100 도달·통과
    row = _progress_row(db, lec["id"])
    row.updated_at = row.updated_at - timedelta(seconds=60)
    db.commit()
    assert _hb(client, tok, lec["id"], 100, st=st).json()["checkpoint_due"] is True
    ch = _gate_challenge(client, site_key, tok, lec["id"])
    res = _gate_verify(client, site_key, tok, ch["challenge_token"], ["2"])
    assert res["success"] is True and res["lecture"]["next_checkpoint_sec"] == 120

    # 핀 120 도달 → 3연속 오답 → 되감기(watched=90 < 앞 핀 100 < cp=120)
    db.expire_all()
    row = _progress_row(db, lec["id"])
    row.updated_at = row.updated_at - timedelta(seconds=60)
    db.commit()
    assert _hb(client, tok, lec["id"], 120, st=st).json()["checkpoint_due"] is True
    for _ in range(MAX_CHECKPOINT_FAILS):
        ch = _gate_challenge(client, site_key, tok, lec["id"])
        _gate_verify(client, site_key, tok, ch["challenge_token"], ["0"])
    db.expire_all()
    row = _progress_row(db, lec["id"])
    assert int(row.watched_max_sec) == 90 and int(row.next_checkpoint_sec) == 120

    # 운영자가 '앞 핀' 문항의 프롬프트만 수정 — 되감긴 학생의 재도전 예약(120)이 살아야 한다
    r = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q100['id']}",
        json={"prompt": "앞 핀(오타 수정)"},
        headers=auth(ops_tok),
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    row = _progress_row(db, lec["id"])
    assert row.next_checkpoint_sec == 120, "정합화가 되감긴 학생의 유효 예약을 해제했다"

    # 뒤 핀(120) 문항을 삭제하면 예약은 해제되지만, 재예약이 '통과한' 100을 다시 잡으면 안 된다
    r = client.delete(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q120['id']}",
        headers=auth(ops_tok),
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    row = _progress_row(db, lec["id"])
    assert row.next_checkpoint_sec is None
    state = _hb(client, tok, lec["id"], 95, st=st).json()
    assert state["next_checkpoint_sec"] is None, "재예약이 이미 통과한 핀을 다시 잡았다(소급 재출제)"
    assert state["checkpoint_due"] is False


def test_lecture_challenge_is_multi_drag_and_never_leaks_explain(
    client, db, seed_org, media_dir
):
    """강의 게이트는 드래그 담기(multi)로 나가고, 해설은 어떤 응답에도 실리지 않는다.

    hint 유출 차단 회귀 고정 — 화면 억제와 무관하게 챌린지 응답에 explain이 실리면
    봇이 네트워크에서 읽는다(강사가 해설에 정답을 적으면 그대로 유출). verify의
    오답 응답도 마찬가지: 풀 문항은 반복 출제라 오답 응답의 정답이 파밍 재료가 된다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    _add_question(client, ops_tok, lec["id"], answer=2)  # explain="강의 앞부분에서 설명했어요."
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    _reach_checkpoint(client, tok, lec["id"])

    ch = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert ch.status_code == 200, ch.text
    body = ch.json()
    # 드래그 담기 모드 — type=multi + boxLabel(위젯이 이 키를 보고 드래그 모드로 그린다)
    assert body["type"] == "multi"
    assert body["boxLabel"]
    # 해설 미유출 — hint 키 자체가 없고(빈 문자열도 아님), 해설 문구가 응답 어디에도 없다
    assert "hint" not in body
    assert "explain" not in body
    assert "강의 앞부분에서 설명했어요" not in ch.text

    # 오답 verify 응답에도 정답·해설이 없다(파밍 차단) — 게이트는 유지된다
    vr = _gate_verify(client, site_key, tok, body["challenge_token"], ["0"])
    assert vr["success"] is False
    assert "answer" not in vr and "explain" not in vr
    assert "강의 앞부분에서 설명했어요" not in str(vr)

    # 정답 verify(통과) 응답에도 정답 키가 없다
    ch2 = _gate_challenge(client, site_key, tok, lec["id"])
    vr2 = _gate_verify(client, site_key, tok, ch2["challenge_token"], ["2"])
    assert vr2["success"] is True
    assert "answer" not in vr2 and "explain" not in vr2


def test_lecture_token_verify_requires_first_party_edu_key(
    client, db, seed_org, media_dir
):
    """강의 토큰 verify는 발급과 같은 자격(edu 1st-party)을 요구 — 정답 파밍 경로 차단.

    적대적 검토에서 실증된 우회: 정답·해설 제거가 edu 분기 안에만 있으면, 아무 non-edu
    사이트키(무료 요금제가 자가 발급하는 captcha 키로 충분)로 강의 토큰을 채점시켜
    오답 응답의 정답 집합을 수확할 수 있다. 그 경로는 체크포인트 실패 기록도 남기지
    않아 대가가 0이고, 파밍한 답으로 영상을 안 보고 게이트를 연다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    _add_question(client, ops_tok, lec["id"], answer_indexes=[1, 3])
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    _reach_checkpoint(client, tok, lec["id"])
    ch = _gate_challenge(client, site_key, tok, lec["id"])

    # 일반 캡차 키(non-edu) — 채점 자체를 거절, 정답 미노출
    other = client.post(
        "/api/v1/ops/api-keys",
        json={"organization_id": seed_org["org"].id, "product": "captcha"},
        headers=auth(ops_tok),
    )
    assert other.status_code == 200, other.text
    vr = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": ch["challenge_token"], "answer": ["0"]},
        headers={"X-Site-Key": other.json()["site_key"]},
    )
    assert vr.status_code == 403
    assert "answer" not in vr.text and "explain" not in vr.text

    # 외부 판매 edu 키(first_party=False)도 동일하게 거절
    # (_edu_key는 Pro 플랜을 새로 만들어 재호출이 안 되므로 구독이 선 상태에서 직접 발급)
    ext = client.post(
        "/api/v1/ops/api-keys",
        json={
            "organization_id": seed_org["org"].id,
            "product": "edu",
            "subject": "국어",
            "first_party": False,
        },
        headers=auth(ops_tok),
    )
    assert ext.status_code == 200, ext.text
    ext_key = ext.json()["site_key"]
    vr2 = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": ch["challenge_token"], "answer": ["0"]},
        headers={"X-Site-Key": ext_key, **auth(tok)},
    )
    assert vr2.status_code == 403
    assert "answer" not in vr2.text

    # 거절된 시도는 체크포인트를 소비하지 않는다 — 정상 키로 원래 게이트를 그대로 통과
    assert db.query(LectureCheckpointEvent).count() == 0
    ch2 = _gate_challenge(client, site_key, tok, lec["id"])
    vr3 = _gate_verify(client, site_key, tok, ch2["challenge_token"], ["1", "3"])
    assert vr3["success"] is True


def test_multi_answer_graded_as_exact_set(client, db, seed_org, media_dir):
    """다답 문항 — 집합 정확 일치만 통과. 부분 선택·초과 선택은 오답(부분 정답 없음)."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    q = _add_question(client, ops_tok, lec["id"], answer_indexes=[1, 3])
    # answer_index는 첫 값으로 동기화(구버전 읽기 경로 하위호환)
    assert q["answer_index"] == 1 and q["answer_indexes"] == [1, 3]
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    _reach_checkpoint(client, tok, lec["id"])

    # 부분 선택 → 오답, 게이트 유지
    ch = _gate_challenge(client, site_key, tok, lec["id"])
    assert ch["type"] == "multi" and ch["boxLabel"]
    vr = _gate_verify(client, site_key, tok, ch["challenge_token"], ["1"])
    assert vr["success"] is False
    assert vr["lecture"]["checkpoints_passed"] == 0

    # 초과 선택 → 오답
    ch = _gate_challenge(client, site_key, tok, lec["id"])
    vr = _gate_verify(client, site_key, tok, ch["challenge_token"], ["1", "3", "0"])
    assert vr["success"] is False

    # 정확 일치(순서 무관) → 통과 + 재예약
    ch = _gate_challenge(client, site_key, tok, lec["id"])
    vr = _gate_verify(client, site_key, tok, ch["challenge_token"], ["3", "1"])
    assert vr["success"] is True
    assert vr["lecture"]["checkpoints_passed"] == 1

    ev = db.query(LectureCheckpointEvent).order_by(LectureCheckpointEvent.created_at).all()
    assert [e.result for e in ev] == ["failed", "failed", "passed"]


def test_single_answer_row_backward_compat_as_multi(client, db, seed_org, media_dir):
    """answer_indexes NULL(기존 단일 정답 행) — multi로 나가고 1개만 담아 제출하면 통과."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    q = _add_question(client, ops_tok, lec["id"], answer=2)  # answer_indexes 미전송
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)

    # DB에 목록이 저장되지 않았다(NULL=[answer_index] 규약 — 기존 행과 동일 형태)
    db.expire_all()
    row = db.get(LectureQuestion, q["id"])
    assert row.answer_indexes is None and row.answer_index == 2
    # 운영자 행에는 유효 목록으로 채워 내려 콘솔이 체크박스를 그린다
    assert q["answer_indexes"] == [2]

    _reach_checkpoint(client, tok, lec["id"])
    ch = _gate_challenge(client, site_key, tok, lec["id"])
    assert ch["type"] == "multi" and ch["boxLabel"]
    # 2개 담으면 오답(집합 일치), 정답 1개만 담으면 통과
    vr = _gate_verify(client, site_key, tok, ch["challenge_token"], ["2", "0"])
    assert vr["success"] is False
    ch = _gate_challenge(client, site_key, tok, lec["id"])
    vr = _gate_verify(client, site_key, tok, ch["challenge_token"], ["2"])
    assert vr["success"] is True


def test_multi_answer_validation_400(client, db, seed_org, media_dir):
    """운영자 다답 검증 — 빈 배열·중복·범위 밖은 400 + 한국어 사유."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()

    def _post(answer_indexes):
        return client.post(
            f"/api/v1/ops/lectures/{lec['id']}/questions",
            json={
                "position_sec": 5,
                "prompt": "다답 검증",
                "options": ["가", "나", "다", "라"],
                "answer_index": 0,
                "answer_indexes": answer_indexes,
                "status": "active",
            },
            headers=auth(ops_tok),
        )

    r = _post([])
    assert r.status_code == 400 and "최소 1개" in r.json()["detail"]
    r = _post([1, 1])
    assert r.status_code == 400 and "중복" in r.json()["detail"]
    r = _post([0, 4])
    assert r.status_code == 400 and "범위" in r.json()["detail"]

    # 수정 경로도 같은 규칙 — 빈 배열이 기존 정답으로 조용히 대체되지 않는다
    q = _add_question(client, ops_tok, lec["id"], answer_indexes=[0, 2])
    for bad in ([], [2, 2], [9]):
        r = client.put(
            f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}",
            json={"answer_indexes": bad},
            headers=auth(ops_tok),
        )
        assert r.status_code == 400, (bad, r.text)

    # answer_index만 보내는 구버전 수정 — 단일 정답으로 전환(스테일 목록 잔존 금지)
    r = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}",
        json={"answer_index": 3},
        headers=auth(ops_tok),
    )
    assert r.status_code == 200
    assert r.json()["answer_index"] == 3 and r.json()["answer_indexes"] == [3]
    db.expire_all()
    assert db.get(LectureQuestion, q["id"]).answer_indexes is None


def test_challenge_before_checkpoint_409(client, db, seed_org, media_dir):
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    _add_question(client, ops_tok, lec["id"], position=60)  # 60초 핀
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))  # cp=60, watched=0

    ch = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert ch.status_code == 409


def test_challenge_no_questions_clear_4xx(client, db, seed_org, media_dir):
    """예약과 게이트 사이에서 문항이 사라져도 — 폴백(과목 은행) 출제 없이 명확한 4xx.

    운영자 삭제 경로는 이제 예약을 함께 걷으므로(아래 테스트) 이 상태는 경합으로만 생긴다:
    학생이 게이트로 오는 사이에 삭제 트랜잭션이 커밋된 창. 그 좁은 창에서도 강의와 무관한
    과목 은행 문제로 때우지 않는다는 것이 이 테스트가 지키는 규약이다. 경합을 재현할 수
    없으므로 삭제가 예약 정합화보다 먼저 보이는 상태를 DB에서 직접 만든다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    q = _add_question(client, ops_tok, lec["id"])
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    _reach_checkpoint(client, tok, lec["id"])  # 문항이 있는 상태에서 예약·도달

    # 경합 재현 — 예약은 그대로 둔 채 문항만 사라진 순간
    db.query(LectureQuestion).filter(LectureQuestion.id == q["id"]).update(
        {"status": "deleted"}
    )
    db.commit()

    ch = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert ch.status_code == 409
    assert "문항이 없" in ch.json()["detail"]


def test_deleting_last_question_does_not_strand_student(client, db, seed_org, media_dir):
    """운영자가 마지막 문항을 지우면 학생의 예약도 걷힌다 — 게이트 없는 지점에 갇히지 않는다.

    예약만 남으면 학생은 cp+GRACE에서 클램프된 채 게이트는 4xx라 안 뜨고, 강의를 영영
    끝낼 수 없다. 문항이 없으면 '검증 없음'이 정직한 상태이지 '진행 불가'가 아니다."""
    from datetime import timedelta

    from app.services.lecture_service import GRACE_SEC

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, duration=600).json()
    q = _add_question(client, ops_tok, lec["id"])  # 1초 핀
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))  # cp=1 예약
    st = _session_token(client, tok, lec["id"])  # 세션은 학생당 하나 — 끝까지 재사용
    r = _hb(client, tok, lec["id"], 1, st=st)
    assert r.json()["checkpoint_due"] is True  # 게이트에 닿아 클램프된 상태

    d = client.delete(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}", headers=auth(ops_tok)
    )
    assert d.status_code == 200, d.text

    # 충분한 wall-clock 경과 위장 — 클램프가 남아 있으면 1+GRACE에서 멈춘다
    # (스팸으로 올리면 안 된다 — HEADROOM은 시작 구간에만 지급, 스팸 회귀는 별도 테스트)
    row = _progress_row(db, lec["id"])
    row.updated_at = row.updated_at - timedelta(seconds=60)
    db.commit()
    r = _hb(client, tok, lec["id"], 500, st=st)
    assert r.json()["next_checkpoint_sec"] is None, "낼 문항이 없는데 예약이 남았다"
    assert r.json()["checkpoint_due"] is False
    assert r.json()["watched_max_sec"] > 1 + GRACE_SEC, "예약이 걷혔는데도 클램프에 갇혔다"


def test_no_questions_schedules_no_checkpoint(client, db, seed_org, media_dir):
    """문항 0개 강의는 체크포인트를 아예 예약하지 않는다 — 학생을 가두지 않는다.

    회귀: 예전에는 문항이 없어도 예약이 잡혀 학생이 cp+GRACE에서 클램프됐는데, 게이트는
    문항이 없어 4xx로 안 뜨니 '캡차가 안 뜨는데 진도도 안 나가는' 상태로 갇혔다(라이브
    제보의 직접 원인). 낼 문제가 없으면 검증을 걸 수 없다는 사실을 예약 단계에서 인정하고,
    문항 0개는 운영자 콘솔의 문항 수로 드러낸다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()  # 문항 없음
    tok = _student_token(client, seed_org)

    d = client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    assert d.json()["next_checkpoint_sec"] is None

    st = _session_token(client, tok, lec["id"])
    r = _hb(client, tok, lec["id"], 4, st=st)
    assert r.json()["checkpoint_due"] is False
    assert r.json()["watched_max_sec"] == 4, "문항이 없다고 진행이 막히면 안 된다"


def test_external_key_lecture_forbidden(client, db, seed_org, media_dir):
    """외부 판매 키(first_party=False)는 강의 파라미터 사용 불가 — 403."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    _add_question(client, ops_tok, lec["id"])
    ext_key = _edu_key(client, db, seed_org, ops_tok, first_party=False)
    tok = _student_token(client, seed_org)
    _reach_checkpoint(client, tok, lec["id"])

    ch = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": ext_key, **auth(tok)},
    )
    assert ch.status_code == 403


# ================================================================ 스트리밍
def test_stream_requires_signature_and_serves_range(client, db, seed_org, media_dir):
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, size=4096).json()
    tok = _student_token(client, seed_org)

    # 서명 없음 / 위조 서명 → 403
    assert client.get(f"/api/v1/lectures/{lec['id']}/stream").status_code == 403
    assert (
        client.get(f"/api/v1/lectures/{lec['id']}/stream?t=garbage").status_code == 403
    )

    # 세션 발급에서 받은 서명 URL → 200 + Range 요청 시 206/Content-Range
    s = _start_session(client, tok, lec["id"])
    assert s.status_code == 200, s.text
    stream_url = s.json()["stream_url"]
    full = client.get(stream_url)
    assert full.status_code == 200, full.text
    assert full.headers["content-type"] == "video/mp4"

    part = client.get(stream_url, headers={"Range": "bytes=0-1023"})
    assert part.status_code == 206, part.status_code
    assert part.headers.get("content-range") == "bytes 0-1023/4096"
    assert len(part.content) == 1024

    # 다른 강의의 토큰으로는 접근 불가(lecture_id 바인딩)
    lec2 = _upload_lecture(client, ops_tok, title="다른 강의", size=100).json()
    t = stream_url.split("?t=")[1]
    assert (
        client.get(f"/api/v1/lectures/{lec2['id']}/stream?t={t}").status_code == 403
    )


def test_stream_token_dies_after_takeover(client, db, seed_org, media_dir):
    """스트림 세션 바인딩 — takeover로 세션이 교체되면 이전 스트림 URL은 즉시 403.

    동시 차단이 '진도 인정'만이 아니라 영상 바이트 전달에도 걸린다(skeptic PLAUSIBLE
    지적의 수정): 두 번째 기기가 세션을 이어받는 순간 첫 기기는 영상 자체를 못 받는다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, size=4096).json()
    tok = _student_token(client, seed_org)

    # 기기 A: 세션 발급 → 스트림 정상(206 Range 포함)
    a = _start_session(client, tok, lec["id"]).json()
    assert client.get(a["stream_url"]).status_code == 200
    part = client.get(a["stream_url"], headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert part.headers.get("content-range") == "bytes 0-99/4096"

    # 기기 B: 이어보기(takeover) → 새 세션·새 스트림 URL
    b = client.post(f"/api/v1/lectures/{lec['id']}/takeover", headers=auth(tok))
    assert b.status_code == 200, b.text
    assert b.json()["session_id"] != a["session_id"]

    # 기기 A의 이전 스트림 URL — 서명은 아직 유효하지만 세션이 교체돼 403
    dead = client.get(a["stream_url"], headers={"Range": "bytes=0-99"})
    assert dead.status_code == 403, dead.status_code

    # 기기 B의 새 URL은 Range까지 정상(seek이 깨지지 않는다)
    fresh = client.get(b.json()["stream_url"], headers={"Range": "bytes=100-199"})
    assert fresh.status_code == 206
    assert fresh.headers.get("content-range") == "bytes 100-199/4096"


# ================================================================ 문항 CRUD · LLM 생성
def test_question_crud(client, db, seed_org, media_dir):
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()

    q = _add_question(client, ops_tok, lec["id"], position=30, answer=1, status="draft")
    assert q["status"] == "draft" and q["answer_index"] == 1

    # 목록
    ls = client.get(f"/api/v1/ops/lectures/{lec['id']}/questions", headers=auth(ops_tok))
    assert ls.status_code == 200 and len(ls.json()) == 1

    # draft 문항은 학생 게이트에 출제되지 않는다 → question_count(active) 0
    tok = _student_token(client, seed_org)
    detail = client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok)).json()
    assert detail["question_count"] == 0

    # 수정(승인 → active)
    up = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}",
        json={"status": "active", "answer_index": 3},
        headers=auth(ops_tok),
    )
    assert up.status_code == 200
    assert up.json()["status"] == "active" and up.json()["answer_index"] == 3

    # 잘못된 answer_index → 400
    bad = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}",
        json={"answer_index": 9},
        headers=auth(ops_tok),
    )
    assert bad.status_code == 400

    # 삭제(소프트) → 목록에서 사라짐
    rm = client.delete(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}", headers=auth(ops_tok)
    )
    assert rm.status_code == 200
    ls2 = client.get(f"/api/v1/ops/lectures/{lec['id']}/questions", headers=auth(ops_tok))
    assert ls2.json() == []
    row = db.get(LectureQuestion, q["id"])
    assert row.status == "deleted"  # 물리 삭제 아님

    # 감사기록 3종
    for action in ("lecture.question.create", "lecture.question.update", "lecture.question.delete"):
        assert db.query(AuditLog).filter(AuditLog.action == action).count() >= 1


def test_generate_without_api_key_returns_honest_503(client, db, seed_org, media_dir, monkeypatch):
    """키 미설정 — stub 문항 반환 없이 503으로 정직하게 실패."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "")
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()

    r = client.post(
        f"/api/v1/ops/lectures/{lec['id']}/questions/generate",
        json={"n": 3},
        headers=auth(ops_tok),
    )
    assert r.status_code == 503
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]
    assert db.query(LectureQuestion).count() == 0  # 어떤 문항도 생성되지 않는다


# ================================================================ 강의 메타 CRUD
def test_lecture_update_delete_and_student_visibility(client, db, seed_org, media_dir):
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    tok = _student_token(client, seed_org)

    assert len(client.get("/api/v1/lectures", headers=auth(tok)).json()) == 1

    # hidden → 학생 목록·상세에서 사라진다
    up = client.put(
        f"/api/v1/ops/lectures/{lec['id']}",
        json={"status": "hidden", "title": "수정된 제목"},
        headers=auth(ops_tok),
    )
    assert up.status_code == 200 and up.json()["title"] == "수정된 제목"
    assert client.get("/api/v1/lectures", headers=auth(tok)).json() == []
    assert client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok)).status_code == 404

    # 소프트 삭제 → ops 목록에서도 제외, 행은 보존, '영상 파일은 물리 삭제'
    assert (media_dir / f"{lec['id']}.mp4").is_file()  # 삭제 전엔 존재
    rm = client.delete(f"/api/v1/ops/lectures/{lec['id']}", headers=auth(ops_tok))
    assert rm.status_code == 200
    assert client.get("/api/v1/ops/lectures", headers=auth(ops_tok)).json() == []
    row = db.get(Lecture, lec["id"])
    db.refresh(row)
    assert row.status == "deleted"  # 레코드·이력은 보존
    assert int(row.video_bytes or 0) == 0  # 파일 부재 표기
    assert not (media_dir / f"{lec['id']}.mp4").exists(), "영상 파일이 물리 삭제되지 않았다"

    # 파일이 이미 없는 강의도 삭제는 예외 없이 통과(unlink missing_ok 멱등)
    lec3 = _upload_lecture(client, ops_tok, title="셋").json()
    (media_dir / f"{lec3['id']}.mp4").unlink()  # 파일을 먼저 지워 부재 상황 재현
    rm3 = client.delete(f"/api/v1/ops/lectures/{lec3['id']}", headers=auth(ops_tok))
    assert rm3.status_code == 200, rm3.text
    assert db.get(Lecture, lec3["id"]).status == "deleted"

    # 학생 진행 행은 학생·강의당 1개(upsert) — 상세 재진입해도 중복 생성 없음
    lec2 = _upload_lecture(client, ops_tok, title="둘").json()
    client.get(f"/api/v1/lectures/{lec2['id']}", headers=auth(tok))
    client.get(f"/api/v1/lectures/{lec2['id']}", headers=auth(tok))
    assert (
        db.query(LectureWatchProgress)
        .filter(LectureWatchProgress.lecture_id == lec2["id"])
        .count()
        == 1
    )


# ================================================================ 동시접속 차단(세션)
def _progress_row(db, lecture_id):
    return (
        db.query(LectureWatchProgress)
        .filter(LectureWatchProgress.lecture_id == lecture_id)
        .first()
    )


def test_concurrent_session_409_and_takeover(client, db, seed_org, media_dir):
    """같은 학생의 두 세션 동시 재생 — 두 번째는 발급부터 409(active_elsewhere), takeover 후 역전.

    다른 강의여도 같은 학생이면 동시 재생이다(한 사람이 두 강의를 동시에 볼 수 없음)."""
    ops_tok = _ops(client, db)
    lec1 = _upload_lecture(client, ops_tok).json()
    lec2 = _upload_lecture(client, ops_tok, title="다른 강의").json()
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec1['id']}", headers=auth(tok))
    client.get(f"/api/v1/lectures/{lec2['id']}", headers=auth(tok))

    # 기기 A(강의1) 재생 시작 — 세션 발급 + 하트비트
    st_a = _session_token(client, tok, lec1["id"])
    assert _hb(client, tok, lec1["id"], 3, st=st_a).status_code == 200

    # 기기 B — 같은 강의든 다른 강의든 세션 발급 자체가 409 + 구조화 detail
    rb_same = _start_session(client, tok, lec1["id"])
    assert rb_same.status_code == 409, rb_same.text
    assert rb_same.json()["detail"]["active_elsewhere"] is True
    rb_other = _start_session(client, tok, lec2["id"])
    assert rb_other.status_code == 409
    assert rb_other.json()["detail"]["active_elsewhere"] is True

    # 409는 진행을 전진시키지 않는다
    assert int(_progress_row(db, lec2["id"]).watched_max_sec or 0) == 0

    # takeover('여기서 계속하기') → 기기 B가 새 서버 세션으로 이어받고 하트비트 통과
    tk = client.post(f"/api/v1/lectures/{lec2['id']}/takeover", headers=auth(tok))
    assert tk.status_code == 200 and tk.json()["ok"] is True
    st_b = tk.json()["session_token"]
    assert _hb(client, tok, lec2["id"], 3, st=st_b).status_code == 200

    # 무효화된 기기 A의 다음 하트비트는 409 — 동시 재생 불가 유지
    ra = _hb(client, tok, lec1["id"], 6, st=st_a)
    assert ra.status_code == 409
    assert ra.json()["detail"]["active_elsewhere"] is True


def test_forged_session_id_in_body_is_ignored(client, db, seed_org, media_dir):
    """클라가 본문에 session_id를 실어 보내도 무시된다 — 세션의 정본은 서버 발급 토큰뿐.

    (구계약 회귀 방지: session_id 본문 필드를 다시 읽기 시작하면 담합 우회가 부활한다.)"""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    tok = _student_token(client, seed_org)
    s = _start_session(client, tok, lec["id"]).json()

    # 본문에 위조 session_id를 끼워 넣어도 200 — 서버 세션은 토큰의 sid 그대로
    r = client.post(
        f"/api/v1/lectures/{lec['id']}/progress",
        json={"position_sec": 3, "session_id": "sess-forged-0001"},
        headers={"X-Lecture-Session": s["session_token"], **auth(tok)},
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    row = _progress_row(db, lec["id"])
    assert row.session_id == s["session_id"]  # 서버 발급값 유지
    assert row.session_id != "sess-forged-0001"


def test_collusion_and_forged_tokens_blocked(client, db, seed_org, media_dir):
    """담합 시나리오 — 두 기기가 식별자를 짜고 와도 서버 세션은 각자 발급이라 두 번째는 409.

    토큰 없이 / 학생 JWT를 세션 토큰인 척(type 검사) / 다른 강의의 세션 토큰(lec 바인딩)
    전부 403. 남는 한계: 발급된 '세션 토큰 자체'를 두 기기가 실시간 공유하면 서버에겐
    한 세션으로 보인다 — 기기 구분 수단이 없는 한 원리적으로 판별 불가(보고서에 명시)."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    tok = _student_token(client, seed_org)

    # 기기 A 정상 시작
    st_a = _session_token(client, tok, lec["id"])
    assert _hb(client, tok, lec["id"], 3, st=st_a).status_code == 200

    # 기기 B: 예전처럼 '같은 session_id를 보내는' 담합은 불가능 — 보낼 곳이 없다.
    # 자기 세션을 받으려면 발급을 거쳐야 하고, A가 활성이라 409.
    assert _start_session(client, tok, lec["id"]).status_code == 409

    # 세션 토큰 없이 하트비트 → 403
    no_tok = client.post(
        f"/api/v1/lectures/{lec['id']}/progress",
        json={"position_sec": 3},
        headers=auth(tok),
    )
    assert no_tok.status_code == 403

    # 학생 로그인 JWT(type=access)를 세션 토큰 자리에 꽂아도 403 (type 검사)
    wrong_type = _hb(client, tok, lec["id"], 3, st=tok)
    assert wrong_type.status_code == 403

    # 다른 강의용 세션 토큰 → 403 (lec 바인딩)
    lec2 = _upload_lecture(client, ops_tok, title="둘").json()
    assert _hb(client, tok, lec2["id"], 3, st=st_a).status_code == 403


def test_dead_session_auto_reclaimed_after_ttl(client, db, seed_org, media_dir):
    """30초 무하트비트 세션은 죽은 것 — 새 세션이 409 없이 정상 진입(새로고침·크래시 오탐 방지).

    '30초 경과'는 앱이 쓴 last_heartbeat_at을 뒤로 미는 방식으로 시뮬레이션한다 —
    비교 코드 경로(claim_session의 threshold 계산)는 전부 실제로 탄다."""
    from datetime import timedelta

    from app.db.base import _now
    from app.services.lecture_service import SESSION_TTL_SEC

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    old = _start_session(client, tok, lec["id"]).json()
    assert _hb(client, tok, lec["id"], 3, st=old["session_token"]).status_code == 200

    # TTL 직전이면 아직 살아있는 세션 — 새 발급은 409
    assert _start_session(client, tok, lec["id"]).status_code == 409

    row = _progress_row(db, lec["id"])
    row.last_heartbeat_at = _now() - timedelta(seconds=SESSION_TTL_SEC + 1)
    db.commit()

    # TTL 경과 — 죽은 세션으로 간주, 새 세션이 409 없이 발급·재점유
    fresh = _start_session(client, tok, lec["id"])
    assert fresh.status_code == 200, fresh.text
    assert _hb(client, tok, lec["id"], 4, st=fresh.json()["session_token"]).status_code == 200
    db.expire_all()
    assert _progress_row(db, lec["id"]).session_id == fresh.json()["session_id"]
    assert fresh.json()["session_id"] != old["session_id"]


def test_mutation_utcnow_breaks_dead_session_reclaim(client, db, seed_org, media_dir, monkeypatch):
    """변이 테스트(판별력 증명) — claim_session의 시각을 utcnow로 되돌리면 죽은 세션
    재점유가 실패한다(정상 사용자 오탐).

    utcnow(KST-9h)면 threshold가 9시간 과거로 밀려, 31초 전에 죽은 세션도 '살아있음'으로
    보여 새 세션이 409를 받는다. 위 test_dead_session_auto_reclaimed가 같은 상황에서
    200을 단언하므로 두 테스트가 함께 '_now 로컬 naive 규약'의 판별력을 증명한다."""
    import time as _time
    from datetime import datetime as _dt
    from datetime import timedelta

    from app.db.base import _now
    from app.services import lecture_service
    from app.services.lecture_service import SESSION_TTL_SEC

    if _time.timezone == 0:
        pytest.skip("로컬 TZ가 UTC라 utcnow 변이가 무해 — 판별 불가 환경")

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    old = _start_session(client, tok, lec["id"]).json()
    assert _hb(client, tok, lec["id"], 3, st=old["session_token"]).status_code == 200

    row = _progress_row(db, lec["id"])
    row.last_heartbeat_at = _now() - timedelta(seconds=SESSION_TTL_SEC + 1)
    db.commit()

    monkeypatch.setattr(lecture_service, "_now", _dt.utcnow)  # 규약 위반을 일부러 주입
    r = _start_session(client, tok, lec["id"])
    # 죽은 세션인데 살아있다고 오판 — utcnow 혼입이 성실한 사용자를 잠근다는 실증
    assert r.status_code == 409


def test_takeover_spam_rate_limited(client, db, seed_org, media_dir):
    """takeover 스팸 차단 — 두 세션이 번갈아 takeover하면 동시 차단이 무력화되므로
    시간당 RATE_TAKEOVER_PER_HOUR 초과 시 429."""
    from app.api.v1.endpoints.lectures import RATE_TAKEOVER_PER_HOUR

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))

    for _ in range(RATE_TAKEOVER_PER_HOUR):
        r = client.post(f"/api/v1/lectures/{lec['id']}/takeover", headers=auth(tok))
        assert r.status_code == 200, r.text
    over = client.post(f"/api/v1/lectures/{lec['id']}/takeover", headers=auth(tok))
    assert over.status_code == 429


# ================================================================ 캡차 면제 없음
def test_interacted_self_report_grants_no_exemption(client, db, seed_org, media_dir):
    """★ 상호작용 자기신고로는 캡차를 건너뛸 수 없다 — 면제 장치 제거의 회귀 고정.

    면제는 '성실한 시청자를 덜 방해한다'는 명분이었으나 전제가 틀렸다: 강의에 집중하는
    학생은 아무것도 만지지 않아 면제가 도우려던 사람을 못 돕고, interacted는 클라이언트
    자기신고라 위조 가능해 봇·딴짓만 이득을 봤다(방향이 거꾸로). 실제로 위조 한 줄로
    강사 지정 고정 문항을 건너뛰는 것이 실증됐다. 이제 체크포인트에 닿으면 예외 없이 캡차다.

    구버전 플레이어가 interacted·tab_hidden을 계속 보내도 무해해야 한다(조용히 무시)."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    _add_question(client, ops_tok, lec["id"])  # 1초 핀 — 낼 문항이 있어야 체크포인트가 잡힌다
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    st = _session_token(client, tok, lec["id"])

    # interacted/tab_hidden(구계약 자기신고)을 계속 보내도 매번 캡차를 요구한다
    for _ in range(3):
        r = _hb(client, tok, lec["id"], 1, st=st, interacted=True, tab_hidden=True)
        assert r.status_code == 200, r.text  # 구버전 필드가 422를 내지 않는다
        assert r.json()["checkpoint_due"] is True, "interacted 자기신고로 캡차가 면제됐다"
        assert "exempted" not in r.json(), "면제 개념이 응답에 남아 있다"

    # 면제 이벤트가 하나도 안 남는다 — 면제 경로 자체가 없다
    assert (
        db.query(LectureCheckpointEvent)
        .filter(LectureCheckpointEvent.result == "exempted")
        .count()
        == 0
    )


# ================================================================ 핀(출제 시점) 예약
def test_pin_unit_fires_in_order_and_respects_watched():
    """단위 — 아직 안 닿은 가장 이른 핀에 잡히고, 지나온 핀은 다시 안 잡는다.

    (구간(window_sec) 출제는 제거됨 0717 — 되감기(cp-REWIND) 기준이 cp라, cp가 내용
    시점과 멀어질 수 있는 구간은 엉뚱한 대목을 되감았다. 이제 cp == position_sec.)"""
    from app.services.lecture_service import next_checkpoint

    # 가장 이른 핀부터(정렬 무관), 지나온 핀은 소진
    assert next_checkpoint(0, 1000, [80, 30, 50]) == 30
    assert next_checkpoint(30, 1000, [80, 30, 50]) == 50
    assert next_checkpoint(80, 1000, [80, 30, 50]) is None  # 전부 소진
    # 예약은 정확히 핀 시점 — 무작위 없음(cp == position이 되감기 정합의 전제)
    assert next_checkpoint(199, 1000, [200]) == 200
    # 소진 판정은 watched < pin — 핀에 닿았다는 것은 그 체크포인트를 이미 겪었다는 뜻
    # (클램프 때문에 캡차 없이 핀을 지날 수 없다)
    assert next_checkpoint(200, 1000, [200]) is None
    # 영상 밖 핀은 무시 — 낼 수 없는 지점을 예약하지 않는다
    assert next_checkpoint(0, 1000, [5000]) is None
    # 0초 핀은 잡히지 않는다(watched < pin 판정 — 아직 아무것도 안 본 지점).
    # 생성/수정 검증이 active 문항의 position>=1을 강제해 이 상태 자체를 막는다.
    assert next_checkpoint(0, 1000, [0]) is None
    # 핀이 없으면 예약도 없다 — 낼 문제 없는 예약은 조용한 실패가 된다
    assert next_checkpoint(0, 1000, []) is None


def test_pinned_question_fires_at_its_position_and_is_served(
    client, db, seed_org, media_dir
):
    """통합 — 핀 시점에 체크포인트가 잡히고, 그 시점엔 '그 시점의' 문항만 나온다.

    뒤(500초) 핀 문항이 앞(3초) 게이트에 새어나오면 안 된다 — 미리 소진되면
    정작 지정 시점에 낼 문제가 사라진다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, duration=1000).json()
    # 핀 시점은 하트비트 헤드룸(5초) 안 — 속도 상한 때문에 먼 지점은 한 비트로 못 닿는다
    r = client.post(
        f"/api/v1/ops/lectures/{lec['id']}/questions",
        json={
            "position_sec": 3,
            "prompt": "방금 화면에 나온 그래프의 색은?",
            "options": ["빨강", "파랑", "초록", "노랑"],
            "answer_index": 2,
            "status": "active",
        },
        headers=auth(ops_tok),
    )
    assert r.status_code == 200, r.text
    later = _add_question(
        client, ops_tok, lec["id"], position=500, answer=0, prompt="뒤 시점 전용 문항"
    )

    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    d = client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    assert d.json()["next_checkpoint_sec"] == 3, "가장 이른 핀 시점에 잡혀야 한다"

    st = _session_token(client, tok, lec["id"])
    r = _hb(client, tok, lec["id"], 3, st=st)
    assert r.json()["checkpoint_due"] is True

    ch = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert ch.status_code == 200, ch.text
    # 그 시점엔 그 시점의 문항이 나온다 — 뒤 핀 문항이 새어나오지 않는다
    assert ch.json()["prompt"] == "방금 화면에 나온 그래프의 색은?"
    assert later["prompt"] != ch.json()["prompt"]


def test_pin_added_later_reclaims_stale_reservation(client, db, seed_org, media_dir):
    """강사가 나중에 더 이른 핀을 추가하면, 그 지점을 지나칠 예약이 다시 잡힌다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, duration=1000).json()
    _add_question(client, ops_tok, lec["id"], position=600)
    tok = _student_token(client, seed_org)
    d = client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    assert d.json()["next_checkpoint_sec"] == 600  # 옛 예약 — 새 핀 시점을 지나쳐 버린다

    r = client.post(
        f"/api/v1/ops/lectures/{lec['id']}/questions",
        json={
            "position_sec": 40,
            "prompt": "나중에 추가한 핀 문항",
            "options": ["가", "나"],
            "answer_index": 0,
            "status": "active",
        },
        headers=auth(ops_tok),
    )
    assert r.status_code == 200, r.text

    st = _session_token(client, tok, lec["id"])
    hb = _hb(client, tok, lec["id"], 4, st=st)
    assert hb.json()["next_checkpoint_sec"] == 40, "핀 추가 후에도 옛 예약이 남았다"


def test_pin_validation_rejects_unreachable_positions(client, db, seed_org, media_dir):
    """뜰 수 없는 공개 문항은 조용히 죽는 대신 거절한다 — 0초(시청 전)·영상 밖.

    핀은 watched < start 판정이라 0초 핀은 영영 안 뜨고, 영상 밖(100초 강의에 900 오타)
    시점은 예약 자체가 안 잡힌다. 유일 문항이면 그 강의의 시청 검증이 통째로 꺼지는데
    목록에는 멀쩡한 active로 보여 알아챌 방법이 없다(적대적 검토에서 실증)."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, duration=600).json()
    body = {
        "prompt": "문항",
        "options": ["가", "나"],
        "answer_index": 0,
        "status": "active",
    }
    r0 = client.post(
        f"/api/v1/ops/lectures/{lec['id']}/questions",
        json={**body, "position_sec": 0},
        headers=auth(ops_tok),
    )
    assert r0.status_code == 400 and "1초 이상" in r0.json()["detail"]

    r1 = client.post(
        f"/api/v1/ops/lectures/{lec['id']}/questions",
        json={**body, "position_sec": 600},
        headers=auth(ops_tok),
    )
    assert r1.status_code == 400 and "영상 길이를 벗어" in r1.json()["detail"]


def test_draft_at_zero_allowed_until_activation(client, db, seed_org, media_dir):
    """draft는 '시점 미배치'(position 0)로 존재할 수 있다 — LLM 생성 문항의 기본 상태.

    단 공개(활성화)하려면 시점을 지정해야 한다: 0초 핀은 활성화돼도 영영 안 떠서,
    승인만 하고 배치를 잊으면 검증이 조용히 꺼진 문항이 생기기 때문이다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, duration=600).json()

    # 시점 미배치 draft 생성 OK
    q = _add_question(client, ops_tok, lec["id"], position=0, status="draft")
    assert q["status"] == "draft" and q["position_sec"] == 0

    # 시점을 그대로 두고 문구만 고치는 draft 수정도 OK(검수 작업 흐름)
    up = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}",
        json={"prompt": "검수하며 문구 수정"},
        headers=auth(ops_tok),
    )
    assert up.status_code == 200, up.text

    # 시점 없이 승인(활성화) → 400, 시점을 지정하면 통과
    bad = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}",
        json={"status": "active"},
        headers=auth(ops_tok),
    )
    assert bad.status_code == 400 and "1초 이상" in bad.json()["detail"]
    ok = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}",
        json={"status": "active", "position_sec": 30},
        headers=auth(ops_tok),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "active" and ok.json()["position_sec"] == 30


def test_shrinking_duration_rejects_orphaned_questions(client, db, seed_org, media_dir):
    """★ 강의 길이를 줄여 문항을 영상 밖으로 밀어내는 것도 거절한다.

    문항 PUT은 같은 상황을 400으로 막는데 강의 PUT만 통과시키면, 검증이 조용히 꺼지고
    강사는 나중에 설명만 고치려다 영문 모를 400을 맞는다(적대적 검토에서 실증)."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, duration=1000).json()
    _add_question(client, ops_tok, lec["id"], position=500)

    u = client.put(
        f"/api/v1/ops/lectures/{lec['id']}",
        json={"duration_sec": 100},
        headers=auth(ops_tok),
    )
    assert u.status_code == 400, u.text
    assert "벗어나는 공개 문항이 1개" in u.json()["detail"]

    # 문항을 먼저 정리하면 통과한다 — 막기만 하고 길을 안 열어주면 안 된다
    qs = client.get(
        f"/api/v1/ops/lectures/{lec['id']}/questions", headers=auth(ops_tok)
    ).json()
    client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{qs[0]['id']}",
        json={"position_sec": 50},
        headers=auth(ops_tok),
    )
    u2 = client.put(
        f"/api/v1/ops/lectures/{lec['id']}",
        json={"duration_sec": 100},
        headers=auth(ops_tok),
    )
    assert u2.status_code == 200, u2.text


def test_duplicate_pin_at_same_position_rejected(client, db, seed_org, media_dir):
    """★ 같은 시점에 공개 문항 둘 — 하나만 뜨고 나머지는 영구 사문이 되므로 거절한다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, duration=1000).json()
    body = {
        "position_sec": 300,
        "options": ["가", "나"],
        "answer_index": 0,
        "status": "active",
    }
    r1 = client.post(
        f"/api/v1/ops/lectures/{lec['id']}/questions",
        json={**body, "prompt": "핀 A"},
        headers=auth(ops_tok),
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        f"/api/v1/ops/lectures/{lec['id']}/questions",
        json={**body, "prompt": "핀 B"},
        headers=auth(ops_tok),
    )
    assert r2.status_code == 400, r2.text
    assert "같은 시점에 출제되는" in r2.json()["detail"]


def test_ops_preview_stream_is_isolated_from_student_stream(
    client, db, seed_org, media_dir
):
    """운영자 미리보기 스트림 — 문항 시점 확인·화면 따오기용. 학생 경로와 교차 오염 금지.

    ① 운영자는 세션 없이 재생할 수 있다(운영자는 시청 검증 대상이 아니다).
    ② 학생 스트림 토큰으로는 못 들어온다 — 들어가지면 세션 바인딩(동시재생 차단)이
       통째로 우회된다. 반대로 운영자 토큰도 학생 스트림에 못 들어간다.
    ③ 미리보기 발급은 운영자 권한이 필요하다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    tok = _student_token(client, seed_org)

    p = client.post(f"/api/v1/ops/lectures/{lec['id']}/preview", headers=auth(ops_tok))
    assert p.status_code == 200, p.text
    ops_stream = p.json()["stream_url"]
    assert client.get(ops_stream).status_code == 200  # ① 세션 없이 재생

    # ② 학생 토큰 ↔ 운영자 경로 교차 차단
    s = client.post(f"/api/v1/lectures/{lec['id']}/session", headers=auth(tok))
    student_t = s.json()["stream_url"].split("t=")[1]
    ops_t = ops_stream.split("t=")[1]
    assert client.get(f"/api/v1/ops/lectures/{lec['id']}/stream?t={student_t}").status_code == 403
    assert client.get(f"/api/v1/lectures/{lec['id']}/stream?t={ops_t}").status_code == 403

    # ③ 학생은 미리보기를 발급받을 수 없다
    assert client.post(
        f"/api/v1/ops/lectures/{lec['id']}/preview", headers=auth(tok)
    ).status_code in (401, 403)


def test_ops_preview_does_not_steal_student_session(client, db, seed_org, media_dir):
    """운영자 미리보기는 세션을 점유하지 않는다 — 시청 중인 학생을 걷어차면 안 된다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    st = _session_token(client, tok, lec["id"])

    client.post(f"/api/v1/ops/lectures/{lec['id']}/preview", headers=auth(ops_tok))

    # 학생 하트비트가 그대로 살아 있어야 한다
    assert _hb(client, tok, lec["id"], 2, st=st).status_code == 200


def test_cleared_reservation_is_rescheduled(client, db, seed_org, media_dir):
    """회귀 — 문항 시점 이동으로 예약이 해제되면 다음 하트비트가 다시 잡는다.

    예전에는 next_checkpoint_sec=None을 되돌릴 경로가 없어, 예약이 해제되는 순간 그
    강의를 보던 학생은 남은 내내 캡차가 한 번도 안 떴다 = 시청 검증이 조용히 꺼졌다.
    주석은 '다음 하트비트가 다시 잡는다'고 적혀 있었지만 그런 코드가 없었다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, duration=1000).json()
    q = _add_question(client, ops_tok, lec["id"], position=300)
    tok = _student_token(client, seed_org)
    d = client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    assert d.json()["next_checkpoint_sec"] == 300

    # 강사가 문항 시점을 40초로 이동 — 300초 예약은 어떤 핀 구간도 안 덮어 해제된다
    u = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}",
        json={"position_sec": 40},
        headers=auth(ops_tok),
    )
    assert u.status_code == 200, u.text

    st = _session_token(client, tok, lec["id"])
    hb = _hb(client, tok, lec["id"], 4, st=st)
    assert hb.json()["next_checkpoint_sec"] == 40, "해제된 예약이 다시 안 잡혔다"


# ================================================================ 자료실(강의 자료)
def _upload_material(
    client, ops_tok, lecture_id, *, title="수업 자료", filename="자료.pdf",
    size=1024, content_type="application/pdf", order_no=None,
):
    data = {"title": title}
    if order_no is not None:
        data["order_no"] = str(order_no)
    return client.post(
        f"/api/v1/ops/lectures/{lecture_id}/materials",
        data=data,
        files={"file": (filename, b"\x01" * size, content_type)},
        headers=auth(ops_tok),
    )


def _add_link_material(client, ops_tok, lecture_id, *, title="참고 링크",
                       url="https://example.com/doc", **extra):
    return client.post(
        f"/api/v1/ops/lectures/{lecture_id}/materials",
        json={"title": title, "url": url, **extra},
        headers=auth(ops_tok),
    )


def test_material_link_create_and_student_visible(client, db, seed_org, media_dir):
    """link 자료 — JSON 생성 → 학생 상세 materials에 외부 URL 그대로 노출 + audit."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()

    r = _add_link_material(client, ops_tok, lec["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "link" and body["url"] == "https://example.com/doc"
    assert body["order_no"] == 1  # 미지정 → 맨 뒤 자동 배정(max 0 + 1)

    tok = _student_token(client, seed_org)
    detail = client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok)).json()
    mats = detail["materials"]
    assert len(mats) == 1
    assert mats[0]["kind"] == "link" and mats[0]["url"] == "https://example.com/doc"

    # http(s) 아닌 스킴 거절 — javascript: 링크가 학생 화면으로 흘러가면 안 된다
    bad = _add_link_material(client, ops_tok, lec["id"], url="javascript:alert(1)")
    assert bad.status_code == 400
    # 제목 없는 링크 거절
    empty = _add_link_material(client, ops_tok, lec["id"], title="  ")
    assert empty.status_code == 400

    assert (
        db.query(AuditLog).filter(AuditLog.action == "lecture.material.create").count() == 1
    )


def test_material_file_upload_and_download(client, db, seed_org, media_dir):
    """file 자료 — multipart 업로드(청크 복사·원자 이동) → 학생 다운로드 200 + attachment.

    학생 응답에 내부 경로·url 원본은 없고 download_url(엔드포인트 경로)만 노출된다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()

    r = _upload_material(client, ops_tok, lec["id"], size=2048)
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["kind"] == "file" and m["file_ext"] == ".pdf" and m["file_bytes"] == 2048
    assert (media_dir / "materials" / f"{m['id']}.pdf").is_file()
    assert not list((media_dir / "materials").glob(".upload-*")), "임시파일이 남았다"

    tok = _student_token(client, seed_org)
    detail = client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok)).json()
    item = detail["materials"][0]
    assert "url" not in item, "file 자료의 학생 응답에 경로 원문이 노출됐다"
    dl_url = item["download_url"]
    assert dl_url == f"/api/v1/lectures/{lec['id']}/materials/{m['id']}/download"

    # 학생 인증 없이는 다운로드 불가
    assert client.get(dl_url).status_code in (401, 403)

    dl = client.get(dl_url, headers=auth(tok))
    assert dl.status_code == 200, dl.text
    assert "attachment" in dl.headers.get("content-disposition", "")
    assert dl.headers["content-type"].startswith("application/octet-stream")
    assert dl.content == b"\x01" * 2048


def test_material_rejects_executable_and_web_exts(client, db, seed_org, media_dir):
    """실행파일·웹문서 확장자 거절(화이트리스트 밖) — 거절된 업로드는 파일을 남기지 않는다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    for bad in ("malware.exe", "run.bat", "s.sh", "x.js", "p.html", "v.svg", "noext"):
        r = _upload_material(client, ops_tok, lec["id"], filename=bad)
        assert r.status_code == 400, f"{bad}: {r.status_code}"
    mdir = media_dir / "materials"
    assert not mdir.exists() or list(mdir.iterdir()) == []
    assert db.query(LectureMaterial).count() == 0


def test_material_upload_size_exception_and_other_paths_413(
    client, db, seed_org, media_dir, monkeypatch
):
    """자료 업로드 예외는 'POST materials + multipart'만 — 같은 경로 JSON·타 경로는 1MB 413."""
    from app.core.config import get_settings

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()

    # 2MB multipart 자료 → 전역 1MB를 넘지만 자료 예외(50MB)로 통과·정상 저장
    ok = _upload_material(client, ops_tok, lec["id"], size=2 * 1024 * 1024)
    assert ok.status_code == 200, ok.text
    assert ok.json()["file_bytes"] == 2 * 1024 * 1024

    # 자료 상한 초과 multipart → 413 (상한을 낮춰 실측 — 미들웨어가 자료 상한을 실제로 본다)
    monkeypatch.setattr(get_settings(), "MAX_MATERIAL_UPLOAD_BYTES", 10_000)
    over = _upload_material(client, ops_tok, lec["id"], size=20_000)
    assert over.status_code == 413

    # 같은 경로라도 JSON(비 multipart) 대용량 본문은 1MB에서 413 — 예외는 multipart 한정
    big_json = client.post(
        f"/api/v1/ops/lectures/{lec['id']}/materials",
        content=b"x" * 1_100_000,
        headers={"Content-Type": "application/json", **auth(ops_tok)},
    )
    assert big_json.status_code == 413

    # 전혀 다른 경로는 여전히 413
    other = client.post(
        "/api/v1/auth/login",
        content=b"x" * 1_100_000,
        headers={"Content-Type": "application/json"},
    )
    assert other.status_code == 413


def test_material_update_soft_delete_and_order(client, db, seed_org, media_dir):
    """메타 수정(title·order_no)·order_no 자동 증가·소프트 삭제 후 목록/상세/다운로드 제외."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    m1 = _upload_material(client, ops_tok, lec["id"], title="첫 자료").json()
    m2 = _add_link_material(client, ops_tok, lec["id"], title="둘째 링크").json()
    assert (m1["order_no"], m2["order_no"]) == (1, 2)  # 자동 배정 순차 증가

    up = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/materials/{m1['id']}",
        json={"title": "수정된 자료", "order_no": 5},
        headers=auth(ops_tok),
    )
    assert up.status_code == 200
    assert up.json()["title"] == "수정된 자료" and up.json()["order_no"] == 5

    # ops 목록 — order_no 오름차순으로 재배열됨(m2=2 < m1=5)
    ls = client.get(f"/api/v1/ops/lectures/{lec['id']}/materials", headers=auth(ops_tok)).json()
    assert [x["id"] for x in ls] == [m2["id"], m1["id"]]

    # 소프트 삭제 → ops 목록·학생 상세 제외, 행은 보존, 'file 종류는 파일 물리 삭제', 다운로드 404
    assert (media_dir / "materials" / f"{m1['id']}.pdf").is_file()  # 삭제 전엔 존재
    rm = client.delete(
        f"/api/v1/ops/lectures/{lec['id']}/materials/{m1['id']}", headers=auth(ops_tok)
    )
    assert rm.status_code == 200
    ls2 = client.get(f"/api/v1/ops/lectures/{lec['id']}/materials", headers=auth(ops_tok)).json()
    assert [x["id"] for x in ls2] == [m2["id"]]
    tok = _student_token(client, seed_org)
    detail = client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok)).json()
    assert [x["id"] for x in detail["materials"]] == [m2["id"]]
    row = db.get(LectureMaterial, m1["id"])
    db.refresh(row)
    assert row.status == "deleted"  # 레코드는 보존(소프트)
    assert not (media_dir / "materials" / f"{m1['id']}.pdf").exists(), "자료 파일이 물리 삭제되지 않았다"
    dl = client.get(
        f"/api/v1/lectures/{lec['id']}/materials/{m1['id']}/download", headers=auth(tok)
    )
    assert dl.status_code == 404

    # link 자료 삭제 — 지울 파일이 없어도 예외 없이 통과
    rm2 = client.delete(
        f"/api/v1/ops/lectures/{lec['id']}/materials/{m2['id']}", headers=auth(ops_tok)
    )
    assert rm2.status_code == 200
    assert db.get(LectureMaterial, m2["id"]).status == "deleted"

    for action in ("lecture.material.update", "lecture.material.delete"):
        assert db.query(AuditLog).filter(AuditLog.action == action).count() >= 1


def test_lecture_delete_cascades_materials(client, db, seed_org, media_dir):
    """강의 소프트 삭제 시 그 강의의 자료도 함께 소프트 삭제 + file 파일 물리 삭제.

    (skeptic REFUTED 회귀 방지: 이걸 안 하면 부모 강의 삭제 후 자료 CRUD가 전부 404가
    되어 자료 파일이 영구 고아로 남는다.)"""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    mf = _upload_material(client, ops_tok, lec["id"], title="파일 자료").json()
    ml = _add_link_material(client, ops_tok, lec["id"], title="링크 자료").json()
    fpath = media_dir / "materials" / f"{mf['id']}.pdf"
    assert fpath.is_file()

    rm = client.delete(f"/api/v1/ops/lectures/{lec['id']}", headers=auth(ops_tok))
    assert rm.status_code == 200

    # 자료 행은 보존되되 status=deleted, file 자료 파일은 디스크에서 사라진다
    for mid in (mf["id"], ml["id"]):
        row = db.get(LectureMaterial, mid)
        db.refresh(row)
        assert row.status == "deleted", f"{mid} 자료가 함께 삭제되지 않았다"
    assert not fpath.exists(), "자료 파일이 고아로 남았다"

    # 감사 로그에 자료 삭제 개수 기록
    log = (
        db.query(AuditLog)
        .filter(AuditLog.action == "lecture.delete")
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert log.after_json["materials_deleted"] == 2


def test_material_broken_multipart_is_400_not_500(client, db, seed_org, media_dir):
    """boundary는 선언됐는데 본문이 깨진 multipart — 500이 아니라 400으로 정직하게 거절.

    (skeptic REFUTED 회귀 방지: 손상·잘린 업로드나 프록시가 헤더를 건드린 정상 업로드가
    500으로 떨어지면 안 된다.)"""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    r = client.post(
        f"/api/v1/ops/lectures/{lec['id']}/materials",
        content=b"--xyz\r\nnot really multipart\r\n",
        headers={
            "Content-Type": "multipart/form-data; boundary=xyz",
            **auth(ops_tok),
        },
    )
    assert r.status_code == 400, r.status_code


def test_material_ops_only(client, db, seed_org, media_dir):
    """학생 토큰으로 자료 생성/수정/삭제 불가 — 운영자 전용."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    m = _add_link_material(client, ops_tok, lec["id"]).json()
    tok = _student_token(client, seed_org)

    assert _add_link_material(client, tok, lec["id"]).status_code == 403
    assert (
        client.put(
            f"/api/v1/ops/lectures/{lec['id']}/materials/{m['id']}",
            json={"title": "탈취"},
            headers=auth(tok),
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/ops/lectures/{lec['id']}/materials/{m['id']}", headers=auth(tok)
        ).status_code
        == 403
    )


# ================================================================ 강의 목차 순서
def test_lecture_toc_order(client, db, seed_org, media_dir):
    """order_no 3,1,2로 만들어도 학생 목록·상세 toc·ops 목록이 1,2,3 순으로 나온다."""
    ops_tok = _ops(client, db)
    _upload_lecture(client, ops_tok, title="3강", order_no=3)
    _upload_lecture(client, ops_tok, title="1강", order_no=1)
    l2 = _upload_lecture(client, ops_tok, title="2강", order_no=2).json()
    _upload_lecture(client, ops_tok, title="수학1강", subject="수학", order_no=1)

    tok = _student_token(client, seed_org)
    rows = client.get("/api/v1/lectures?subject=국어", headers=auth(tok)).json()
    assert [r["title"] for r in rows] == ["1강", "2강", "3강"]

    # 상세 toc — 같은 과목만, order_no 오름차순, 내 진행 포함
    detail = client.get(f"/api/v1/lectures/{l2['id']}", headers=auth(tok)).json()
    assert [t["title"] for t in detail["toc"]] == ["1강", "2강", "3강"]
    assert all("progress" in t and "order_no" in t for t in detail["toc"])
    # l2는 상세 진입으로 진행 행이 생겼다 — toc에도 반영
    mine = [t for t in detail["toc"] if t["id"] == l2["id"]][0]
    assert mine["progress"] is not None

    # ops 목록도 과목별 목차순
    ops_ls = client.get("/api/v1/ops/lectures", headers=auth(ops_tok)).json()
    korean = [r["title"] for r in ops_ls if r["subject"] == "국어"]
    assert korean == ["1강", "2강", "3강"]


def test_ops_list_question_counts(client, db, seed_org, media_dir):
    """ops 목록의 active_question_count — '검증 없음' 콘솔 경고의 근거.

    0이면 확인(캡차)이 아예 안 떠서 그 강의는 시청 검증이 없다. draft는 안 잡히고,
    승인해야 잡힌다. (풀 개념 제거로 pool_question_count 필드는 사라졌다 — 회귀 방지.)"""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()

    def row():
        ls = client.get("/api/v1/ops/lectures", headers=auth(ops_tok)).json()
        return next(r for r in ls if r["id"] == lec["id"])

    # 문항 0개 — 검증 없음 상태
    r = row()
    assert r["active_question_count"] == 0
    assert "pool_question_count" not in r  # 풀 개념 부활 회귀 방지
    assert "check_min_sec" not in r and "check_max_sec" not in r  # 간격 설정도 함께 제거됨

    # draft 문항 — 아직 공개가 아니라 안 잡힌다
    draft_q = _add_question(client, ops_tok, lec["id"], position=0, status="draft")
    r = row()
    assert r["active_question_count"] == 0 and r["question_count"] == 1

    # 시점 지정 + 승인 → 잡힌다
    up = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{draft_q['id']}",
        json={"status": "active", "position_sec": 18},
        headers=auth(ops_tok),
    )
    assert up.status_code == 200, up.text
    r = row()
    assert r["active_question_count"] == 1

    # 삭제 → 검증 없음 상태로 복귀
    rm = client.delete(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{draft_q['id']}",
        headers=auth(ops_tok),
    )
    assert rm.status_code == 200, rm.text
    r = row()
    assert r["active_question_count"] == 0


def test_lecture_order_no_auto_assign_and_reorder(client, db, seed_org, media_dir):
    """생성 시 미지정 → 과목 내 max+1 자동 배정, PUT order_no 재배열이 목록에 반영."""
    ops_tok = _ops(client, db)
    a = _upload_lecture(client, ops_tok, title="가").json()
    b = _upload_lecture(client, ops_tok, title="나").json()
    assert (a["order_no"], b["order_no"]) == (1, 2)

    up = client.put(
        f"/api/v1/ops/lectures/{b['id']}", json={"order_no": 0}, headers=auth(ops_tok)
    )
    assert up.status_code == 200 and up.json()["order_no"] == 0
    bad = client.put(
        f"/api/v1/ops/lectures/{b['id']}", json={"order_no": -1}, headers=auth(ops_tok)
    )
    assert bad.status_code == 400

    tok = _student_token(client, seed_org)
    rows = client.get("/api/v1/lectures", headers=auth(tok)).json()
    assert [r["title"] for r in rows] == ["나", "가"]


# ================================================================ 문항 이미지(화면 캡처 보기)
def _attach_image(
    client, ops_tok, lecture_id, question_id, *, slot="prompt", option_index=None,
    filename="캡처.png", size=4096, content_type="image/png",
):
    data = {"slot": slot}
    if option_index is not None:
        data["option_index"] = str(option_index)
    return client.post(
        f"/api/v1/ops/lectures/{lecture_id}/questions/{question_id}/images",
        data=data,
        files={"file": (filename, b"\x02" * size, content_type)},
        headers=auth(ops_tok),
    )


def _image_id_from_url(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def test_question_image_attach_serve_and_replace(client, db, seed_org, media_dir):
    """프롬프트·보기 이미지 첨부 → 서빙 200(인라인·무인증) + 교체 시 옛 파일 물리 삭제."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    q = _add_question(client, ops_tok, lec["id"])
    qdir = media_dir / "questions"

    # 프롬프트 이미지 첨부 — 파일이 최종 경로에 존재, 임시파일 없음
    r = _attach_image(client, ops_tok, lec["id"], q["id"], slot="prompt")
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["prompt_image_url"]
    assert row["option_image_urls"] == [None, None, None, None]
    img1 = _image_id_from_url(row["prompt_image_url"])
    assert (qdir / f"{img1}.png").is_file()
    assert not list(qdir.glob(".upload-*")), "임시파일이 남았다"

    # 보기 1번 이미지 첨부 — 해당 인덱스만 채워진다
    r2 = _attach_image(client, ops_tok, lec["id"], q["id"], slot="option", option_index=1)
    assert r2.status_code == 200, r2.text
    urls = r2.json()["option_image_urls"]
    assert urls[1] and urls[0] is None and urls[2] is None and urls[3] is None

    # 서빙 — <img>는 Authorization을 못 실으므로 무인증 200 + 인라인 이미지 타입
    sv = client.get(row["prompt_image_url"])
    assert sv.status_code == 200, sv.text
    assert sv.headers["content-type"].startswith("image/png")
    assert sv.content == b"\x02" * 4096

    # 같은 슬롯 재첨부 = 교체 — 새 id 발급, 옛 파일은 물리 삭제
    r3 = _attach_image(client, ops_tok, lec["id"], q["id"], slot="prompt", filename="새캡처.webp",
                       content_type="image/webp")
    assert r3.status_code == 200, r3.text
    img2 = _image_id_from_url(r3.json()["prompt_image_url"])
    assert img2 != img1
    assert (qdir / f"{img2}.webp").is_file()
    assert not (qdir / f"{img1}.png").exists(), "교체된 옛 이미지가 남았다"
    # 교체 후 옛 URL은 404 (payload 참조 기준 서빙)
    assert client.get(row["prompt_image_url"]).status_code == 404

    # 감사기록 — 첨부 3회
    assert (
        db.query(AuditLog).filter(AuditLog.action == "lecture.question.image.create").count()
        == 3
    )

    # ops 문항 목록에도 이미지 URL이 실린다
    ls = client.get(f"/api/v1/ops/lectures/{lec['id']}/questions", headers=auth(ops_tok)).json()
    assert ls[0]["prompt_image_url"].endswith(img2)


def test_question_image_rejects_executable_svg_and_bad_slot(client, db, seed_org, media_dir):
    """실행파일·SVG·비이미지 Content-Type 거절 + 슬롯 지정 오류 400 — 파일을 남기지 않는다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    q = _add_question(client, ops_tok, lec["id"])

    for bad in ("x.exe", "s.svg", "p.html", "r.bat", "noext"):
        r = _attach_image(client, ops_tok, lec["id"], q["id"], filename=bad)
        assert r.status_code == 400, f"{bad}: {r.status_code}"
    ct = _attach_image(client, ops_tok, lec["id"], q["id"], content_type="application/octet-stream")
    assert ct.status_code == 400
    bad_slot = _attach_image(client, ops_tok, lec["id"], q["id"], slot="banner")
    assert bad_slot.status_code == 400
    out_of_range = _attach_image(client, ops_tok, lec["id"], q["id"], slot="option", option_index=9)
    assert out_of_range.status_code == 400
    no_index = _attach_image(client, ops_tok, lec["id"], q["id"], slot="option")
    assert no_index.status_code == 400

    qdir = media_dir / "questions"
    assert not qdir.exists() or list(qdir.iterdir()) == []
    # 문항 payload도 오염되지 않았다
    row = db.get(LectureQuestion, q["id"])
    assert "prompt_image" not in (row.payload or {})
    assert "option_images" not in (row.payload or {})


def test_question_image_replace_failure_leaves_no_tmp(client, db, seed_org, media_dir, monkeypatch):
    """os.replace 실패(디스크 풀·잠금) — 임시파일을 남기지 않고 payload도 오염되지 않는다."""
    import app.api.v1.endpoints.lectures as lectures_mod

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    q = _add_question(client, ops_tok, lec["id"])

    real_replace = lectures_mod.os.replace

    def boom(src, dst):
        raise OSError("disk error")

    monkeypatch.setattr(lectures_mod.os, "replace", boom)
    with pytest.raises(OSError):
        _attach_image(client, ops_tok, lec["id"], q["id"], slot="prompt")
    monkeypatch.setattr(lectures_mod.os, "replace", real_replace)

    qdir = media_dir / "questions"
    assert not qdir.exists() or not list(qdir.iterdir()), "replace 실패 후 임시파일이 남았다"
    row = db.get(LectureQuestion, q["id"])
    assert "prompt_image" not in (row.payload or {})


def test_question_image_delete_and_question_delete_cascade(client, db, seed_org, media_dir):
    """이미지 제거·문항 삭제 시 파일 물리 삭제 + 레코드(payload 참조 포함) 보존."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    q = _add_question(client, ops_tok, lec["id"])
    qdir = media_dir / "questions"

    pi = _attach_image(client, ops_tok, lec["id"], q["id"], slot="prompt").json()
    oi = _attach_image(client, ops_tok, lec["id"], q["id"], slot="option", option_index=0).json()
    prompt_img = _image_id_from_url(pi["prompt_image_url"])
    opt_img = _image_id_from_url(oi["option_image_urls"][0])

    # 보기 이미지 제거(DELETE) — 참조·파일 모두 정리, 서빙 404
    rm = client.delete(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}/images",
        params={"slot": "option", "option_index": 0},
        headers=auth(ops_tok),
    )
    assert rm.status_code == 200, rm.text
    assert rm.json()["option_image_urls"][0] is None
    assert not (qdir / f"{opt_img}.png").exists()
    assert client.get(oi["option_image_urls"][0]).status_code == 404
    assert (
        db.query(AuditLog).filter(AuditLog.action == "lecture.question.image.delete").count()
        == 1
    )
    # 없는 슬롯 재삭제 → 404
    rm2 = client.delete(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}/images",
        params={"slot": "option", "option_index": 0},
        headers=auth(ops_tok),
    )
    assert rm2.status_code == 404

    # 문항 삭제 — 남은 프롬프트 이미지 파일 물리 삭제, 행·payload 참조는 이력으로 보존
    assert (qdir / f"{prompt_img}.png").is_file()
    dq = client.delete(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}", headers=auth(ops_tok)
    )
    assert dq.status_code == 200
    assert not (qdir / f"{prompt_img}.png").exists(), "문항 삭제가 이미지를 정리하지 않았다"
    row = db.get(LectureQuestion, q["id"])
    db.refresh(row)
    assert row.status == "deleted"
    assert (row.payload or {}).get("prompt_image", {}).get("id") == prompt_img  # 이력 보존
    # deleted 문항의 이미지는 서빙도 닫힌다
    assert client.get(pi["prompt_image_url"]).status_code == 404


def test_lecture_delete_cascades_question_images(client, db, seed_org, media_dir):
    """강의 삭제 — 문항 이미지 파일도 연쇄 물리 삭제(고아 파일 방지), 문항 행은 보존."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    q = _add_question(client, ops_tok, lec["id"])
    pi = _attach_image(client, ops_tok, lec["id"], q["id"], slot="prompt").json()
    img = _image_id_from_url(pi["prompt_image_url"])
    qdir = media_dir / "questions"
    assert (qdir / f"{img}.png").is_file()

    rm = client.delete(f"/api/v1/ops/lectures/{lec['id']}", headers=auth(ops_tok))
    assert rm.status_code == 200
    assert not (qdir / f"{img}.png").exists(), "강의 삭제가 문항 이미지를 정리하지 않았다"
    assert db.get(LectureQuestion, q["id"]).status != "deleted"  # 문항 행은 보존(강의만 deleted)
    assert client.get(pi["prompt_image_url"]).status_code == 404  # deleted 강의 — 서빙 차단


def test_question_image_size_exception_and_other_paths_413(
    client, db, seed_org, media_dir, monkeypatch
):
    """이미지 업로드 예외는 'POST images + multipart'만 — 상한 초과·JSON·타 경로는 413."""
    from app.core.config import get_settings

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    q = _add_question(client, ops_tok, lec["id"])

    # 2MB multipart 이미지 → 전역 1MB를 넘지만 이미지 예외(5MB)로 통과·정상 저장
    ok = _attach_image(client, ops_tok, lec["id"], q["id"], size=2 * 1024 * 1024)
    assert ok.status_code == 200, ok.text

    # 이미지 상한 초과 multipart → 413 (상한을 낮춰 실측 — 미들웨어가 이미지 상한을 실제로 본다)
    monkeypatch.setattr(get_settings(), "MAX_QUESTION_IMAGE_BYTES", 10_000)
    over = _attach_image(client, ops_tok, lec["id"], q["id"], size=20_000)
    assert over.status_code == 413

    # 같은 경로라도 JSON(비 multipart) 대용량 본문은 1MB에서 413 — 예외는 multipart 한정
    big_json = client.post(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}/images",
        content=b"x" * 1_100_000,
        headers={"Content-Type": "application/json", **auth(ops_tok)},
    )
    assert big_json.status_code == 413

    # 전혀 다른 경로는 여전히 413
    other = client.post(
        "/api/v1/auth/login",
        content=b"x" * 1_100_000,
        headers={"Content-Type": "application/json"},
    )
    assert other.status_code == 413


def test_text_only_question_challenge_backward_compat(client, db, seed_org, media_dir):
    """기존 텍스트 전용 문항 — 챌린지 페이로드가 종전과 동일(이미지 키 자체가 없다)."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    _add_question(client, ops_tok, lec["id"])
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    _reach_checkpoint(client, tok, lec["id"])

    ch = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert ch.status_code == 200, ch.text
    body = ch.json()
    assert "prompt_image" not in body
    assert all("image" not in o for o in body["options"])
    # ops 문항 목록의 이미지 필드도 빈 상태로 하위호환(None/None 리스트)
    row = client.get(f"/api/v1/ops/lectures/{lec['id']}/questions", headers=auth(ops_tok)).json()[0]
    assert row["prompt_image_url"] is None
    assert row["option_image_urls"] == [None, None, None, None]


def test_challenge_with_images_serves_and_never_leaks_answer(client, db, seed_org, media_dir):
    """이미지 문항 챌린지 — prompt_image·보기 image URL 전달, 정답 신호는 어디에도 없다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    q = _add_question(client, ops_tok, lec["id"], answer=2)
    _attach_image(client, ops_tok, lec["id"], q["id"], slot="prompt")
    _attach_image(client, ops_tok, lec["id"], q["id"], slot="option", option_index=0)
    _attach_image(client, ops_tok, lec["id"], q["id"], slot="option", option_index=2)
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    _reach_checkpoint(client, tok, lec["id"])

    ch = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert ch.status_code == 200, ch.text
    body = ch.json()
    assert body["prompt_image"].startswith(f"/api/v1/lectures/{lec['id']}/questions/")
    assert "image" in body["options"][0] and "image" in body["options"][2]
    assert "image" not in body["options"][1] and "image" not in body["options"][3]
    # 정답 미노출 — 응답 키 어디에도 정오 신호가 없다(이미지 URL은 모든 보기가 같은 형태,
    # 정답은 Fernet 암호화 토큰 안에만 존재해 클라이언트가 복호화할 수 없다)
    assert "answer" not in body and "answer_index" not in body
    assert all(set(o) <= {"id", "text", "image"} for o in body["options"])

    # 학생(무인증 <img>)이 챌린지의 이미지 URL을 그대로 로드할 수 있다
    assert client.get(body["prompt_image"]).status_code == 200
    assert client.get(body["options"][0]["image"]).status_code == 200

    # 정답 제출은 종전과 동일하게 동작(이미지 확장이 채점 경로를 건드리지 않는다)
    vr = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": body["challenge_token"], "answer": ["2"]},
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert vr.status_code == 200 and vr.json()["success"] is True


def test_option_shrink_cleans_image_files_and_empty_text_rules(client, db, seed_org, media_dir):
    """보기 축소 시 범위 밖 이미지 정리 + 이미지 있는 보기만 빈 텍스트 허용."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()
    q = _add_question(client, ops_tok, lec["id"])  # 보기 4개
    qdir = media_dir / "questions"

    r = _attach_image(client, ops_tok, lec["id"], q["id"], slot="option", option_index=3).json()
    img = _image_id_from_url(r["option_image_urls"][3])
    assert (qdir / f"{img}.png").is_file()

    # 보기 4개 → 2개 축소 — 3번 보기 이미지 파일이 함께 정리된다(고아 파일 방지)
    up = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}",
        json={"options": ["가", "나"], "answer_index": 0},
        headers=auth(ops_tok),
    )
    assert up.status_code == 200, up.text
    assert up.json()["option_image_urls"] == [None, None]
    assert not (qdir / f"{img}.png").exists(), "축소로 빠진 보기의 이미지가 남았다"

    # 이미지 없는 보기의 빈 텍스트 → 400 (종전 규칙 유지)
    bad = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}",
        json={"options": ["가", "  "], "answer_index": 0},
        headers=auth(ops_tok),
    )
    assert bad.status_code == 400

    # 이미지가 붙은 보기는 텍스트를 비울 수 있다(그림 보기 문항) —
    r2 = _attach_image(client, ops_tok, lec["id"], q["id"], slot="option", option_index=1)
    assert r2.status_code == 200
    ok = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}",
        json={"options": ["가", ""], "answer_index": 0},
        headers=auth(ops_tok),
    )
    assert ok.status_code == 200, ok.text
    # 빈 텍스트 보기의 이미지 삭제는 400 — 내용 없는 보기를 만들지 않는다
    rm = client.delete(
        f"/api/v1/ops/lectures/{lec['id']}/questions/{q['id']}/images",
        params={"slot": "option", "option_index": 1},
        headers=auth(ops_tok),
    )
    assert rm.status_code == 400
