"""기동 시 고아 잡 스위퍼(lecture_service.sweep_stuck_gen_jobs) 테스트.

생성 잡은 프로세스 내 BackgroundTasks로 돌아, 재배포·크래시 순간 'running'이던 잡이
DB에 유령으로 남는다. 스위퍼는 '오래 멈춘'(updated_at이 임계값보다 오래된) pending/running
잡만 error로 마감하고, 방금 갱신된 정상 잡과 이미 끝난 잡은 건드리지 않는다.
"""

from datetime import timedelta

from app.db.base import _now
from app.models import LectureQuestionGenJob
from app.services.lecture_service import STUCK_GEN_JOB_MINUTES, sweep_stuck_gen_jobs


def _job(db, status, *, age_minutes):
    """updated_at을 age_minutes 전으로 고정한 잡 하나 생성.

    INSERT 시 default=_now 대신 명시값이 쓰이고, onupdate는 UPDATE에서만 발동하므로
    add+commit(INSERT) 후에도 지정한 오래된 updated_at이 유지된다."""
    ts = _now() - timedelta(minutes=age_minutes)
    job = LectureQuestionGenJob(
        lecture_id="lec-x", requested_by="actor", n=1,
        status=status, created_at=ts, updated_at=ts,
    )
    db.add(job)
    db.commit()
    return job.id


def test_sweeps_stuck_running_and_pending(db):
    stale = STUCK_GEN_JOB_MINUTES + 5
    running_id = _job(db, "running", age_minutes=stale)
    pending_id = _job(db, "pending", age_minutes=stale)

    swept = sweep_stuck_gen_jobs(db)

    assert swept == 2
    for jid in (running_id, pending_id):
        job = db.get(LectureQuestionGenJob, jid)
        assert job.status == "error"
        assert job.phase is None
        assert job.finished_at is not None
        assert "재시작" in (job.error_detail or "")


def test_leaves_fresh_running_job_untouched(db):
    """방금 갱신된 running 잡(다른 워커에서 정상 작동 중일 수 있음)은 건드리지 않는다."""
    fresh_id = _job(db, "running", age_minutes=1)

    swept = sweep_stuck_gen_jobs(db)

    assert swept == 0
    assert db.get(LectureQuestionGenJob, fresh_id).status == "running"


def test_ignores_terminal_jobs(db):
    """이미 끝난(done/error) 잡은 오래됐어도 대상이 아니다."""
    done_id = _job(db, "done", age_minutes=STUCK_GEN_JOB_MINUTES + 60)
    error_id = _job(db, "error", age_minutes=STUCK_GEN_JOB_MINUTES + 60)

    swept = sweep_stuck_gen_jobs(db)

    assert swept == 0
    assert db.get(LectureQuestionGenJob, done_id).status == "done"
    assert db.get(LectureQuestionGenJob, error_id).status == "error"


def test_returns_zero_when_nothing_stuck(db):
    assert sweep_stuck_gen_jobs(db) == 0
