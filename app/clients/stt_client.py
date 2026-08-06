"""OpenAI Whisper STT 클라이언트 — 강의 영상 음성을 타임스탬프 있는 자막으로 전사.

LLM 문항 생성의 재료: 전사 세그먼트([start, end, text])가 있어야 "이 대목을 물어라"
(position_sec)와 "이 내용이 시작되는 시점"(content_start_sec)을 기계가 제안할 수 있다.

가짜 성공 금지 규약(ai_client와 동일): 키가 없으면 SttNotConfiguredError를 호출 전에
던지고, 호출/파싱 실패는 SttError로 정직하게 전파한다 — 빈 자막을 성공처럼 반환하지
않는다. 기존 의존성 httpx로 직접 호출한다(신규 SDK 불필요 — ai_client와 같은 이유).
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

_API_URL = "https://api.openai.com/v1/audio/transcriptions"
_MODEL = "whisper-1"  # verbose_json(세그먼트 타임스탬프)을 지원하는 안정 모델
_TIMEOUT_SEC = 300.0  # 수십 분짜리 강의 오디오 전사 여유
# 자체 워커는 GPU라도 대용량 영상은 수 분 걸릴 수 있어 넉넉히(25MB 한계가 없어 큰 영상도 옴)
_WORKER_TIMEOUT_SEC = 1800.0
# Whisper API 업로드 상한(공식 25MB) — 넘으면 서버가 413을 주지만, 왕복 전에
# 정직하게 거절해 운영자에게 원인(파일 크기)을 바로 알린다.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# 업로드 Content-Type — 강의 업로드 화이트리스트(mp4/webm)와 동일 집합
_MEDIA_TYPES = {".mp4": "video/mp4", ".webm": "video/webm"}


class SttNotConfiguredError(Exception):
    """STT API 키 미설정 — 전사 기능을 쓸 수 없다(호출 전에 발생)."""


class SttError(Exception):
    """STT 호출/파싱 실패 — 원인을 담아 정직하게 전파한다."""


def transcribe_video(path: Path, *, api_key: str) -> list[dict]:
    """강의 영상 파일 → 전사 세그먼트 [{start, end, text}] (초 단위, 시간순).

    Whisper는 mp4/webm 컨테이너를 그대로 받는다(별도 오디오 추출 불필요 — ffmpeg 없이
    동작한다는 기존 결정과 정합). 세그먼트가 하나도 없으면(무음 영상 등) SttError —
    빈 자막으로 문항을 생성하는 것은 성공 위장이다."""
    key = (api_key or "").strip()
    if not key:
        raise SttNotConfiguredError("STT API 키(OpenAI)가 설정되지 않았습니다.")
    if not path.is_file():
        raise SttError(f"강의 영상 파일을 찾을 수 없습니다: {path.name}")
    size = path.stat().st_size
    if size > _MAX_UPLOAD_BYTES:
        raise SttError(
            f"영상이 STT 업로드 상한(25MB)을 넘습니다({size / 1024 / 1024:.1f}MB). "
            "현재 버전은 25MB 이하 영상만 전사할 수 있습니다."
        )
    media_type = _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")

    try:
        with open(path, "rb") as f:
            resp = httpx.post(
                _API_URL,
                headers={"Authorization": f"Bearer {key}"},
                data={
                    "model": _MODEL,
                    "language": "ko",
                    "response_format": "verbose_json",
                },
                files={"file": (path.name, f, media_type)},
                timeout=_TIMEOUT_SEC,
            )
    except httpx.HTTPError as e:
        raise SttError(f"STT API 호출 실패(네트워크): {e}") from e
    if resp.status_code != 200:
        raise SttError(f"STT API 오류(HTTP {resp.status_code}): {resp.text[:300]}")

    body = resp.json()
    segments = body.get("segments")
    if not isinstance(segments, list) or not segments:
        raise SttError("STT 응답에 전사 세그먼트가 없습니다(무음 영상이거나 전사 실패).")
    out: list[dict] = []
    for seg in segments:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        try:
            start = max(0.0, float(seg.get("start", 0)))
            end = max(start, float(seg.get("end", start)))
        except (TypeError, ValueError):
            continue
        out.append({"start": round(start, 2), "end": round(end, 2), "text": text})
    if not out:
        raise SttError("STT 전사 결과가 비어 있습니다(유효한 발화 세그먼트 없음).")
    return out


# 워커로 보낼 오디오 규격 — whisper가 내부적으로 16kHz 모노로 리샘플하므로 이 규격으로
# 미리 뽑으면 품질 손실이 없다. FLAC은 이 샘플레이트 기준 무손실이라 전사 품질에 영향 없음.
_AUDIO_SR = "16000"
_AUDIO_EXTRACT_TIMEOUT_SEC = 1800.0


def _extract_audio(path: Path) -> Path:
    """강의 영상 → 16kHz 모노 FLAC 임시파일. 실패는 SttError로 정직하게 전파한다.

    ★왜 영상을 그대로 보내지 않나(2026-08-06 장애에서 배운 것):
    faster-whisper는 오디오만 디코딩하고 영상 트랙은 버린다. 그런데 예전 구현은 원본
    mp4를 통째로 POST했고, 11.9GB 강의에서 GPU 워커가 그 크기만큼 임시파일을 쓰려다
    디스크가 가득 차(Errno 28) 전사가 중단됐다. 오디오만 보내면 12GB → 수십 MB가 되어
    전송·워커 디스크·디코딩이 모두 줄고, 결과는 동일하다."""
    if shutil.which("ffmpeg") is None:
        raise SttError("ffmpeg이 설치되어 있지 않아 강의 오디오를 추출할 수 없습니다.")
    fd, tmp_name = tempfile.mkstemp(prefix="stt-audio-", suffix=".flac")
    os.close(fd)
    out_path = Path(tmp_name)
    cmd = [
        "ffmpeg", "-nostdin", "-y",
        "-i", str(path),
        "-vn",                # 영상 트랙 버림
        "-ac", "1",           # 모노
        "-ar", _AUDIO_SR,     # 16kHz — whisper 내부 규격
        "-c:a", "flac",
        str(out_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=_AUDIO_EXTRACT_TIMEOUT_SEC)
    except subprocess.TimeoutExpired as e:
        out_path.unlink(missing_ok=True)
        raise SttError("강의 오디오 추출이 제한 시간 안에 끝나지 않았습니다.") from e
    except OSError as e:
        out_path.unlink(missing_ok=True)
        raise SttError(f"강의 오디오 추출 실행에 실패했습니다: {e}") from e
    if proc.returncode != 0 or not out_path.is_file() or out_path.stat().st_size == 0:
        lines = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        detail = lines[-1] if lines else f"ffmpeg 종료코드 {proc.returncode}"
        out_path.unlink(missing_ok=True)
        raise SttError(f"강의 오디오 추출에 실패했습니다: {detail[:200]}")
    return out_path


def transcribe_via_worker(path: Path, *, worker_url: str, worker_token: str = "") -> list[dict]:
    """자체 호스팅 faster-whisper 워커로 전사 → [{start, end, text}] (OpenAI 경로와 동일 형태).

    stt-worker/(GPU·faster-whisper)로 **추출한 오디오만** POST한다. OpenAI와 달리 25MB 상한이 없고
    과금이 없다. 반환 형태·예외(SttError)는 transcribe_video와 같아, 호출부는 소스를 모른 채
    동일하게 쓴다. 빈 결과는 SttError — 가짜 성공 금지."""
    url = (worker_url or "").strip().rstrip("/")
    if not url:
        raise SttNotConfiguredError("STT 워커 URL(STT_WORKER_URL)이 설정되지 않았습니다.")
    if not path.is_file():
        raise SttError(f"강의 영상 파일을 찾을 수 없습니다: {path.name}")
    # 영상이 아니라 '오디오만' 보낸다 — _extract_audio 주석 참고(12GB → 수십 MB).
    audio_path = _extract_audio(path)
    try:
        with open(audio_path, "rb") as f:
            resp = httpx.post(
                f"{url}/transcribe",
                params={"language": "ko"},
                headers={"X-Worker-Token": worker_token or ""},
                files={"file": (audio_path.name, f, "audio/flac")},
                timeout=_WORKER_TIMEOUT_SEC,
            )
    except httpx.HTTPError as e:
        raise SttError(f"STT 워커 호출 실패(네트워크): {e}") from e
    finally:
        audio_path.unlink(missing_ok=True)  # 추출본은 항상 치운다(실패해도 남기지 않는다)
    if resp.status_code != 200:
        raise SttError(f"STT 워커 오류(HTTP {resp.status_code}): {resp.text[:300]}")
    body = resp.json()
    segments = body.get("segments")
    if not isinstance(segments, list) or not segments:
        raise SttError("STT 워커 응답에 전사 세그먼트가 없습니다(무음 영상이거나 전사 실패).")
    out: list[dict] = []
    for seg in segments:  # 워커가 정규화해 주지만 방어적으로 재검증
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        try:
            start = max(0.0, float(seg.get("start", 0)))
            end = max(start, float(seg.get("end", start)))
        except (TypeError, ValueError):
            continue
        out.append({"start": round(start, 2), "end": round(end, 2), "text": text})
    if not out:
        raise SttError("STT 워커 전사 결과가 비어 있습니다(유효한 발화 세그먼트 없음).")
    return out


def transcribe_lecture(
    path: Path, *, worker_url: str = "", worker_token: str = "", api_key: str = ""
) -> list[dict]:
    """전사 라우터 — 자체 워커가 설정되면 워커(무료·GPU·자사), 아니면 OpenAI(폴백).

    우선순위: STT_WORKER_URL > OpenAI 키. 둘 다 없으면 SttNotConfiguredError. 반환 형태·예외는
    두 경로가 동일하므로 호출부는 이 함수 하나만 부르면 된다(소스 분기 불필요)."""
    if (worker_url or "").strip():
        return transcribe_via_worker(path, worker_url=worker_url, worker_token=worker_token)
    if (api_key or "").strip():
        return transcribe_video(path, api_key=api_key)
    raise SttNotConfiguredError("STT 워커(STT_WORKER_URL)도 OpenAI 키도 설정되지 않았습니다.")
