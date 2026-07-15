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
    check_min=1, check_max=1, size=2 * 1024 * 1024, filename="v.mp4",
    content_type="video/mp4", order_no=None,
):
    data = {
        "title": title,
        "subject": subject,
        "duration_sec": str(duration),
        "check_min_sec": str(check_min),
        "check_max_sec": str(check_max),
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


def _add_question(client, ops_tok, lecture_id, *, position=0, answer=2, status="active"):
    r = client.post(
        f"/api/v1/ops/lectures/{lecture_id}/questions",
        json={
            "position_sec": position,
            "prompt": "강의에서 설명한 내용은?",
            "options": ["가", "나", "다", "라"],
            "answer_index": answer,
            "explain": "강의 앞부분에서 설명했어요.",
            "status": status,
        },
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
    lec = _upload_lecture(client, ops_tok, check_min=60, check_max=60).json()
    tok = _student_token(client, seed_org)

    # 상세 진입 → 진행 행 생성(첫 체크포인트 60초). 순수 조회 — 세션·stream_url 없음
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
    """체크포인트(cp)+유예(15초)를 넘어서는 진행은 캡차를 풀기 전까지 정지."""
    from app.services.lecture_service import GRACE_SEC

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, check_min=1, check_max=1).json()
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))  # cp=1 예약
    st = _session_token(client, tok, lec["id"])

    last = 0
    for _ in range(8):  # 헤드룸 5초/비트 — 클램프 없으면 40초까지 전진했을 것
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
    lec = _upload_lecture(client, ops_tok, check_min=60, check_max=60).json()
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
    _add_question(client, ops_tok, lec["id"], answer=2)
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
        json={"challenge_token": body["challenge_token"], "answer": "2"},
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
        json={"challenge_token": ch["challenge_token"], "answer": "0"},
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


def test_challenge_before_checkpoint_409(client, db, seed_org, media_dir):
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, check_min=60, check_max=60).json()
    _add_question(client, ops_tok, lec["id"])
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))  # cp=60, watched=0

    ch = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert ch.status_code == 409


def test_challenge_no_questions_clear_4xx(client, db, seed_org, media_dir):
    """문항 0개 강의 — 폴백(과목 은행) 출제 없이 명확한 4xx."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok).json()  # 문항 없음
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    _reach_checkpoint(client, tok, lec["id"])

    ch = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert ch.status_code == 409
    assert "문항이 없" in ch.json()["detail"]


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
    lec1 = _upload_lecture(client, ops_tok, check_min=60, check_max=60).json()
    lec2 = _upload_lecture(client, ops_tok, title="다른 강의", check_min=60, check_max=60).json()
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
    lec = _upload_lecture(client, ops_tok, check_min=60, check_max=60).json()
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
    lec = _upload_lecture(client, ops_tok, check_min=60, check_max=60).json()
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
    lec2 = _upload_lecture(client, ops_tok, title="둘", check_min=60, check_max=60).json()
    assert _hb(client, tok, lec2["id"], 3, st=st_a).status_code == 403


def test_dead_session_auto_reclaimed_after_ttl(client, db, seed_org, media_dir):
    """30초 무하트비트 세션은 죽은 것 — 새 세션이 409 없이 정상 진입(새로고침·크래시 오탐 방지).

    '30초 경과'는 앱이 쓴 last_heartbeat_at을 뒤로 미는 방식으로 시뮬레이션한다 —
    비교 코드 경로(claim_session의 threshold 계산)는 전부 실제로 탄다."""
    from datetime import timedelta

    from app.db.base import _now
    from app.services.lecture_service import SESSION_TTL_SEC

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, check_min=60, check_max=60).json()
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
    lec = _upload_lecture(client, ops_tok, check_min=60, check_max=60).json()
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
    lec = _upload_lecture(client, ops_tok, check_min=60, check_max=60).json()
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))

    for _ in range(RATE_TAKEOVER_PER_HOUR):
        r = client.post(f"/api/v1/lectures/{lec['id']}/takeover", headers=auth(tok))
        assert r.status_code == 200, r.text
    over = client.post(f"/api/v1/lectures/{lec['id']}/takeover", headers=auth(tok))
    assert over.status_code == 429


# ================================================================ 상호작용 면제·의심 가중
def test_interaction_exemption_with_streak_cap(client, db, seed_org, media_dir):
    """체크포인트 도달 시 interacted=true면 캡차 면제 + 재예약 — 단 연속 2회 상한,
    세 번째는 무조건 캡차. 캡차 통과가 상한(streak)을 리셋한다.

    ⚠️ interacted는 클라 자기신고(위조 가능) — 이 테스트는 '면제가 상한으로 유한하다'는
    남용 제한을 검증하는 것이지, 이 신호가 봇을 막는다는 뜻이 아니다."""
    from app.services.lecture_service import EXEMPT_STREAK_MAX

    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, check_min=1, check_max=1).json()
    _add_question(client, ops_tok, lec["id"], answer=2)
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))  # cp=1 예약
    st = _session_token(client, tok, lec["id"])

    # 면제 1·2회 — 캡차 없이 next 재예약(checkpoint_due=False)
    for i in range(EXEMPT_STREAK_MAX):
        cp = _progress_row(db, lec["id"]).next_checkpoint_sec
        r = _hb(client, tok, lec["id"], cp, st=st, interacted=True)
        body = r.json()
        assert body["exempted"] is True, body
        assert body["checkpoint_due"] is False
        assert body["next_checkpoint_sec"] > cp
        assert body["checkpoints_passed"] == 0  # 면제는 '캡차 통과'가 아니다

    # 3번째 도달 — interacted=true여도 상한 초과라 무조건 캡차
    db.expire_all()
    cp3 = _progress_row(db, lec["id"]).next_checkpoint_sec
    r3 = _hb(client, tok, lec["id"], cp3, st=st, interacted=True)
    assert r3.json()["exempted"] is False
    assert r3.json()["checkpoint_due"] is True

    # 감사 이벤트 — exempted 2건
    assert (
        db.query(LectureCheckpointEvent)
        .filter(LectureCheckpointEvent.result == "exempted")
        .count()
        == EXEMPT_STREAK_MAX
    )

    # 캡차 통과 → streak 리셋 — 다음 체크포인트는 다시 면제 가능
    ch = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    ).json()
    vr = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": ch["challenge_token"], "answer": "2"},
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert vr.status_code == 200 and vr.json()["success"] is True
    db.expire_all()
    row = _progress_row(db, lec["id"])
    assert int(row.exempt_streak or 0) == 0
    cp4 = row.next_checkpoint_sec
    r4 = _hb(client, tok, lec["id"], cp4, st=st, interacted=True)
    assert r4.json()["exempted"] is True

    # 면제 경로에서도 학습 적립은 없다(보상축 비오염)
    sid = seed_org["student"].id
    assert db.query(CoinTransaction).filter(CoinTransaction.student_id == sid).count() == 0
    assert db.query(LearningAttempt).filter(LearningAttempt.student_id == sid).count() == 0


def test_exemption_refused_at_last_gate(client, db, seed_org, media_dir):
    """마지막 체크포인트(재예약 후보 None)는 interacted=true여도 면제 불가 — 무조건 캡차.

    적대적 검토에서 실증된 구멍의 회귀 테스트: check 60~60·duration 100이면 cp=60의
    다음 후보가 120(≥100)=None이라, 여기서 면제해 주면 남은 게이트가 사라져
    interacted 스팸만으로 캡차 0회 완주가 된다."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(
        client, ops_tok, check_min=60, check_max=60, duration=100
    ).json()
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))  # cp=60
    st = _session_token(client, tok, lec["id"])

    # 정상 페이스로 cp까지 시청(suspicion 0 유지 — 좁힌 간격이 후보를 duration 안으로
    # 되돌리면 이 테스트의 전제가 무너진다)
    body = None
    pos = 4
    for _ in range(30):
        r = _hb(client, tok, lec["id"], pos, st=st, interacted=True)
        body = r.json()
        if body["checkpoint_due"] or body["exempted"]:
            break
        pos = body["watched_max_sec"] + 4
    assert body is not None and body["checkpoint_due"] is True, body
    assert body["exempted"] is False  # interacted=true였는데도 면제 거부
    assert (
        db.query(LectureCheckpointEvent)
        .filter(LectureCheckpointEvent.result == "exempted")
        .count()
        == 0
    )


def test_no_interaction_still_requires_captcha(client, db, seed_org, media_dir):
    """interacted 미신고(기본 False)면 기존과 동일 — 체크포인트 도달 즉시 캡차."""
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, check_min=1, check_max=1).json()
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))
    st = _session_token(client, tok, lec["id"])
    r = _hb(client, tok, lec["id"], 1, st=st)
    assert r.json()["checkpoint_due"] is True
    assert r.json()["exempted"] is False


def test_suspicion_narrows_interval_with_floor(client, db, seed_org, media_dir):
    """의심 누적 → 간격 축소 + 하한 준수.

    ① 단위: next_checkpoint가 suspicion으로 좁아지되 CHECKPOINT_FLOOR_SEC(또는 더 작은
       check_min) 아래로는 안 내려간다. ② API: seek 점프·tab_hidden 신고가 suspicion을
       올린다. ③ 통합: 의심 누적 학생의 캡차 통과 재예약이 실제로 좁아진다."""
    from app.services.lecture_service import CHECKPOINT_FLOOR_SEC, next_checkpoint

    # ① 단위 — 좁힘과 하한
    fake = Lecture(check_min_sec=100, check_max_sec=100, duration_sec=10_000)
    assert next_checkpoint(0, fake, suspicion=0) == 100
    assert next_checkpoint(0, fake, suspicion=3) == 25  # 100 // (1+3)
    assert next_checkpoint(0, fake, suspicion=999) == CHECKPOINT_FLOOR_SEC  # 하한
    tiny = Lecture(check_min_sec=5, check_max_sec=5, duration_sec=10_000)
    # 원래 간격이 하한보다 짧으면 그 값을 유지 — 축소가 간격을 '늘리지' 않는다
    assert next_checkpoint(0, tiny, suspicion=999) == 5

    # ② API — seek 점프(속도상한 초과 신고)와 tab_hidden이 suspicion을 누적
    ops_tok = _ops(client, db)
    lec = _upload_lecture(client, ops_tok, check_min=100, check_max=100, duration=600).json()
    _add_question(client, ops_tok, lec["id"], answer=2)
    site_key = _edu_key(client, db, seed_org, ops_tok, first_party=True)
    tok = _student_token(client, seed_org)
    client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(tok))  # cp=100
    st = _session_token(client, tok, lec["id"])

    _hb(client, tok, lec["id"], 590, st=st)  # 안 본 구간 seek 시도 → +1
    db.expire_all()
    assert int(_progress_row(db, lec["id"]).suspicion or 0) == 1
    _hb(client, tok, lec["id"], 6, st=st, tab_hidden=True)  # 탭 백그라운드 자기신고 → +1
    db.expire_all()
    assert int(_progress_row(db, lec["id"]).suspicion or 0) == 2

    # ③ 통합 — 점프를 반복하며 cp(100)까지 도달(그때마다 suspicion 누적),
    #    캡차 통과 후 재예약 간격이 100이 아니라 하한(20) 근처로 좁아졌는지 본다.
    due = False
    for _ in range(40):
        body = _hb(client, tok, lec["id"], 590, st=st).json()
        if body["checkpoint_due"]:
            due = True
            break
    assert due, "체크포인트에 도달하지 못했다"
    ch = client.post(
        f"/api/v1/captcha/v1/challenge?lecture={lec['id']}",
        headers={"X-Site-Key": site_key, **auth(tok)},
    ).json()
    vr = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": ch["challenge_token"], "answer": "2"},
        headers={"X-Site-Key": site_key, **auth(tok)},
    )
    assert vr.status_code == 200 and vr.json()["success"] is True
    db.expire_all()
    row = _progress_row(db, lec["id"])
    suspicion = int(row.suspicion or 0)
    # 반복 점프로 상한(SUSPICION_MAX=8)까지 누적 → 통과 시 반감(8//2=4). 무한 누적 금지 +
    # 캡차 통과가 회복 경로라는 두 규약을 함께 단언한다.
    from app.services.lecture_service import SUSPICION_MAX

    assert suspicion == SUSPICION_MAX // 2, f"cap·반감 규약 위반: {suspicion}"
    gap = int(row.next_checkpoint_sec) - int(row.watched_max_sec)
    # 축소: max(100//(1+suspicion), 20) ≤ gap < 원래 간격 100
    assert CHECKPOINT_FLOOR_SEC <= gap < 100, f"간격이 좁아지지 않았다: {gap}(suspicion={suspicion})"


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
