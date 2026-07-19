"""강사 제공 자막(전사) — 파서 + 붙여넣기/업로드 저장 + 자동 STT 대체·캐시.

왜: 강사가 이미 자막을 가진 경우 자동 STT(Whisper)를 다시 도는 건 품질↓·비용↑·25MB 한계.
강사 자막을 받아 LLM 생성에 우선 쓰고, 자동 STT 결과도 저장해 재전사를 막는다.
"""

import pytest

from app.services.transcript_parser import TranscriptParseError, parse_transcript
from tests.test_captcha_api import _ops, auth
from tests.test_lectures import _upload_lecture


# ------------------------------------------------------------------ 파서 단위
def test_parse_srt_vtt_paste():
    srt = "1\n00:00:01,000 --> 00:00:04,000\n안녕\n\n2\n00:00:05,000 --> 00:00:07,000\n분수"
    r = parse_transcript(srt)
    assert len(r) == 2 and r[0] == {"start": 1.0, "end": 4.0, "text": "안녕"}

    vtt = "WEBVTT\n\n00:01.000 --> 00:03.000\n<v T>안녕\n\n00:05.000 --> 00:07.000\n끝"
    assert [s["text"] for s in parse_transcript(vtt)] == ["안녕", "끝"]  # 태그 제거·헤더 스킵

    paste = "[00:10] 시작\n0:20 다음"
    r = parse_transcript(paste)
    assert r[0]["start"] == 10.0 and r[0]["end"] == 20.0 and r[1]["text"] == "다음"


def test_parse_garbage_raises():
    for bad in ["", "   ", "타임스탬프 없는 그냥 텍스트"]:
        with pytest.raises(TranscriptParseError):
            parse_transcript(bad)


# ------------------------------------------------------------------ 엔드포인트
def _lec(client, tok):
    return _upload_lecture(client, tok, title="자막강의", subject="수학", duration=600).json()


def test_transcript_paste_get_delete(client, db, seed_org):
    tok = _ops(client, db)
    lec = _lec(client, tok)
    r = client.get(f"/api/v1/ops/lectures/{lec['id']}/transcript", headers=auth(tok))
    assert r.json()["has_transcript"] is False

    r = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/transcript",
        json={"content": "[00:05] 첫 내용\n0:30 둘째 내용", "format": "auto"}, headers=auth(tok),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_transcript"] and body["source"] == "paste" and body["segment_count"] == 2

    r = client.get(f"/api/v1/ops/lectures/{lec['id']}/transcript", headers=auth(tok))
    assert r.json()["segment_count"] == 2 and len(r.json()["preview"]) == 2

    # 빈/불량 자막 → 400(빈 자막을 성공처럼 저장하지 않음)
    r = client.put(
        f"/api/v1/ops/lectures/{lec['id']}/transcript",
        json={"content": "타임스탬프 없는 텍스트"}, headers=auth(tok),
    )
    assert r.status_code == 400

    r = client.delete(f"/api/v1/ops/lectures/{lec['id']}/transcript", headers=auth(tok))
    assert r.status_code == 200
    assert client.get(
        f"/api/v1/ops/lectures/{lec['id']}/transcript", headers=auth(tok)
    ).json()["has_transcript"] is False


def test_transcript_upload_srt(client, db, seed_org):
    tok = _ops(client, db)
    lec = _lec(client, tok)
    srt_text = "1\n00:00:01,000 --> 00:00:03,000\n한글 자막\n"
    r = client.post(
        f"/api/v1/ops/lectures/{lec['id']}/transcript/upload",
        files={"file": ("sub.srt", srt_text.encode("utf-8"), "text/plain")}, headers=auth(tok),
    )
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "srt" and r.json()["segment_count"] == 1


def test_generate_prefers_stored_transcript_over_stt(client, db, monkeypatch, tmp_path, seed_org):
    """저장된 강사 자막이 있으면 STT를 건너뛰고 그 자막을 LLM에 넘긴다(transcript_source=paste)."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-stt")  # STT 키 있어도 안 써야 함
    monkeypatch.setattr(get_settings(), "LECTURE_MEDIA_DIR", str(tmp_path))
    tok = _ops(client, db)
    lec = _lec(client, tok)
    client.put(
        f"/api/v1/ops/lectures/{lec['id']}/transcript",
        json={"content": "[00:05] 강사자막 내용", "format": "auto"}, headers=auth(tok),
    )

    import app.clients.ai_client as ai
    import app.clients.stt_client as stt

    called = {"stt": False}

    def leak_stt(path, *, api_key):
        called["stt"] = True
        return [{"start": 0.0, "end": 1.0, "text": "STT_LEAK"}]

    monkeypatch.setattr(stt, "transcribe_video", leak_stt)
    seen = {}

    def fake_gen(**k):
        seen.update(k)
        return [{"prompt": "q", "options": ["가", "나"], "answer_index": 0, "explain": ""}]

    monkeypatch.setattr(ai, "generate_lecture_questions", fake_gen)
    monkeypatch.setattr(ai, "verify_questions", lambda items, **k: None)

    r = client.post(f"/api/v1/ops/lectures/{lec['id']}/questions/generate", json={"n": 1}, headers=auth(tok))
    assert r.status_code == 200, r.text
    assert called["stt"] is False  # STT 미호출
    assert r.json()["transcript_source"] == "paste"
    assert seen["transcript"][0]["text"] == "강사자막 내용"  # 저장 자막이 LLM에 전달(STT_LEAK 아님)


def test_generate_caches_auto_stt(client, db, monkeypatch, tmp_path, seed_org):
    """저장된 자막이 없으면 자동 STT하고 그 결과를 저장한다(재생성 시 재전사 방지)."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-stt")
    monkeypatch.setattr(get_settings(), "LECTURE_MEDIA_DIR", str(tmp_path))
    tok = _ops(client, db)
    lec = _lec(client, tok)

    import app.clients.ai_client as ai
    import app.clients.stt_client as stt

    monkeypatch.setattr(
        stt, "transcribe_video", lambda path, *, api_key: [{"start": 0.0, "end": 2.0, "text": "자동전사"}]
    )
    monkeypatch.setattr(
        ai, "generate_lecture_questions",
        lambda **k: [{"prompt": "q", "options": ["가", "나"], "answer_index": 0, "explain": ""}],
    )
    monkeypatch.setattr(ai, "verify_questions", lambda items, **k: None)

    r = client.post(f"/api/v1/ops/lectures/{lec['id']}/questions/generate", json={"n": 1}, headers=auth(tok))
    assert r.status_code == 200, r.text
    assert r.json()["transcript_source"] == "stt"
    # 자동 STT 결과가 캐시됐는지 — 다음 생성은 재전사 안 함
    t = client.get(f"/api/v1/ops/lectures/{lec['id']}/transcript", headers=auth(tok)).json()
    assert t["has_transcript"] and t["source"] == "stt" and t["segment_count"] == 1
