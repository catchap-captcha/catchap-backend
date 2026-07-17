"""OpenAI Whisper STT 클라이언트 — 강의 영상 음성을 타임스탬프 있는 자막으로 전사.

LLM 문항 생성의 재료: 전사 세그먼트([start, end, text])가 있어야 "이 대목을 물어라"
(position_sec)와 "이 내용이 시작되는 시점"(content_start_sec)을 기계가 제안할 수 있다.

가짜 성공 금지 규약(ai_client와 동일): 키가 없으면 SttNotConfiguredError를 호출 전에
던지고, 호출/파싱 실패는 SttError로 정직하게 전파한다 — 빈 자막을 성공처럼 반환하지
않는다. 기존 의존성 httpx로 직접 호출한다(신규 SDK 불필요 — ai_client와 같은 이유).
"""

from pathlib import Path

import httpx

_API_URL = "https://api.openai.com/v1/audio/transcriptions"
_MODEL = "whisper-1"  # verbose_json(세그먼트 타임스탬프)을 지원하는 안정 모델
_TIMEOUT_SEC = 300.0  # 수십 분짜리 강의 오디오 전사 여유
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
