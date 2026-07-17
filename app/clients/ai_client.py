"""Anthropic Messages API 클라이언트 — 강의 확인 문항 자동 생성(LLM).

가짜 성공 금지: ANTHROPIC_API_KEY가 비어 있으면 호출 전에 AiNotConfiguredError를 던진다
(stub 문항을 만들어 성공처럼 반환하지 않는다). 응답 파싱 실패도 정직한 예외로 전파한다.
기존 의존성 httpx로 직접 호출한다(신규 SDK 불필요).
"""

import json
import re

import httpx

from app.core.config import get_settings

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_TIMEOUT_SEC = 120.0


class AiNotConfiguredError(Exception):
    """LLM API 키 미설정 — 자동 생성 기능을 쓸 수 없다(호출 전에 발생)."""


class AiGenerationError(Exception):
    """LLM 호출/파싱 실패 — 원인을 담아 정직하게 전파한다."""


def _prompt(
    lecture_title: str,
    description: str | None,
    subject: str,
    n: int,
    transcript: list[dict] | None,
) -> str:
    head = (
        "당신은 초등학생용 강의 영상의 '시청 확인 문제' 출제자입니다.\n"
        f"과목: {subject}\n강의 제목: {lecture_title}\n"
        f"강의 설명: {description or '(없음)'}\n"
    )
    rules = (
        "규칙:\n"
        "- 초등학생이 이해할 수 있는 쉬운 한국어 문장.\n"
        "- 각 문제는 보기 4개, 정답은 하나.\n"
        "- explain에 정답 해설 1~2문장.\n"
    )
    if not transcript:
        return (
            head
            + f"\n이 강의 내용을 실제로 본 학생만 맞힐 수 있는 4지선다 확인 문제 {n}개를 만드세요.\n"
            + rules
            + "\n다음 JSON 배열만 출력하세요(코드펜스·설명 없이):\n"
            '[{"prompt": "질문", "options": ["보기1", "보기2", "보기3", "보기4"], '
            '"answer_index": 0, "explain": "해설"}]'
        )
    # 전사 기반 — 자막의 실제 발화만 근거로 삼고, 출제 시점(그 대목 설명이 끝난 직후)과
    # 내용 시작 시점(오답 3회 시 되감을 지점)까지 함께 제안하게 한다. 시점은 초 단위 정수.
    lines = "\n".join(f"[{seg['start']:.0f}s~{seg['end']:.0f}s] {seg['text']}" for seg in transcript)
    return (
        head
        + "\n아래는 이 강의의 음성 전사(자막)입니다. 각 줄 앞의 [시작~끝]은 초 단위 시점입니다.\n"
        "---\n" + lines + "\n---\n\n"
        f"전사에 실제로 나온 내용만 근거로, 강의를 본 학생만 맞힐 수 있는 4지선다 확인 문제 {n}개를 만드세요.\n"
        + rules
        + "- 전사에 없는 내용을 지어내지 마세요(상식으로 풀리는 문제 금지).\n"
        "- position_sec: 이 문제를 낼 시점(초, 정수) — 그 내용 설명이 '끝난 직후'의 자막 시점.\n"
        "- content_start_sec: 그 내용 설명이 '시작되는' 자막 시점(초, 정수) — 반드시 position_sec보다 앞.\n"
        "- 서로 다른 문제는 서로 다른 대목에서 내고, position_sec이 겹치지 않게 하세요.\n\n"
        "다음 JSON 배열만 출력하세요(코드펜스·설명 없이):\n"
        '[{"prompt": "질문", "options": ["보기1", "보기2", "보기3", "보기4"], '
        '"answer_index": 0, "explain": "해설", "position_sec": 45, "content_start_sec": 12}]'
    )


def _parse_questions(text: str, n: int) -> list[dict]:
    """모델 응답 텍스트 → 문항 리스트. 형식이 어긋나면 AiGenerationError(폴백 생성 금지)."""
    raw = text.strip()
    # 지시를 어기고 코드펜스로 감싼 경우만 벗겨낸다 — 그 외 가공은 하지 않는다
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise AiGenerationError(f"LLM 응답이 JSON 배열이 아닙니다: {e}") from e
    if not isinstance(data, list) or not data:
        raise AiGenerationError("LLM 응답 JSON이 비어 있거나 배열이 아닙니다.")

    out: list[dict] = []
    for i, item in enumerate(data[:n]):
        if not isinstance(item, dict):
            raise AiGenerationError(f"{i + 1}번째 항목이 객체가 아닙니다.")
        prompt = item.get("prompt")
        options = item.get("options")
        answer_index = item.get("answer_index")
        if not isinstance(prompt, str) or not prompt.strip():
            raise AiGenerationError(f"{i + 1}번째 항목의 prompt가 비어 있습니다.")
        if (
            not isinstance(options, list)
            or not (2 <= len(options) <= 6)
            or not all(isinstance(o, str) and o.strip() for o in options)
        ):
            raise AiGenerationError(f"{i + 1}번째 항목의 options가 올바르지 않습니다.")
        if not isinstance(answer_index, int) or not (0 <= answer_index < len(options)):
            raise AiGenerationError(f"{i + 1}번째 항목의 answer_index가 보기 범위를 벗어납니다.")
        explain = item.get("explain")
        parsed = {
            "prompt": prompt.strip(),
            "options": [o.strip() for o in options],
            "answer_index": answer_index,
            "explain": explain.strip() if isinstance(explain, str) else "",
        }
        # 시점 제안(전사 기반 프롬프트일 때만 옴) — 형식이 어긋난 시점은 문항 전체를
        # 버리지 않고 시점만 버린다(시점 미배치 draft로 저장돼 운영자가 지정).
        pos = item.get("position_sec")
        if isinstance(pos, (int, float)) and int(pos) >= 1:
            parsed["position_sec"] = int(pos)
            cs = item.get("content_start_sec")
            if isinstance(cs, (int, float)) and 0 <= int(cs) < int(pos):
                parsed["content_start_sec"] = int(cs)
        out.append(parsed)
    if not out:
        raise AiGenerationError("LLM이 유효한 문항을 생성하지 못했습니다.")
    return out


def generate_lecture_questions(
    *,
    lecture_title: str,
    description: str | None,
    subject: str,
    n: int = 5,
    api_key: str | None = None,
    transcript: list[dict] | None = None,
) -> list[dict]:
    """강의 메타(+전사)에서 확인 문항 n개 생성.

    반환 [{prompt, options, answer_index, explain[, position_sec, content_start_sec]}] —
    시점 필드는 전사가 있을 때만 제안된다(초 단위, content_start < position 보장).
    api_key는 호출자가 해석해 넘긴다(운영 콘솔 입력(DB) → .env 폴백 —
    settings_service.resolve_anthropic_key). None이면 .env만 본다(하위호환).
    키가 없으면 AiNotConfiguredError, 호출/파싱 실패는 AiGenerationError — 어떤 경우에도
    stub 문항을 지어내 반환하지 않는다."""
    settings = get_settings()
    key = (api_key if api_key is not None else settings.ANTHROPIC_API_KEY or "").strip()
    if not key:
        raise AiNotConfiguredError("LLM API 키(Anthropic)가 설정되지 않았습니다.")

    n = max(1, min(int(n), 20))
    try:
        resp = httpx.post(
            _API_URL,
            headers={
                "x-api-key": key,
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": settings.LLM_MODEL,
                "max_tokens": 8192,
                "messages": [
                    {
                        "role": "user",
                        "content": _prompt(lecture_title, description, subject, n, transcript),
                    }
                ],
            },
            timeout=_TIMEOUT_SEC,
        )
    except httpx.HTTPError as e:
        raise AiGenerationError(f"LLM API 호출 실패(네트워크): {e}") from e
    if resp.status_code != 200:
        raise AiGenerationError(f"LLM API 오류(HTTP {resp.status_code}): {resp.text[:300]}")

    body = resp.json()
    if body.get("stop_reason") == "refusal":
        raise AiGenerationError("LLM이 요청을 거절했습니다(stop_reason=refusal).")
    text = "".join(
        block.get("text", "")
        for block in body.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )
    if not text.strip():
        raise AiGenerationError("LLM 응답에 텍스트 블록이 없습니다.")
    return _parse_questions(text, n)
