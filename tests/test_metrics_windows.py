"""지표 창 실무 규칙 — 정답률 최소 표본 폴백(월→지난달→누적) + 반 배정 이력."""

from datetime import datetime, timedelta

from app.services import aggregate


def _attempt(db, student, result, created_at):
    from app.models import LearningAttempt

    db.add(
        LearningAttempt(
            organization_id=student.organization_id, student_id=student.id,
            subject="수학", chapter_no=1, result=result, score=0,
            created_at=created_at, updated_at=created_at,
        )
    )


def test_acc_min_sample_falls_back_to_prev_month(db, seed_org):
    """이번 달 표본 < 5 → 지난달 정답률 사용(1문제로 0%↔100% 출렁임 방지)."""
    student = seed_org["student"]
    now = datetime.now()
    prev_mid = (now.replace(day=1) - timedelta(days=15))  # 지난달 중순

    # 지난달 10건 중 9 정답(90%), 이번 달 2건 중 0 정답(표본 부족)
    for i in range(10):
        _attempt(db, student, "correct" if i < 9 else "incorrect", prev_mid)
    for _ in range(2):
        _attempt(db, student, "incorrect", now)
    db.commit()

    m = aggregate.student_roster_metrics(db, [student.id])
    assert m[student.id]["acc"] == 90, "표본 부족 시 지난달로 폴백해야 한다"

    # 이번 달이 5건을 채우면 이번 달 값으로 전환 (2오답 + 3정답 = 60%)
    for _ in range(3):
        _attempt(db, student, "correct", now)
    db.commit()
    m2 = aggregate.student_roster_metrics(db, [student.id])
    assert m2[student.id]["acc"] == 60


def test_acc_min_sample_falls_back_to_all_time(db, seed_org):
    """이번 달·지난달 둘 다 표본 부족 → 누적 정답률."""
    student = seed_org["student"]
    old = datetime.now() - timedelta(days=100)
    for i in range(8):
        _attempt(db, student, "correct" if i < 6 else "incorrect", old)  # 누적 75%
    _attempt(db, student, "incorrect", datetime.now())  # 이번 달 1건뿐
    db.commit()

    m = aggregate.student_roster_metrics(db, [student.id])
    # 누적 9건 중 6 정답 = 67%
    assert m[student.id]["acc"] == 67


def test_class_assignment_history_recorded(db, seed_org):
    """배정 변경 시 이력 행이 닫히고 열린다 — 같은 반 재배정은 무시."""
    from app.models import ClassAssignment
    from app.services.onboarding_service import record_class_assignment

    student = seed_org["student"]
    cls = seed_org["class"]

    record_class_assignment(db, student, cls.id)
    db.commit()
    rows = db.query(ClassAssignment).filter(ClassAssignment.student_id == student.id).all()
    assert len(rows) == 1 and rows[0].ended_on is None

    # 같은 반 재배정 — 이력 오염 없음
    record_class_assignment(db, student, cls.id)
    db.commit()
    assert db.query(ClassAssignment).filter(ClassAssignment.student_id == student.id).count() == 1

    # 배정 해제 — 열린 행이 닫힌다
    record_class_assignment(db, student, None)
    db.commit()
    rows = db.query(ClassAssignment).filter(ClassAssignment.student_id == student.id).all()
    assert len(rows) == 1 and rows[0].ended_on is not None

    # 재배정 — 새 열린 행
    record_class_assignment(db, student, cls.id)
    db.commit()
    open_rows = (
        db.query(ClassAssignment)
        .filter(ClassAssignment.student_id == student.id, ClassAssignment.ended_on.is_(None))
        .all()
    )
    assert len(open_rows) == 1


def test_year_time_cut_at_assignment_start(db, seed_org):
    """장기 학습시간은 현재 반 배정 시작일 이후만 — 이전 반 시간이 섞이지 않는다."""
    from app.models import ClassAssignment
    from app.services.onboarding_service import record_class_assignment

    student = seed_org["student"]
    cls = seed_org["class"]
    now = datetime.now()

    # 배정 40일 전(다른 반 시절) 10분 + 배정 후 5분
    before = now - timedelta(days=40)
    for created, ms in ((before, 600_000), (now, 300_000)):
        from app.models import LearningAttempt

        db.add(
            LearningAttempt(
                organization_id=student.organization_id, student_id=student.id,
                subject="수학", chapter_no=1, result="correct", score=0,
                solve_time_ms=ms, created_at=created, updated_at=created,
            )
        )
    record_class_assignment(db, student, cls.id)  # 오늘 배정 시작
    db.flush()
    # 배정 시작을 30일 전으로 조정(테스트 시나리오)
    row = db.query(ClassAssignment).filter(ClassAssignment.student_id == student.id).first()
    row.started_on = now - timedelta(days=30)
    db.commit()

    m = aggregate.student_roster_metrics(db, [student.id])
    assert m[student.id]["year_min"] == 5, "배정(30일 전) 이전 10분은 제외돼야 한다"
    # 월간 창은 이번 달만 — 40일 전 10분은 제외, 오늘 5분만
    assert m[student.id]["month_min"] == 5
