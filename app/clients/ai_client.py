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


def _post_messages(key: str, prompt: str, *, max_tokens: int) -> str:
    """Anthropic Messages API 1회 호출 → 응답 텍스트. 실패는 AiGenerationError로 전파.

    생성(generate)과 자기검증(solve)이 공유하는 단일 호출 경로."""
    settings = get_settings()
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
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
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
    return text


def _solve_prompt(
    questions: list[dict],
    *,
    context: dict | None = None,
    transcript: list[dict] | None = None,
) -> str:
    """자기검증(solver)용 프롬프트 — 정답·해설은 항상 숨긴다.

    - context(제목·과목·설명): 실제 공격자가 강의 화면에서 '보는' 공개 정보 — 블라인드
      판정에도 항상 준다(안 주면 검증자가 공격자보다 불리해 판정이 후해진다).
    - transcript 없음 = 블라인드(상식으로 풀리는지 = 봇도 풀 수 있는지).
    - transcript 있음 = 자막 기준으로 풀리는지(못 풀면 문항 자체가 불량이라는 신호)."""
    blocks = []
    for i, q in enumerate(questions):
        opts = "\n".join(f"  {j}) {o}" for j, o in enumerate(q["options"]))
        blocks.append(f"문제{i + 1}: {q['prompt']}\n{opts}")
    ctx = ""
    if context:
        ctx = (
            f"과목: {context.get('subject') or '(미상)'}\n"
            f"강의 제목: {context.get('title') or '(미상)'}\n"
            f"강의 설명: {context.get('description') or '(없음)'}\n\n"
        )
    if transcript is None:
        head = (
            "당신은 아래 강의를 '전혀 보지 않았습니다'. 강의 페이지에서 보이는 공개 정보"
            "(과목·제목·설명)와 문제·보기 텍스트만으로, 일반 상식과 추론을 총동원해 "
            "각 문제의 정답 보기 번호를 고르세요.\n"
            "모르면 가장 그럴듯한 것을 고르되, 반드시 하나를 고르세요.\n\n"
        )
    else:
        lines = "\n".join(f"[{seg['start']:.0f}s~{seg['end']:.0f}s] {seg['text']}" for seg in transcript)
        head = (
            "아래는 어느 강의의 음성 전사(자막)입니다. 자막에 나온 내용을 근거로 "
            "각 문제의 정답 보기 번호를 고르세요. 자막에 근거가 없으면 가장 그럴듯한 것을 "
            "고르되, 반드시 하나를 고르세요.\n\n---\n" + lines + "\n---\n\n"
        )
    return (
        head
        + ctx
        + "\n\n".join(blocks)
        + '\n\n각 문제의 답을 이 JSON 배열로만 출력하세요(코드펜스·설명 없이):\n'
        '[{"q": 1, "answer_index": 0}]'
    )


def solve_questions(
    questions: list[dict],
    *,
    api_key: str | None = None,
    context: dict | None = None,
    transcript: list[dict] | None = None,
) -> list[bool]:
    """solver 1회 호출 — questions 순서대로 '맞혔는지'(bool) 리스트.

    transcript=None이면 블라인드(공개 맥락만), 있으면 자막 기준 풀이.
    파싱 실패로 특정 문항의 답을 못 얻으면 그 문항은 False(미정답 처리).
    키 없으면 AiNotConfiguredError. (다수결·판정 조합은 verify_questions가 담당.)"""
    if not questions:
        return []
    settings = get_settings()
    key = (api_key if api_key is not None else settings.ANTHROPIC_API_KEY or "").strip()
    if not key:
        raise AiNotConfiguredError("LLM API 키(Anthropic)가 설정되지 않았습니다.")

    text = _post_messages(
        key, _solve_prompt(questions, context=context, transcript=transcript), max_tokens=1024
    )
    raw = text.strip()
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    try:
        picks = json.loads(raw)
    except ValueError as e:
        raise AiGenerationError(f"자기검증 응답이 JSON 배열이 아닙니다: {e}") from e
    # {q(1-based): answer_index} 로 정규화 — 순서가 뒤섞이거나 누락돼도 q로 매칭
    by_q: dict[int, int] = {}
    if isinstance(picks, list):
        for p in picks:
            if isinstance(p, dict) and isinstance(p.get("q"), int) and isinstance(p.get("answer_index"), int):
                by_q[p["q"]] = p["answer_index"]
    return [by_q.get(i + 1, -1) == q.get("answer_index") for i, q in enumerate(questions)]


def _shuffled_variants(questions: list[dict], rng) -> list[dict]:
    """보기 순서를 셔플한 사본 — answer_index도 함께 재배치.

    LLM 생성 문항의 위치 편향(정답을 특정 위치·긴 보기에 두는 습관)을 검증자가
    '습관으로' 맞히는 것을 막는다. 원본은 변형하지 않는다."""
    out = []
    for q in questions:
        idxs = list(range(len(q["options"])))
        rng.shuffle(idxs)
        out.append(
            {
                "prompt": q["prompt"],
                "options": [q["options"][i] for i in idxs],
                "answer_index": idxs.index(q["answer_index"]),
            }
        )
    return out


def verify_questions(
    questions: list[dict],
    *,
    api_key: str | None = None,
    context: dict | None = None,
    transcript: list[dict] | None = None,
    trials: int = 3,
) -> list[dict]:
    """자기검증 오케스트레이터 — 문항별 {blind_passed, transcript_passed, verdict}.

    1) 블라인드 solve를 '보기 셔플'로 trials회(기본 3) 돌려 다수결 — 우연 정답(4지선다
       25%)과 위치 편향을 줄인다. 공개 맥락(제목·과목·설명)은 항상 제공(공격자 조건 일치).
    2) 자막이 있으면 자막-포함 solve 1회 — '자막을 줘도 못 푸는' 문항은 불량(환각·모호)
       신호다.
    verdict:
      - 'bank'    = 블라인드로 풀림 → 상식 문제 → 시청 검증(캡차) 부적합, 지식 은행 후보.
      - 'captcha' = 블라인드로 못 풀고 자막으론 풀림 → 강의 의존적·정상 문항(이상적).
      - 'discard' = 블라인드로도 자막으로도 못 풀림 → 불량 의심(폐기 권고).
      - 자막이 없으면 불량 판별 불가 → 못 푼 문항은 'captcha'(종전과 동일, 한계 명시).
    판정은 강사 검수의 참고 신호다(자동 배치 아님)."""
    if not questions:
        return []
    import random

    trials = max(1, min(int(trials), 5))
    blind_hits = [0] * len(questions)
    for t in range(trials):
        rng = random.Random(t * 7919 + len(questions))  # 재현 가능(시드=회차)·회차마다 다른 셔플
        variants = _shuffled_variants(questions, rng)
        result = solve_questions(variants, api_key=api_key, context=context)
        for i, ok in enumerate(result):
            if ok:
                blind_hits[i] += 1
    majority = trials // 2 + 1
    blind_passed = [h >= majority for h in blind_hits]

    transcript_passed: list[bool] | None = None
    if transcript:
        transcript_passed = solve_questions(
            questions, api_key=api_key, context=context, transcript=transcript
        )

    out = []
    for i in range(len(questions)):
        if blind_passed[i]:
            verdict = "bank"
        elif transcript_passed is not None and not transcript_passed[i]:
            verdict = "discard"
        else:
            verdict = "captcha"
        out.append(
            {
                "blind_passed": blind_passed[i],
                "transcript_passed": None if transcript_passed is None else transcript_passed[i],
                "verdict": verdict,
            }
        )
    return out


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
    text = _post_messages(
        key, _prompt(lecture_title, description, subject, n, transcript), max_tokens=8192
    )
    return _parse_questions(text, n)
