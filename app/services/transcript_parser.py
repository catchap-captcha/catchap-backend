"""자막/전사 파서 — 강사가 올린 SRT·VTT·타임스탬프 텍스트를 LLM이 먹는 세그먼트로 변환.

출력: [{start: float초, end: float초, text: str}] (시간순). 이 포맷은 stt_client의
transcribe_video 출력과 동일하다 — ai_client._prompt/_solve_prompt가 그대로 쓴다(그래서
강사 제공 자막이 자동 STT를 '그 자리에서' 대체할 수 있다).

세 입력을 auto로 판별한다:
- SRT/VTT: 'WEBVTT' 헤더나 '-->' 큐 화살표가 있으면 큐 기반으로 파싱.
- 붙여넣기: 그 외에는 줄 기반('[00:12] 내용' / '00:12 내용' / '0:01:30 내용').
형식이 못 알아먹을 만큼 어긋나 세그먼트가 0개면 TranscriptParseError(빈 자막을 성공처럼
반환하지 않는다 — stt_client와 같은 정직성 규약).
"""

import re

# 비정상 대용량 붙여넣기 방어 상한(1시간 강의도 보통 수백 세그먼트)
MAX_SEGMENTS = 5000


class TranscriptParseError(Exception):
    """자막 파싱 실패 — 원인을 담아 정직하게 전파한다."""


# [HH:]MM:SS[.,mmm] — 시간 토큰 하나
_TS_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?$")
# 큐 화살표: 00:00:01,000 --> 00:00:04,000 (SRT=콤마·VTT=점, VTT는 뒤에 위치설정이 붙기도)
_CUE_RE = re.compile(
    r"(?P<a>(?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)\s*-->\s*"
    r"(?P<b>(?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)"
)
# 줄 앞 타임스탬프: [00:12] 내용 / 00:12 내용 / 0:01:30 - 내용
_LINE_RE = re.compile(
    r"^\s*\[?\s*((?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)\s*\]?[\s\-:]*(.*)$"
)


def _to_sec(token: str) -> float | None:
    m = _TS_RE.match((token or "").strip())
    if not m:
        return None
    h, mm, ss, ms = m.groups()
    return int(h or 0) * 3600 + int(mm) * 60 + int(ss) + int((ms or "0").ljust(3, "0")[:3]) / 1000


def _clean(body: str) -> str:
    # VTT 인라인 태그(<v Roger>, <00:00:01.000>, <c> 등) 제거
    return re.sub(r"<[^>]+>", "", body).strip()


def _parse_cues(text: str) -> list[dict]:
    out: list[dict] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        cue_i = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if cue_i is None:
            continue  # WEBVTT 헤더·NOTE·번호만 있는 블록 건너뜀
        mt = _CUE_RE.search(lines[cue_i])
        if not mt:
            continue
        start = _to_sec(mt.group("a"))
        end = _to_sec(mt.group("b"))
        if start is None or end is None:
            continue
        body = _clean(" ".join(lines[cue_i + 1:]))
        if body:
            out.append({"start": round(max(0.0, start), 2), "end": round(max(start, end), 2), "text": body})
    return out


def _parse_lines(text: str) -> list[dict]:
    raw: list[tuple[float, str]] = []
    for ln in text.splitlines():
        m = _LINE_RE.match(ln)
        if not m:
            continue
        start = _to_sec(m.group(1))
        body = _clean(m.group(2))
        if start is None or not body:
            continue
        raw.append((start, body))
    out: list[dict] = []
    for i, (start, body) in enumerate(raw):
        end = raw[i + 1][0] if i + 1 < len(raw) else start + 4.0  # 끝 시각 = 다음 시작(마지막은 +4s)
        out.append({"start": round(max(0.0, start), 2), "end": round(max(start, end), 2), "text": body})
    return out


def parse_transcript(content: str, fmt: str = "auto") -> list[dict]:
    """자막 텍스트 → [{start, end, text}] (시간순). 세그먼트 0개면 TranscriptParseError."""
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise TranscriptParseError("빈 자막입니다.")
    fmt = (fmt or "auto").lower()
    if fmt == "auto":
        fmt = "cue" if ("-->" in text or text.lstrip().upper().startswith("WEBVTT")) else "line"

    if fmt in ("srt", "vtt", "cue"):
        segs = _parse_cues(text)
        if not segs:  # 형식 오판 방어 — 큐로 0개면 줄 기반 재시도
            segs = _parse_lines(text)
    elif fmt in ("line", "paste"):
        segs = _parse_lines(text)
    else:
        raise TranscriptParseError(f"지원하지 않는 자막 형식입니다: {fmt}")

    if not segs:
        raise TranscriptParseError(
            "자막에서 '타임스탬프 + 내용'을 하나도 찾지 못했어요. SRT/VTT 파일이거나 "
            "'00:12 내용'처럼 줄마다 시각으로 시작하는 형식이어야 해요."
        )
    segs.sort(key=lambda s: s["start"])
    if len(segs) > MAX_SEGMENTS:
        raise TranscriptParseError(f"세그먼트가 너무 많아요({len(segs)}개 > 상한 {MAX_SEGMENTS}).")
    return segs
