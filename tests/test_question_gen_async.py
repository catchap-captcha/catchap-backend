"""AI 확인문항 생성 비동기 전환(0720) — 엔드포인트는 잡만 만들고, 러너가 백그라운드로 수행.

왜: STT+생성이 긴 영상이면 수분 걸려 강사가 동기 대기하던 걸(타임아웃 위험) 잡 상태로 비동기화.
여기선 (1) POST가 즉시 job_id 반환(pending) (2) 러너가 잡을 done으로 만들고 draft 생성
(3) 상태 폴링이 소유 스코프인지 (4) 키 없으면 잡 없이 503 — 를 고정한다.
"""
import app.clients.ai_client as ai_client
from app.api.v1.endpoints import lectures as lec_ep
from app.core.config import get_settings
from app.models import Lecture, LectureQuestion, LectureQuestionGenJob, LectureTranscript
from tests.conftest import TestSession
from tests.test_captcha_api import _instructor, auth
from tests.test_lectures import _upload_lecture


def _mock_ai(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "")  # STT 미설정
    monkeypatch.setattr(get_settings(), "LECTURE_MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr(
        ai_client, "generate_lecture_questions",
        lambda **k: [{"prompt": "q", "options": ["가", "나"], "answer_index": 0, "explain": ""}],
    )
    monkeypatch.setattr(ai_client, "verify_questions", lambda items, **k: None)


def _lec_with_transcript(client, db, tok):
    lec_id = _upload_lecture(client, tok, title="비동기 강의", subject="국어", duration=300).json()["id"]
    # 강사 자막 저장 → STT 건너뜀(OpenAI 키 없이도 생성 가능)
    db.add(LectureTranscript(
        lecture_id=lec_id, segments=[{"start": 0, "end": 1, "text": "내용"}],
        source="paste", segment_count=1,
    ))
    db.commit()
    return lec_id


def test_generate_returns_job_immediately(client, db, monkeypatch, tmp_path):
    """POST는 실제 생성을 기다리지 않고 즉시 job_id(pending)를 돌려준다."""
    _mock_ai(monkeypatch, tmp_path)
    monkeypatch.setattr(lec_ep, "_run_question_gen_job", lambda *a, **k: None)  # 배선만 검증
    tok = _instructor(client, db)
    lec_id = _lec_with_transcript(client, db, tok)

    r = client.post(f"/api/v1/ops/lectures/{lec_id}/questions/generate", json={"n": 1}, headers=auth(tok))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "pending" and body["n"] == 1
    assert db.get(LectureQuestionGenJob, body["job_id"]) is not None


def test_no_key_returns_503_without_job(client, db, monkeypatch, tmp_path):
    """LLM 키가 없으면 잡을 만들지 않고 즉시 503(즉각 피드백)."""
    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "")
    monkeypatch.setattr(get_settings(), "LECTURE_MEDIA_DIR", str(tmp_path))
    tok = _instructor(client, db)
    lec_id = _upload_lecture(client, tok, title="키없음", subject="국어", duration=300).json()["id"]

    r = client.post(f"/api/v1/ops/lectures/{lec_id}/questions/generate", json={"n": 1}, headers=auth(tok))
    assert r.status_code == 503
    assert db.query(LectureQuestionGenJob).count() == 0


def test_runner_completes_job_and_creates_drafts(client, db, monkeypatch, tmp_path):
    """러너가 잡을 done으로 만들고 draft 문항을 생성한다(자기 세션·요약 반영)."""
    _mock_ai(monkeypatch, tmp_path)
    tok = _instructor(client, db)
    lec_id = _lec_with_transcript(client, db, tok)
    job = LectureQuestionGenJob(lecture_id=lec_id, requested_by="actor", n=1, status="pending")
    db.add(job)
    db.commit()
    job_id = job.id

    # 러너를 테스트 세션 팩토리로 직접 구동(프로덕션은 SessionLocal=MySQL)
    lec_ep._run_question_gen_job(job_id, session_factory=TestSession)

    # 별도 세션으로 검증 — 러너가 다른 세션에서 커밋했으므로 신선 조회
    s = TestSession()
    try:
        done = s.get(LectureQuestionGenJob, job_id)
        assert done.status == "done"
        assert done.created_count == 1
        assert done.transcript_used is True and done.transcript_source == "paste"
        assert done.finished_at is not None
        assert s.query(LectureQuestion).filter(
            LectureQuestion.lecture_id == lec_id, LectureQuestion.status == "draft"
        ).count() == 1
    finally:
        s.close()


def test_runner_records_error_on_failure(client, db, monkeypatch, tmp_path):
    """생성이 실패하면 러너가 잡을 error로 남긴다(조용한 실패·성공 위장 금지)."""
    _mock_ai(monkeypatch, tmp_path)

    def boom(**k):
        from app.clients.ai_client import AiGenerationError
        raise AiGenerationError("LLM 응답 파싱 실패")

    monkeypatch.setattr(ai_client, "generate_lecture_questions", boom)
    tok = _instructor(client, db)
    lec_id = _lec_with_transcript(client, db, tok)
    job = LectureQuestionGenJob(lecture_id=lec_id, requested_by="actor", n=1, status="pending")
    db.add(job)
    db.commit()
    job_id = job.id

    lec_ep._run_question_gen_job(job_id, session_factory=TestSession)

    s = TestSession()
    try:
        failed = s.get(LectureQuestionGenJob, job_id)
        assert failed.status == "error"
        assert "파싱" in (failed.error_detail or "")
        assert s.query(LectureQuestion).filter(LectureQuestion.lecture_id == lec_id).count() == 0
    finally:
        s.close()


def test_runner_notifies_instructor_on_done(client, db, monkeypatch, tmp_path):
    """생성 완료 시 요청 강사에게 인앱 알림 + 이메일(dev=dry-run·EmailLog 기록)이 간다.
    (사용자 요청 0722: 강사가 생성 걸어두고 떠나도 완료를 알림·메일로 받게.)"""
    from app.models import EmailLog, Notification, User

    _mock_ai(monkeypatch, tmp_path)
    tok = _instructor(client, db, email="notify-done@t.dev")
    inst = db.query(User).filter(User.email == "notify-done@t.dev").first()
    lec_id = _lec_with_transcript(client, db, tok)
    job = LectureQuestionGenJob(lecture_id=lec_id, requested_by=inst.id, n=1, status="pending")
    db.add(job)
    db.commit()
    job_id = job.id

    lec_ep._run_question_gen_job(job_id, session_factory=TestSession)

    s = TestSession()
    try:
        notes = (
            s.query(Notification)
            .filter(Notification.user_id == inst.id, Notification.type == "lecture_gen")
            .all()
        )
        assert len(notes) == 1
        assert "완료" in notes[0].title and notes[0].read_at is None
        # 이메일도 시도됨(개발=dry-run) → EmailLog 기록
        assert s.query(EmailLog).filter(EmailLog.to_email == "notify-done@t.dev").count() >= 1
    finally:
        s.close()


def test_runner_notifies_instructor_on_error(client, db, monkeypatch, tmp_path):
    """생성 실패 시에도 요청 강사에게 실패 알림이 간다(조용한 실패 금지)."""
    from app.models import Notification, User

    _mock_ai(monkeypatch, tmp_path)

    def boom(**k):
        from app.clients.ai_client import AiGenerationError

        raise AiGenerationError("LLM 파싱 실패")

    monkeypatch.setattr(ai_client, "generate_lecture_questions", boom)
    tok = _instructor(client, db, email="notify-fail@t.dev")
    inst = db.query(User).filter(User.email == "notify-fail@t.dev").first()
    lec_id = _lec_with_transcript(client, db, tok)
    job = LectureQuestionGenJob(lecture_id=lec_id, requested_by=inst.id, n=1, status="pending")
    db.add(job)
    db.commit()
    job_id = job.id

    lec_ep._run_question_gen_job(job_id, session_factory=TestSession)

    s = TestSession()
    try:
        note = (
            s.query(Notification)
            .filter(Notification.user_id == inst.id, Notification.type == "lecture_gen")
            .first()
        )
        assert note is not None and "실패" in note.title
    finally:
        s.close()


def test_gen_job_status_scoped_to_owner(client, db, monkeypatch, tmp_path):
    """생성 잡 상태 폴링은 소유 강사만 — 남의 강의 잡은 404(존재 미노출)."""
    _mock_ai(monkeypatch, tmp_path)
    tok = _instructor(client, db)
    lec_id = _lec_with_transcript(client, db, tok)
    job = LectureQuestionGenJob(lecture_id=lec_id, requested_by="actor", n=1, status="pending")
    db.add(job)
    db.commit()

    # 소유 강사 — 200
    r = client.get(f"/api/v1/ops/lectures/{lec_id}/questions/gen-jobs/{job.id}", headers=auth(tok))
    assert r.status_code == 200 and r.json()["status"] == "pending"

    # 다른 강사 — 남의 강의라 404
    other = _instructor(client, db, email="other-gen@t.dev")
    r2 = client.get(f"/api/v1/ops/lectures/{lec_id}/questions/gen-jobs/{job.id}", headers=auth(other))
    assert r2.status_code == 404


def test_phase_hooks_report_steps(client, db, monkeypatch, tmp_path):
    """생성 세부 단계(자막 변환→문항 생성→검증)가 on_phase 콜백으로 순서대로 보고된다."""
    import app.clients.stt_client as stt

    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-stt")  # STT 유발(자막 없음)
    monkeypatch.setattr(get_settings(), "LECTURE_MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr(stt, "transcribe_video", lambda path, *, api_key: [{"start": 0, "end": 1, "text": "t"}])
    monkeypatch.setattr(
        ai_client, "generate_lecture_questions",
        lambda **k: [{"prompt": "q", "options": ["가", "나"], "answer_index": 0, "explain": ""}],
    )
    monkeypatch.setattr(ai_client, "verify_questions", lambda items, **k: None)
    tok = _instructor(client, db)
    lec_id = _upload_lecture(client, tok, title="단계 강의", subject="국어", duration=300).json()["id"]

    from app.api.v1.endpoints.lectures import _generate_questions_now

    phases = []
    _generate_questions_now(db, db.get(Lecture, lec_id), 1, "actor", on_phase=phases.append)
    assert phases == ["transcribing", "generating", "verifying"]
