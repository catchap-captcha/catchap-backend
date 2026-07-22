"""일일 신규 상한(문제은행 2단계) — 하루 새 문항 예산 소진 시 새 문항을 억제하되
복습·틀린 것은 계속 낸다. 오늘의 Q·코스 Q가 같은 과목이면 예산을 공유한다."""
from app.services import bank_mode, subject_banks


def test_daily_new_cap_suppresses_new_but_not_wrong(db, seed_org, monkeypatch):
    monkeypatch.setattr(bank_mode, "DAILY_NEW_CAP", 2)
    student = seed_org["student"]
    ids = [q["id"] for q in subject_banks.playable_pool("수학")]
    assert len(ids) >= 4

    assert bank_mode._daily_new_remaining(db, student.id, "수학") == 2
    assert bank_mode.pick_question(db, student, "수학") is not None  # 상한 전엔 새 문항 나옴

    # 새 문항 2개 시작(정답) → 오늘 시작한 새 문항 = 상한(2) 도달
    bank_mode.record_answer(db, student.id, "수학", ids[0], True)
    bank_mode.record_answer(db, student.id, "수학", ids[1], True)
    db.commit()
    assert bank_mode._daily_new_remaining(db, student.id, "수학") == 0
    # 상한 도달 + 만기·틀린 없음 → 새 문항 억제로 None(오늘 완료)
    assert bank_mode.pick_question(db, student, "수학") is None
    # 코스 Q(pick_from)도 같은 예산 — 새 문항 억제(우회 방지). 단 pick_from은 휴면 폴백이
    # 있어 None이 아니라 이미 시작한 휴면 문항(ids[0]/ids[1])을 낸다(새 ids[2:]는 안 냄).
    qf = bank_mode.pick_from(db, student, "수학", ids)
    assert qf is not None and qf["id"] in {ids[0], ids[1]}
    # queue_status의 new도 상한 반영(0)
    assert bank_mode.queue_status(db, student, "수학")["new"] == 0

    # 틀린 것은 상한과 무관하게 계속 재출제된다
    bank_mode.record_answer(db, student.id, "수학", ids[2], False)
    db.commit()
    q = bank_mode.pick_question(db, student, "수학")
    assert q is not None and q["id"] == ids[2]


def test_under_cap_serves_new(db, seed_org):
    """상한 미도달이면 새 문항 정상 서빙(오탐 방지)."""
    student = seed_org["student"]
    assert bank_mode._daily_new_remaining(db, student.id, "수학") == bank_mode.DAILY_NEW_CAP
    assert bank_mode.pick_question(db, student, "수학") is not None
