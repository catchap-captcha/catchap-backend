"""(강사 계정 × 코스 과목)별 출제 규칙 — 스코프 해석·목록·삭제 규약."""

from app.services import settings_service


def test_resolve_gen_rules_scope_then_global(db):
    # 전역만 있을 때 — 어떤 (강사, 과목)이든 전역을 쓴다.
    settings_service.set_setting(db, "llm_gen_rules", "GLOBAL", updated_by=None)
    db.commit()
    assert settings_service.resolve_gen_rules(db, "instrA", "수학") == "GLOBAL"

    # (강사A, 수학) 전용본 저장 — 그 조합만 전용, 나머지는 전역.
    settings_service.set_setting(
        db, settings_service.scoped_gen_key("instrA", "수학"), "SCOPED_A_MATH", updated_by=None
    )
    db.commit()
    assert settings_service.resolve_gen_rules(db, "instrA", "수학") == "SCOPED_A_MATH"
    assert settings_service.resolve_gen_rules(db, "instrA", "영어") == "GLOBAL"  # 다른 과목
    assert settings_service.resolve_gen_rules(db, "instrB", "수학") == "GLOBAL"  # 다른 강사

    # 목록 — 저장한 전용본이 (강사, 과목, 규칙)으로 나온다.
    listed = settings_service.list_scoped_gen_rules(db)
    assert {"instructor_id": "instrA", "subject": "수학", "rules": "SCOPED_A_MATH"} in listed

    # 빈 값 저장 = 전용본 삭제 → 전역으로 복귀.
    settings_service.set_setting(
        db, settings_service.scoped_gen_key("instrA", "수학"), "", updated_by=None
    )
    db.commit()
    assert settings_service.resolve_gen_rules(db, "instrA", "수학") == "GLOBAL"
    assert settings_service.list_scoped_gen_rules(db) == []


def test_resolve_gen_rules_none_when_nothing_set(db):
    # 전역·전용 아무것도 없으면 None(호출부가 서버 기본값 DEFAULT_GEN_RULES 사용).
    assert settings_service.resolve_gen_rules(db, "instrA", "수학") is None
    assert settings_service.resolve_gen_rules(db, None, None) is None
