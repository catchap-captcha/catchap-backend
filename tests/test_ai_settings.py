"""운영자 AI 설정(system_settings) — 암호화 저장·마스킹 읽기·키 해석 우선순위."""

import json

import pytest

from app.models import AuditLog, Lecture, LectureQuestion, SystemSetting

from tests.test_captcha_api import _instructor, _ops, auth


def _gen_now(db, lec_id, n):
    """생성 로직(추출된 헬퍼)을 직접 구동 — 비동기 전환(0720)으로 POST는 잡만 만든다.
    실제 STT+생성 로직은 _generate_questions_now가 담당하므로 여기서 바로 호출해 검증한다."""
    from app.api.v1.endpoints.lectures import _generate_questions_now

    return _generate_questions_now(db, db.get(Lecture, lec_id), n, "actor")


def test_ai_settings_roundtrip_masked_and_encrypted(client, db, monkeypatch):
    """저장 → 마스킹 읽기(원문 미반환·끝 4자리) → DB에는 암호문만 → 빈 값으로 삭제."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "")
    ops_tok = _ops(client, db)

    # 초기 상태 — 둘 다 미설정
    r = client.get("/api/v1/ops/settings/ai", headers=auth(ops_tok))
    assert r.status_code == 200, r.text
    assert r.json()["llm"]["configured"] is False
    assert r.json()["stt"]["configured"] is False

    # 저장 — 응답에도 원문이 없어야 한다(끝 4자리만)
    r = client.put(
        "/api/v1/ops/settings/ai",
        json={"anthropic_api_key": "sk-ant-test-SECRET-1234", "openai_api_key": "sk-oai-XYZ-5678"},
        headers=auth(ops_tok),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["llm"]["configured"] is True
    assert body["llm"]["last4"] == "1234" and body["llm"]["source"] == "console"
    assert body["stt"]["configured"] is True and body["stt"]["last4"] == "5678"
    assert "SECRET" not in r.text  # 원문이 응답 어디에도 없다

    # DB에는 평문이 없다 — Fernet 암호문만
    rows = {s.key: s.value for s in db.query(SystemSetting).all()}
    assert set(rows) == {"anthropic_api_key", "openai_api_key"}
    assert "SECRET" not in rows["anthropic_api_key"]
    # 감사 로그에도 값·끝자리가 없다(키 이름만)
    log = (
        db.query(AuditLog)
        .filter(AuditLog.action == "system.settings.ai_keys")
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert log is not None
    dumped = json.dumps(log.after_json or {})
    assert "SECRET" not in dumped and "1234" not in dumped

    # 해석 우선순위 — DB(콘솔) 값이 곧바로 쓰인다
    from app.services import settings_service

    assert settings_service.resolve_anthropic_key(db) == "sk-ant-test-SECRET-1234"
    assert settings_service.resolve_openai_key(db) == "sk-oai-XYZ-5678"

    # 빈 문자열 = 삭제(미설정 복귀). 미전송 키는 그대로 유지된다.
    r = client.put(
        "/api/v1/ops/settings/ai", json={"openai_api_key": ""}, headers=auth(ops_tok)
    )
    assert r.status_code == 200
    assert r.json()["stt"]["configured"] is False
    assert r.json()["llm"]["configured"] is True  # 미전송 — 변경 없음


def test_ai_settings_env_fallback_and_ops_only(client, db, seed_org, monkeypatch):
    """DB에 없으면 .env 폴백이 source=env로 보이고, 운영자 외 접근은 거부된다."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "sk-env-fallback-9999")
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "")
    ops_tok = _ops(client, db)
    r = client.get("/api/v1/ops/settings/ai", headers=auth(ops_tok))
    assert r.json()["llm"] == {
        "configured": True, "last4": "9999", "source": "env", "updated_at": None,
    }

    from app.services import settings_service

    assert settings_service.resolve_anthropic_key(db) == "sk-env-fallback-9999"

    # 교사(비운영자)는 읽기·쓰기 모두 거부
    t = client.post(
        "/api/v1/auth/login",
        json={"email": "t1@test.dev", "password": "Password123!"},
    )
    assert t.status_code == 200, t.text
    tt = t.json()["access_token"]
    assert client.get("/api/v1/ops/settings/ai", headers=auth(tt)).status_code in (401, 403)
    assert (
        client.put(
            "/api/v1/ops/settings/ai", json={"anthropic_api_key": "x"}, headers=auth(tt)
        ).status_code
        in (401, 403)
    )


def test_generate_uses_console_key_and_reports_transcript_flag(
    client, db, monkeypatch, tmp_path
):
    """생성 파이프라인이 '콘솔 키'를 실제로 쓰는지 — LLM 호출을 가로채 키를 검증한다.

    STT 미설정이면 transcript_used=false가 정직하게 내려오고, 전사 없이도 draft가
    생성된다(메타 기반·position=0 미배치)."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "")
    monkeypatch.setattr(get_settings(), "LECTURE_MEDIA_DIR", str(tmp_path))
    ops_tok = _ops(client, db)
    itok = _instructor(client, db)

    # 콘솔에서 LLM 키만 저장(STT는 미설정)
    r = client.put(
        "/api/v1/ops/settings/ai",
        json={"anthropic_api_key": "sk-console-key-7777"},
        headers=auth(ops_tok),
    )
    assert r.status_code == 200

    seen = {}

    def fake_generate(**kwargs):
        seen.update(kwargs)
        return [
            {"prompt": "전사 없이 낸 문제", "options": ["가", "나"], "answer_index": 0, "explain": ""}
        ]

    import app.clients.ai_client as ai_client

    monkeypatch.setattr(ai_client, "generate_lecture_questions", fake_generate)
    # 자기검증(2번째 LLM)도 가로채 실제 네트워크 호출을 막는다(판정 자체는 다른 테스트에서)
    monkeypatch.setattr(
        ai_client, "verify_questions",
        lambda items, **k: [
            {"blind_passed": True, "transcript_passed": None, "verdict": "bank"}
        ] * len(items),
    )

    files = {"file": ("v.mp4", b"0" * 1024, "video/mp4")}
    up = client.post(
        "/api/v1/ops/lectures",
        data={"title": "설정 검증 강의", "subject": "국어", "duration_sec": "300"},
        files=files,
        headers=auth(itok),
    )
    assert up.status_code == 200, up.text
    lec_id = up.json()["id"]

    body = _gen_now(db, lec_id, 1)
    assert seen["api_key"] == "sk-console-key-7777", "콘솔 키가 LLM 호출에 쓰이지 않았다"
    assert seen["transcript"] is None  # STT 미설정 — 전사 없음
    assert body["transcript_used"] is False
    assert body["questions"][0]["position_sec"] == 0  # 시점 미배치 draft
    assert body["questions"][0]["status"] == "draft"
    assert db.query(LectureQuestion).count() == 1


def test_self_verification_tags_bank_vs_captcha(client, db, monkeypatch, tmp_path):
    """자기검증(2번째 LLM): 상식으로 풀린 문항=은행 후보(bank), 못 푼 문항=캡차 후보(captcha).

    생성 LLM과 별개로 solve LLM을 가로채, 판정이 문항 메타(solver_passed·
    suggested_placement)와 응답 요약(bank/captcha_candidates)에 반영되는지 고정한다."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "")
    monkeypatch.setattr(get_settings(), "LECTURE_MEDIA_DIR", str(tmp_path))
    ops_tok = _ops(client, db)
    itok = _instructor(client, db)
    client.put(
        "/api/v1/ops/settings/ai",
        json={"anthropic_api_key": "sk-console-key-7777"},
        headers=auth(ops_tok),
    )

    import app.clients.ai_client as ai_client

    monkeypatch.setattr(
        ai_client,
        "generate_lecture_questions",
        lambda **k: [
            {"prompt": "상식 문제", "options": ["가", "나"], "answer_index": 0, "explain": ""},
            {"prompt": "강의 필요 문제", "options": ["다", "라"], "answer_index": 1, "explain": ""},
        ],
    )
    # 1번=블라인드로 풀림(상식→bank), 2번=블라인드 못 풀고 자막으론 풀림(→captcha)
    monkeypatch.setattr(
        ai_client, "verify_questions",
        lambda items, **k: [
            {"blind_passed": True, "transcript_passed": None, "verdict": "bank"},
            {"blind_passed": False, "transcript_passed": None, "verdict": "captcha"},
        ],
    )

    up = client.post(
        "/api/v1/ops/lectures",
        data={"title": "자기검증 강의", "subject": "국어", "duration_sec": "300"},
        files={"file": ("v.mp4", b"0" * 1024, "video/mp4")},
        headers=auth(itok),
    )
    lec_id = up.json()["id"]
    body = _gen_now(db, lec_id, 2)
    assert body["self_verified"] is True
    assert body["bank_candidates"] == 1 and body["captcha_candidates"] == 1
    assert body["verify_error"] is None
    qs = body["questions"]
    assert qs[0]["solver_passed"] is True and qs[0]["suggested_placement"] == "bank"
    assert qs[1]["solver_passed"] is False and qs[1]["suggested_placement"] == "captcha"


def test_self_verification_failure_is_honest_not_swallowed(client, db, monkeypatch, tmp_path):
    """자기검증 LLM이 실패해도 생성은 살리되, 조용히 삼키지 않고 verify_error로 노출한다."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "")
    monkeypatch.setattr(get_settings(), "LECTURE_MEDIA_DIR", str(tmp_path))
    ops_tok = _ops(client, db)
    itok = _instructor(client, db)
    client.put(
        "/api/v1/ops/settings/ai",
        json={"anthropic_api_key": "sk-console-key-7777"},
        headers=auth(ops_tok),
    )

    import app.clients.ai_client as ai_client
    from app.clients.ai_client import AiGenerationError

    monkeypatch.setattr(
        ai_client,
        "generate_lecture_questions",
        lambda **k: [{"prompt": "문제", "options": ["가", "나"], "answer_index": 0, "explain": ""}],
    )

    def boom(items, **k):
        raise AiGenerationError("solver 응답 파싱 실패")

    monkeypatch.setattr(ai_client, "verify_questions", boom)

    up = client.post(
        "/api/v1/ops/lectures",
        data={"title": "검증실패 강의", "subject": "국어", "duration_sec": "300"},
        files={"file": ("v.mp4", b"0" * 1024, "video/mp4")},
        headers=auth(itok),
    )
    body = _gen_now(db, up.json()["id"], 1)  # 생성은 살아있다(자기검증만 실패)
    assert body["created"] == 1
    assert body["self_verified"] is False
    assert "solver" in (body["verify_error"] or "")  # 조용히 삼키지 않음
    assert body["questions"][0]["solver_passed"] is None  # 미판정


def test_generate_stt_failure_is_honest_502(client, db, monkeypatch, tmp_path):
    """STT 키가 '설정돼 있는데' 전사가 실패하면 — 메타 폴백으로 강등하지 않고 502."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "sk-llm-ok")
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-stt-broken")
    monkeypatch.setattr(get_settings(), "LECTURE_MEDIA_DIR", str(tmp_path))
    ops_tok = _ops(client, db)
    itok = _instructor(client, db)

    import app.clients.stt_client as stt_client

    def broken_transcribe(path, *, api_key):
        raise stt_client.SttError("HTTP 401: invalid api key")

    monkeypatch.setattr(stt_client, "transcribe_video", broken_transcribe)

    files = {"file": ("v.mp4", b"0" * 1024, "video/mp4")}
    up = client.post(
        "/api/v1/ops/lectures",
        data={"title": "STT 실패 강의", "subject": "국어", "duration_sec": "300"},
        files=files,
        headers=auth(itok),
    )
    lec_id = up.json()["id"]

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        _gen_now(db, lec_id, 1)
    assert ei.value.status_code == 502 and "STT" in str(ei.value.detail)
    assert db.query(LectureQuestion).count() == 0  # 어떤 문항도 생성되지 않는다


def test_ai_client_parses_position_suggestions():
    """전사 기반 응답의 시점 제안 파싱 — 유효 채택, 무효(역전 등)는 시점만 폐기."""
    from app.clients.ai_client import _parse_questions

    raw = (
        '[{"prompt":"질문1","options":["a","b"],"answer_index":0,"explain":"",'
        '"position_sec":45,"content_start_sec":12},'
        '{"prompt":"질문2","options":["a","b"],"answer_index":1,"explain":"",'
        '"position_sec":30,"content_start_sec":30},'
        '{"prompt":"질문3","options":["a","b"],"answer_index":0,"explain":""}]'
    )
    out = _parse_questions(raw, 3)
    assert out[0]["position_sec"] == 45 and out[0]["content_start_sec"] == 12
    # content_start >= position → 시점만 버림(문항은 유지, cs 미포함)
    assert out[1]["position_sec"] == 30 and "content_start_sec" not in out[1]
    assert "position_sec" not in out[2]


def test_solve_questions_parsing_and_conservative_default(monkeypatch):
    """solve_questions 파싱 — q 번호로 매칭, 답 누락 문항은 False(보수적=캡차 후보)."""
    import app.clients.ai_client as ai
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "sk-x")
    qs = [
        {"prompt": "q1", "options": ["가", "나"], "answer_index": 0},  # LLM 0 → 맞힘 → True
        {"prompt": "q2", "options": ["다", "라"], "answer_index": 1},  # LLM 0 → 틀림 → False
        {"prompt": "q3", "options": ["마", "바"], "answer_index": 0},  # 응답 누락 → False(보수적)
    ]
    monkeypatch.setattr(
        ai, "_post_messages",
        lambda key, prompt, **kw: '[{"q":1,"answer_index":0},{"q":2,"answer_index":0}]',
    )
    assert ai.solve_questions(qs, api_key="sk-x") == [True, False, False]
    assert ai.solve_questions([]) == []  # 빈 입력은 호출 없이 []


def test_verify_questions_majority_and_three_way_verdict(monkeypatch):
    """verify_questions — 셔플 3회 다수결 + 자막 solve로 3분류(bank/captcha/discard).

    q1: 블라인드 2/3 → bank(상식). q2: 블라인드 0/3·자막 ✓ → captcha(강의 의존·정상).
    q3: 블라인드 0/3·자막 ✗ → discard(불량 의심). 셔플 변형본이 와도 판정이 원 문항
    순서로 돌아오는지(answer_index 재배치 포함)를 함께 고정한다."""
    import app.clients.ai_client as ai

    qs = [
        {"prompt": "상식", "options": ["a", "b", "c", "d"], "answer_index": 0},
        {"prompt": "강의필요", "options": ["a", "b", "c", "d"], "answer_index": 1},
        {"prompt": "불량", "options": ["a", "b", "c", "d"], "answer_index": 2},
    ]
    calls = {"blind": 0}

    def fake_solve(questions, *, api_key=None, context=None, transcript=None, models=None, on_usage=None, openai_key=None, rules_override=None):
        # 공개 맥락이 항상 전달되는지(공격자 조건 일치) 고정
        assert context and context.get("title") == "T"
        if transcript is not None:
            return [False, True, False]  # 자막 주면: q2만 풀림
        calls["blind"] += 1
        # 블라인드: q1만 2번째 시도까지 풀림(2/3 다수결 통과), q2·q3은 전부 실패
        return [calls["blind"] <= 2, False, False]

    monkeypatch.setattr(ai, "solve_questions", fake_solve)
    out = ai.verify_questions(
        qs, api_key="sk-x", context={"title": "T"}, transcript=[{"start": 0, "end": 1, "text": "x"}]
    )
    assert calls["blind"] == 3  # 셔플 3회
    assert [v["verdict"] for v in out] == ["bank", "captcha", "discard"]
    assert out[0]["blind_passed"] is True and out[1]["transcript_passed"] is True
    assert out[2]["transcript_passed"] is False

    # 자막이 없으면 불량 판별 불가 — 못 푼 문항은 captcha(종전 동작 유지)
    calls["blind"] = 0
    out2 = ai.verify_questions(qs, api_key="sk-x", context={"title": "T"}, transcript=None)
    assert [v["verdict"] for v in out2] == ["bank", "captcha", "captcha"]
    assert all(v["transcript_passed"] is None for v in out2)


def test_shuffled_variants_remap_answer(monkeypatch):
    """_shuffled_variants — 보기 순서가 바뀌어도 answer_index가 정답을 계속 가리킨다."""
    import random

    import app.clients.ai_client as ai

    q = {"prompt": "p", "options": ["정답", "b", "c", "d"], "answer_index": 0}
    for seed in range(6):
        v = ai._shuffled_variants([q], random.Random(seed))[0]
        assert v["options"][v["answer_index"]] == "정답"
        assert sorted(v["options"]) == sorted(q["options"])


def test_ai_verify_prompt_edit_roundtrip(client, db):
    """검증(자기검증) 판정 지침 편집 — 기본값 조회 → 저장 → is_custom → 빈 값 복원.
    생성 '출제 규칙'(llm_gen_rules)과 별개 key(llm_verify_rules)로 독립 저장되는지 고정한다."""
    from app.clients.ai_client import DEFAULT_GEN_RULES, DEFAULT_VERIFY_RULES

    ops_tok = _ops(client, db)

    # 초기 — 미설정이면 기본값 그대로, is_custom False
    r = client.get("/api/v1/ops/settings/ai/verify-prompt", headers=auth(ops_tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_custom"] is False
    assert body["rules"] == DEFAULT_VERIFY_RULES
    assert body["default_rules"] == DEFAULT_VERIFY_RULES

    # 저장 — 커스텀 판정 지침. 응답이 사용자 지정 상태로 갱신된다(가짜 성공 금지 — 서버 응답 확인).
    r = client.put(
        "/api/v1/ops/settings/ai/verify-prompt",
        json={"rules": "매우 엄격하게 판단하세요."},
        headers=auth(ops_tok),
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_custom"] is True
    assert r.json()["rules"] == "매우 엄격하게 판단하세요."

    # 생성 규칙과 '별개 key'로 저장되고, 생성 프롬프트 조회는 영향받지 않는다
    from app.services import settings_service

    assert settings_service.get_setting(db, "llm_verify_rules") == "매우 엄격하게 판단하세요."
    r = client.get("/api/v1/ops/settings/ai/prompt", headers=auth(ops_tok))
    assert r.json()["is_custom"] is False and r.json()["rules"] == DEFAULT_GEN_RULES

    # 빈 값 = 기본값 복원
    r = client.put(
        "/api/v1/ops/settings/ai/verify-prompt", json={"rules": ""}, headers=auth(ops_tok)
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_custom"] is False
    assert r.json()["rules"] == DEFAULT_VERIFY_RULES


# ─────────────────────────────────────────────────────────────────────
# 「사용 모델」 표기 (0816)
#
# 화면은 .env LLM_MODEL 을 "사용 모델" 이라고 찍고 있었다. 그런데 그건 ★슬롯이
# 하나도 없을 때만 쓰는 폴백이고, 실제로는 슬롯에 배정된 모델로 돈다.
# 실측: 화면 "사용 모델: claude-opus-4-8" ↔ 실제 생성 claude-opus-5
# ─────────────────────────────────────────────────────────────────────


def test_ai_settings_reports_model_actually_in_use(client, db, monkeypatch):
    from app.core.config import get_settings
    from app.models import AiModelConfig
    from app.services import ai_models_service

    ops_tok = _ops(client, db)
    monkeypatch.setattr(get_settings(), "LLM_MODEL", "폴백-모델")

    # 등록된 모델이 없으면 실제로 폴백을 쓴다 — 그때만 None 이다
    r = client.get("/api/v1/ops/settings/ai", headers=auth(ops_tok))
    assert r.status_code == 200, r.text
    assert r.json()["llm_model"] == "폴백-모델"
    assert r.json()["llm_model_in_use"] is None

    m = AiModelConfig(provider="Anthropic", model_id="진짜-쓰는-모델", name="생성용", enabled=True)
    db.add(m)
    db.commit()
    ai_models_service.set_slot(db, "generate", m.id, updated_by=None)
    db.commit()

    body = client.get("/api/v1/ops/settings/ai", headers=auth(ops_tok)).json()
    # ★폴백 값은 그대로 두되(하위호환), 실제로 도는 모델을 따로 알려 준다
    assert body["llm_model"] == "폴백-모델"
    assert body["llm_model_in_use"] == "진짜-쓰는-모델"


def test_model_in_use_follows_slot_not_env(client, db, monkeypatch):
    """★슬롯 모델을 끄면 실제로 도는 것도 바뀐다 — 화면이 따라가야 한다."""
    from app.core.config import get_settings
    from app.models import AiModelConfig
    from app.services import ai_models_service

    ops_tok = _ops(client, db)
    monkeypatch.setattr(get_settings(), "LLM_MODEL", "폴백-모델")

    a = AiModelConfig(provider="Anthropic", model_id="A", name="A", enabled=True)
    db.add(a)
    db.commit()
    ai_models_service.set_slot(db, "generate", a.id, updated_by=None)
    db.commit()
    assert client.get("/api/v1/ops/settings/ai", headers=auth(ops_tok)).json()["llm_model_in_use"] == "A"

    a.enabled = False
    db.commit()
    # 자동 스왑도 없고 켜진 모델도 없으면 후보 0 → 그때는 폴백을 쓴다
    assert client.get("/api/v1/ops/settings/ai", headers=auth(ops_tok)).json()["llm_model_in_use"] is None
