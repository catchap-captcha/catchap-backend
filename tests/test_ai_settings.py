"""운영자 AI 설정(system_settings) — 암호화 저장·마스킹 읽기·키 해석 우선순위."""

import json

from app.models import AuditLog, LectureQuestion, SystemSetting

from tests.test_captcha_api import _ops, auth


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
        headers=auth(ops_tok),
    )
    assert up.status_code == 200, up.text
    lec_id = up.json()["id"]

    r = client.post(
        f"/api/v1/ops/lectures/{lec_id}/questions/generate",
        json={"n": 1},
        headers=auth(ops_tok),
    )
    assert r.status_code == 200, r.text
    assert seen["api_key"] == "sk-console-key-7777", "콘솔 키가 LLM 호출에 쓰이지 않았다"
    assert seen["transcript"] is None  # STT 미설정 — 전사 없음
    body = r.json()
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
        headers=auth(ops_tok),
    )
    lec_id = up.json()["id"]
    r = client.post(
        f"/api/v1/ops/lectures/{lec_id}/questions/generate",
        json={"n": 2},
        headers=auth(ops_tok),
    )
    assert r.status_code == 200, r.text
    body = r.json()
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
        headers=auth(ops_tok),
    )
    r = client.post(
        f"/api/v1/ops/lectures/{up.json()['id']}/questions/generate",
        json={"n": 1},
        headers=auth(ops_tok),
    )
    assert r.status_code == 200, r.text  # 생성은 살아있다
    body = r.json()
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

    import app.clients.stt_client as stt_client

    def broken_transcribe(path, *, api_key):
        raise stt_client.SttError("HTTP 401: invalid api key")

    monkeypatch.setattr(stt_client, "transcribe_video", broken_transcribe)

    files = {"file": ("v.mp4", b"0" * 1024, "video/mp4")}
    up = client.post(
        "/api/v1/ops/lectures",
        data={"title": "STT 실패 강의", "subject": "국어", "duration_sec": "300"},
        files=files,
        headers=auth(ops_tok),
    )
    lec_id = up.json()["id"]

    r = client.post(
        f"/api/v1/ops/lectures/{lec_id}/questions/generate",
        json={"n": 1},
        headers=auth(ops_tok),
    )
    assert r.status_code == 502
    assert "STT" in r.json()["detail"]
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

    def fake_solve(questions, *, api_key=None, context=None, transcript=None):
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
