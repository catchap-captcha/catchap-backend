"""자체 호스팅 STT 워커(faster-whisper) 클라이언트 — 라우팅·전사 파싱·에러 정직성.

실제 GPU 전사는 spike-whisper에서 검증됨. 여기선 stt_client의 (1)워커 vs OpenAI 라우팅과
(2)워커 HTTP 계약(세그먼트 정규화·비200/빈결과는 SttError)을 모킹으로 검증한다."""
import httpx
import pytest

from app.clients import stt_client
from app.clients.stt_client import (
    SttError,
    SttNotConfiguredError,
    transcribe_lecture,
    transcribe_via_worker,
)


def _video(tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"\x00" * 128)
    return p


def test_transcribe_lecture_prefers_worker(tmp_path, monkeypatch):
    """워커 URL이 있으면 OpenAI가 아니라 워커를 쓴다(우선순위)."""
    called = {}
    monkeypatch.setattr(stt_client, "transcribe_via_worker",
                        lambda path, **k: (called.__setitem__("worker", k) or [{"start": 0, "end": 1, "text": "안녕"}]))
    monkeypatch.setattr(stt_client, "transcribe_video",
                        lambda path, **k: called.__setitem__("openai", True))
    out = transcribe_lecture(_video(tmp_path), worker_url="http://w:8100", worker_token="t", api_key="sk-x")
    assert "worker" in called and "openai" not in called  # 워커만 불림
    assert out[0]["text"] == "안녕"


def test_transcribe_lecture_falls_back_to_openai(tmp_path, monkeypatch):
    """워커 URL이 없으면 OpenAI로 폴백(하위호환)."""
    monkeypatch.setattr(stt_client, "transcribe_video",
                        lambda path, **k: [{"start": 0, "end": 1, "text": "OpenAI"}])
    out = transcribe_lecture(_video(tmp_path), worker_url="", api_key="sk-x")
    assert out[0]["text"] == "OpenAI"


def test_transcribe_lecture_no_config_raises(tmp_path):
    """워커·키 둘 다 없으면 정직한 미설정 예외(빈 자막 위장 금지)."""
    with pytest.raises(SttNotConfiguredError):
        transcribe_lecture(_video(tmp_path), worker_url="", api_key="")


def test_worker_parses_and_normalizes_segments(tmp_path, monkeypatch):
    """워커 응답 → [{start,end,text}] 정규화(공백 트림·빈 텍스트 제외)."""
    def fake_post(url, **k):
        assert url.endswith("/transcribe")
        assert k["headers"]["X-Worker-Token"] == "secret"
        return httpx.Response(200, json={"segments": [
            {"start": 0.0, "end": 2.5, "text": " 분수는 "},
            {"start": 2.5, "end": 3.0, "text": ""},  # 빈 텍스트 → 제외
        ]})
    monkeypatch.setattr(httpx, "post", fake_post)
    out = transcribe_via_worker(_video(tmp_path), worker_url="http://w:8100/", worker_token="secret")
    assert out == [{"start": 0.0, "end": 2.5, "text": "분수는"}]


def test_worker_non_200_is_honest_error(tmp_path, monkeypatch):
    """워커가 비200이면 SttError로 원인 전파(성공 위장 안 함)."""
    monkeypatch.setattr(httpx, "post", lambda url, **k: httpx.Response(500, text="boom"))
    with pytest.raises(SttError):
        transcribe_via_worker(_video(tmp_path), worker_url="http://w:8100")


def test_worker_empty_segments_is_error(tmp_path, monkeypatch):
    """세그먼트 0개(무음 등)면 SttError — 빈 자막으로 문항 생성하지 않는다."""
    monkeypatch.setattr(httpx, "post", lambda url, **k: httpx.Response(200, json={"segments": []}))
    with pytest.raises(SttError):
        transcribe_via_worker(_video(tmp_path), worker_url="http://w:8100")


def test_worker_url_missing_raises_not_configured(tmp_path):
    with pytest.raises(SttNotConfiguredError):
        transcribe_via_worker(_video(tmp_path), worker_url="")
