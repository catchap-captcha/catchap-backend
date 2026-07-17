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
