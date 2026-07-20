"""STT 워커(stt-worker/main.py)의 FastAPI HTTP 계층을 실제 구동해 검증.

실제 전사(faster-whisper/GPU)는 spike-whisper가 이미 검증했으므로 모델은 stub한다. 여기선
토큰 인증·파일 업로드 → 세그먼트 응답(공백 트림·빈 텍스트 제외)·무음 422 같은 HTTP 계약을 본다.
faster_whisper import가 lazy라 라이브러리 없이도 앱을 로드·구동할 수 있다."""
import importlib.util
import os

import pytest
from fastapi.testclient import TestClient

_WORKER_MAIN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stt-worker", "main.py"
)


def _load_worker():
    spec = importlib.util.spec_from_file_location("stt_worker_main", _WORKER_MAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # faster_whisper는 lazy라 여기서 import 안 됨
    return mod


class _FakeInfo:
    duration = 3.0
    language = "ko"


class _FakeSeg:
    def __init__(self, s, e, t):
        self.start, self.end, self.text = s, e, t


@pytest.fixture()
def worker(monkeypatch):
    mod = _load_worker()
    monkeypatch.setattr(mod, "_TOKEN", "secret")

    class FakeModel:
        def transcribe(self, path, **k):
            # 앞뒤 공백·빈 텍스트 세그먼트 포함 — 정규화가 처리하는지 본다
            return iter([_FakeSeg(0.0, 2.5, " 분수는 "), _FakeSeg(2.5, 3.0, "")]), _FakeInfo()

    monkeypatch.setattr(mod, "_get_model", lambda: FakeModel())
    return TestClient(mod.app)


def test_health_no_model_needed(worker):
    r = worker.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_transcribe_returns_normalized_segments(worker):
    r = worker.post(
        "/transcribe",
        headers={"X-Worker-Token": "secret"},
        files={"file": ("v.mp4", b"\x00" * 128, "video/mp4")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["segments"] == [{"start": 0.0, "end": 2.5, "text": "분수는"}]  # 빈 텍스트 제외
    assert body["language"] == "ko"


def test_transcribe_rejects_bad_token(worker):
    r = worker.post(
        "/transcribe",
        headers={"X-Worker-Token": "wrong"},
        files={"file": ("v.mp4", b"\x00" * 128, "video/mp4")},
    )
    assert r.status_code == 401


def test_transcribe_empty_is_422(monkeypatch):
    mod = _load_worker()
    monkeypatch.setattr(mod, "_TOKEN", "")  # 토큰 검사 생략

    class SilentModel:
        def transcribe(self, path, **k):
            return iter([]), _FakeInfo()  # 무음 — 세그먼트 0개

    monkeypatch.setattr(mod, "_get_model", lambda: SilentModel())
    r = TestClient(mod.app).post(
        "/transcribe", files={"file": ("v.mp4", b"\x00" * 8, "video/mp4")}
    )
    assert r.status_code == 422  # 빈 자막을 성공으로 위장하지 않는다
