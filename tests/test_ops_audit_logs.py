"""감사 로그(GET /ops/logs) — ★"말 그대로 로그" 인가.

그전에는 「누가·언제·무슨 종류」까지만 보여 줬다.

    코스 수정   운영자 (운영자)   코스   2026-08-16 12:21

★어느 코스인지도, 무엇을 어떻게 바꿨는지도 없었다. 그런데 둘 다 DB 에는 있다 —
target_id 로 이름을 찾을 수 있고, before_json/after_json 이 저장돼 있다.
★응답에 안 실어 보내고 있었을 뿐이다(문의 답변 하나만 예외였다).
"""

from datetime import datetime

import pytest
from app.core.permissions import Principal, require_ops
from app.main import app
from app.core.security import new_uuid
from app.models import AuditLog, Course, Lecture


@pytest.fixture()
def ops_client(client):
    app.dependency_overrides[require_ops] = lambda: Principal(kind="user", id="ops-1", role="ops")
    yield client
    app.dependency_overrides.pop(require_ops, None)


def _logs(client, **q) -> list[dict]:
    r = client.get("/api/v1/ops/logs", params=q)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_target_name_is_resolved_not_uuid(ops_client, db):
    """★대상이 UUID 가 아니라 이름으로 나와야 한다."""
    c = Course(instructor_id=new_uuid(), title="카카오 클라우드", subject="IT", status="active")
    db.add(c)
    db.commit()
    db.add(AuditLog(action="course.update", target_type="course", target_id=c.id,
                    before_json={"status": "active"}, after_json={"status": "hidden"}))
    db.commit()

    row = _logs(ops_client)[0]
    assert row["target_name"] == "카카오 클라우드"


def test_changes_shows_only_what_changed(ops_client, db):
    """★바뀐 칸만 — 안 바뀐 것을 늘어놓으면 바뀐 것이 묻힌다."""
    db.add(AuditLog(
        action="course.update", target_type="course", target_id="none",
        before_json={"title": "그대로", "status": "active", "order_no": 1},
        after_json={"title": "그대로", "status": "hidden", "order_no": 2},
    ))
    db.commit()

    ch = {c["field"]: c for c in _logs(ops_client)[0]["changes"]}
    assert set(ch) == {"status", "order_no"}, ch  # title 은 안 바뀌었으니 빠진다
    assert ch["status"]["before"] == "active" and ch["status"]["after"] == "hidden"


def test_create_and_delete_are_same_shape(ops_client, db):
    """만들기(before 없음)·지우기(after 없음)도 같은 모양으로 나와야 한다."""
    db.add(AuditLog(action="course.create", target_type="course", target_id="x",
                    after_json={"title": "새 코스"}))
    db.commit()
    ch = _logs(ops_client)[0]["changes"]
    assert ch == [{"field": "title", "before": None, "after": "새 코스"}], ch

    db.query(AuditLog).delete()
    db.add(AuditLog(action="course.delete", target_type="course", target_id="x",
                    before_json={"title": "지운 코스"}))
    db.commit()
    ch = _logs(ops_client)[0]["changes"]
    assert ch == [{"field": "title", "before": "지운 코스", "after": None}], ch


def test_question_target_says_which_lecture(ops_client, db):
    """문항 자체엔 이름이 없다 — ★어느 강의의 문항인지가 알고 싶은 것이다."""
    from app.models import LectureQuestion

    lec = Lecture(title="7강 빌링 그룹", subject="IT", video_ext=".mp4",
                  video_bytes=1, duration_sec=60, status="active", order_no=1,
                  uploaded_by=new_uuid())
    db.add(lec)
    db.commit()
    q = LectureQuestion(lecture_id=lec.id, position_sec=10, status="active",
                        payload={"prompt": "p", "options": ["a", "b"]}, answer_index=0)
    db.add(q)
    db.commit()
    db.add(AuditLog(action="lecture.question.delete", target_type="lecture_question",
                    target_id=q.id, after_json={"status": "deleted"}))
    db.commit()

    assert _logs(ops_client)[0]["target_name"] == "7강 빌링 그룹 의 문항"


def test_student_target_is_anonymous(ops_client, db, seed_org):
    """★학생은 실명을 쓰지 않는다 — 다른 화면과 같은 익명 코드 규약."""
    sp = seed_org["student"]
    db.add(AuditLog(action="student.assign_class", target_type="student", target_id=sp.id))
    db.commit()

    name = _logs(ops_client)[0]["target_name"]
    assert name is not None and name.startswith("학생 "), name
    # 닉네임(식별 정보)이 새지 않아야 한다
    assert (sp.nickname or "") not in name


def test_long_values_are_trimmed(ops_client, db):
    """값이 길면 자른다 — 목록 응답이 통째로 커지지 않게."""
    db.add(AuditLog(action="system.settings.ai_prompt", target_type="system_setting",
                    target_id="p", before_json={"rules": "가" * 500}, after_json={"rules": "나" * 500}))
    db.commit()
    ch = _logs(ops_client)[0]["changes"][0]
    assert len(ch["before"]) <= 121 and ch["before"].endswith("…")


def test_unknown_target_type_does_not_break(ops_client, db):
    """모르는 대상 종류는 이름을 ★지어내지 않는다(None)."""
    db.add(AuditLog(action="behavior.export", target_type=None, target_id=None))
    db.commit()
    row = _logs(ops_client)[0]
    assert row["target_name"] is None
    assert row["changes"] == []


def test_nested_settings_are_flattened(ops_client, db):
    """★설정 변경(100건)은 중첩 dict 라 그대로 두면 JSON 이 통째로 찍힌다.

    운영자에게는 개발자 말이다 — 펴서 ★바뀐 칸 하나만 짚어 준다.
    """
    db.add(AuditLog(
        action="settings.update", target_type="user_setting", target_id="u1",
        before_json={"settings": {"alerts": {"email": False, "push": True}, "theme": "light"}},
        after_json={"settings": {"alerts": {"email": True, "push": True}, "theme": "light"}},
    ))
    db.commit()
    ch = _logs(ops_client)[0]["changes"]
    assert ch == [{"field": "settings.alerts.email", "before": False, "after": True}], ch


def test_lists_are_not_flattened(ops_client, db):
    """⚠️목록은 펴지 않는다 — 순서가 뜻을 갖는 값이라 칸으로 나누면 오히려 읽기 어렵다."""
    db.add(AuditLog(
        action="lecture.reorder", target_type="lecture", target_id="l1",
        before_json={"order": ["a", "b"]}, after_json={"order": ["b", "a"]},
    ))
    db.commit()
    ch = _logs(ops_client)[0]["changes"]
    assert len(ch) == 1 and ch[0]["field"] == "order"
    assert ch[0]["before"] == ["a", "b"] and ch[0]["after"] == ["b", "a"]


def test_empty_change_is_dropped(ops_client, db):
    """★둘 다 비었으면 뺀다 — 아무 정보가 없는 줄이다.

    실측: 설정을 ★처음 저장할 때 before 가 {"settings": None} 로 남아,
    펴 놓으면 "settings" 키가 값 없이 홀로 남고 화면에 빈 줄이 생겼다.
    """
    db.add(AuditLog(
        action="settings.update", target_type="user_setting", target_id="u1",
        before_json={"settings": None},
        after_json={"settings": {"alerts": {"email": False}}},
    ))
    db.commit()
    ch = _logs(ops_client)[0]["changes"]
    assert ch == [{"field": "settings.alerts.email", "before": None, "after": False}], ch
