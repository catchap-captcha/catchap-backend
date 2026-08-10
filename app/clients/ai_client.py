"""LLM 클라이언트(Anthropic·OpenAI) — 강의 확인 문항 자동 생성/자기검증.

운영자가 슬롯에 고른 모델의 provider에 따라 실제 API를 가른다(#26 다음 단계): Anthropic
Messages API(기본·폴백) 또는 OpenAI Chat Completions(GPT 계열). provider 판별은 _is_openai.

가짜 성공 금지: 키가 하나도 없으면 호출 전에 AiNotConfiguredError를 던진다(stub 문항을 만들어
성공처럼 반환하지 않는다). 응답 파싱 실패도 정직한 예외로 전파한다. 기존 의존성 httpx로
직접 호출한다(신규 SDK 불필요).
"""

import json
import random
import re

import httpx

from app.core.config import get_settings

_API_URL = "https://api.anthropic.com/v1/messages"
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_API_VERSION = "2023-06-01"
_TIMEOUT_SEC = 120.0


class AiNotConfiguredError(Exception):
    """LLM API 키 미설정 — 자동 생성 기능을 쓸 수 없다(호출 전에 발생)."""


class AiGenerationError(Exception):
    """LLM 호출/파싱 실패 — 원인을 담아 정직하게 전파한다."""


# 문항 생성 '출제 규칙' 기본값 — 운영자가 콘솔에서 수정하면 이 자리를 대체한다(llm_gen_rules).
# 구조부(JSON 형식·변수 주입·시점 지침)는 아래 _prompt에 고정 — 규칙만 편집 가능(파서 보호).
DEFAULT_GEN_RULES = (
    "규칙:\n"
    "- 강의 수준에 맞는 명확한 한국어 문장(그 강의를 본 사람이 이해할 수 있게).\n"
    # ★확인 문항(시청 검증) 수율의 핵심(2026-07-22 강화): 실측상 '강의에서 설명한 ~는?' 류
    # 회상 문항은 강한 봇이 도메인 상식으로 blind 풀어 전부 bank로 빠졌다. 정답이 '이 강의에서만
    # 나온 고유값'에 걸리고 오답도 정답만큼 그럴듯해야 소거법·상식 유추를 막아 captcha가 된다.
    "- ★정답은 '이 강의에서만 알 수 있는 구체적 사실 하나'에만 근거하게 하라 — 강사가 화면에 "
    "보여주거나 말한 정확한 값·이름·라벨·수치·설정/명령 문자열·순서·특정 예시처럼, 자막의 한 대목을 "
    "보지 않으면 알 수 없는 것. 일반 상식·도메인 지식·교과서 개념으로 유추되는 답은 금지한다.\n"
    "- ★가능하면 '옳은 설명 고르기'보다 강사가 말한 '정확한 임의값'(수치·개수·정확한 문자열·라벨·"
    "순서)을 물어라 — 추론·상식으로 유도되지 않는(무엇이든 될 수 있는) 답일수록 시청 검증에 좋다.\n"
    "- ★질문 문장에 정답을 유추할 단서를 넣지 마라 — 정답과 같은 단어·표현·의미를 질문에 쓰면 "
    "강의를 안 보고도 '의미 매칭'으로 찍힌다(예: 질문에 '조직 역할 할당'을 쓰고 정답이 '오거나이제이션 "
    "롤 어사인'이면 유출). 질문은 '무엇을/어떤 값을'만 묻고, 판별 정보는 보기에만 두라.\n"
    "- ★스스로 검열하라: '이 강의를 안 본 똑똑한 사람이 상식이나 소거법으로 정답을 고를 수 있나?' "
    "그렇다면 그 문제는 버리고, 강의 고유값에 걸리도록 다시 만들어라.\n"
    "- ★오답 4개를 '경쟁력 있게' 만들어라 — 정답과 같은 범주·비슷한 길이·비슷한 구체성으로 모두 "
    "그럴듯하게. 오답은 '그 강의에서 말했을 법하지만 실제로는 안 말한' 근접값으로 채워라. 정답만 "
    "유독 길거나·상세하거나·유일하게 말 되는 보기는 만들지 마라(그러면 안 보고도 찍힌다).\n"
    "- 각 문제는 보기 4개, 정답은 하나.\n"
    "- explain에 정답 해설 1~2문장(강의의 어느 대목에 근거하는지 밝혀라).\n"
)

# 자기검증(solver) '판정 지침' 기본값 — 운영자가 콘솔에서 수정하면 이 자리를 대체한다
# (llm_verify_rules). 구조부(무엇을 근거로 푸는가=블라인드/자막·JSON 출력 형식)는 _solve_prompt에
# 고정하고, 여기 '판정 태도'만 편집 가능하다(생성 프롬프트 DEFAULT_GEN_RULES와 대칭).
# 왜 태도만 여나: 근거 소스(블라인드 vs 자막)와 출력 JSON을 바꾸면 봇저항 판정 로직·파서가
# 깨진다. 반면 '얼마나 엄격히 볼지'는 운영자가 조절해도 안전하다.
# ★주의: 블라인드 경로는 '상식으로 풀리는지'를 봐야 하므로 배경지식 사용을 막으면 안 된다
# (막으면 상식 문제가 bank 대신 captcha로 오분류). 기본값은 head가 이미 지정한 근거(블라인드=
# 상식·자막=전사)에 중립적인 '반드시 하나 고르기'만 둔다 — 종전 head 말미 문구를 여기로 옮긴 것.
DEFAULT_VERIFY_RULES = "판정 지침:\n- 확신이 없어도 가장 그럴듯한 보기를 반드시 하나 고르세요(빈 답 금지).\n"


def _prompt(
    lecture_title: str,
    description: str | None,
    subject: str,
    n: int,
    transcript: list[dict] | None,
    rules_override: str | None = None,
    avoid_positions: list[int] | None = None,
) -> str:
    head = (
        # 대상 중립화(2026-07-20): 옛 아동 제품 잔재('초등학생용') 제거 — 성인 인강으로 전환됐고,
        # 시청검증의 본질은 '그 강의를 실제로 본 사람'만 풀게 하는 것이라 특정 연령을 못박지 않는다.
        "당신은 강의 영상의 '시청 확인 문제' 출제자입니다.\n"
        f"과목: {subject}\n강의 제목: {lecture_title}\n"
        f"강의 설명: {description or '(없음)'}\n"
    )
    # 운영자가 콘솔에서 출제 규칙을 바꿨으면 그것을, 아니면 기본값을 쓴다. 끝에 개행 보장.
    over = (rules_override or "").strip()
    rules = (over + "\n") if over else DEFAULT_GEN_RULES
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
    # 배치 생성(큰 n)에서 이미 낸 대목을 알려 겹침을 줄인다. 없으면(1회 생성) 아무 것도 덧붙이지 않는다.
    avoid_note = ""
    if avoid_positions:
        secs = ", ".join(f"{int(s)}s" for s in list(avoid_positions)[:80])
        avoid_note = f"- 이미 다음 시점 부근에서 문제를 냈어요 — 그 대목은 피하고 아직 다루지 않은 다른 구간에서 내세요: {secs}\n"
    return (
        head
        + "\n아래는 이 강의의 음성 전사(자막)입니다. 각 줄 앞의 [시작~끝]은 초 단위 시점입니다.\n"
        "---\n" + lines + "\n---\n\n"
        f"전사에 실제로 나온 내용만 근거로, 강의를 본 학생만 맞힐 수 있는 4지선다 확인 문제 {n}개를 만드세요.\n"
        + rules
        + "- 전사에 없는 내용을 지어내지 마세요(상식으로 풀리는 문제 금지).\n"
        "- position_sec: 이 문제를 낼 시점(초, 정수) — 그 내용 설명이 '끝난 직후'의 자막 시점.\n"
        "- content_start_sec: 그 내용 설명이 '시작되는' 자막 시점(초, 정수) — 반드시 position_sec보다 앞.\n"
        "- 서로 다른 문제는 서로 다른 대목에서 내고, position_sec이 겹치지 않게 하세요.\n"
        + avoid_note
        + "\n다음 JSON 배열만 출력하세요(코드펜스·설명 없이):\n"
        '[{"prompt": "질문", "options": ["보기1", "보기2", "보기3", "보기4"], '
        '"answer_index": 0, "explain": "해설", "position_sec": 45, "content_start_sec": 12}]'
    )


def _is_openai(provider: str | None) -> bool:
    """provider 라벨이 OpenAI 계열인가 — 실제 호출 API를 가른다(그 외는 Anthropic)."""
    return "openai" in (provider or "").strip().lower()


def _anthropic_request(key: str, model_id: str, prompt: str, max_tokens: int):
    return httpx.post(
        _API_URL,
        headers={
            "x-api-key": key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        },
        json={
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=_TIMEOUT_SEC,
    )


def _openai_request(key: str, model_id: str, prompt: str, max_tokens: int):
    # OpenAI Chat Completions. 신형 모델(gpt-5·o계열)은 max_tokens를 거부하고
    # max_completion_tokens를 요구하므로 후자를 쓴다(구형도 대개 수용). 호환 안 되면
    # 제공사 오류가 정직하게 드러나고 자동 스왑이 다음 후보로 넘긴다.
    return httpx.post(
        _OPENAI_URL,
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
        json={
            "model": model_id,
            "max_completion_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=_TIMEOUT_SEC,
    )


def test_key(provider: str | None, key: str) -> tuple[bool, str]:
    """API 키 유효성만 가볍게 확인 — 모델 목록 조회로 '인증'만 검사한다(문항 생성 X, 과금 최소).
    반환: (성공?, 사람이 읽는 상세). 잘못된 키를 저장 시점에 바로 잡기 위한 실무용(연결 테스트)."""
    key = (key or "").strip()
    if not key:
        return False, "키가 설정돼 있지 않아요."
    try:
        if _is_openai(provider):
            r = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=_TIMEOUT_SEC,
            )
        else:
            r = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": key, "anthropic-version": _API_VERSION},
                timeout=_TIMEOUT_SEC,
            )
    except httpx.HTTPError as e:
        return False, f"제공사에 연결하지 못했어요: {type(e).__name__}"
    if r.status_code == 200:
        return True, "연결 성공 — 키가 유효해요."
    if r.status_code in (401, 403):
        return False, f"키가 유효하지 않아요(인증 실패 {r.status_code}). 키를 다시 확인하세요."
    if r.status_code == 429:
        return False, "키는 유효하지만 지금 사용량 한도(429)에 걸렸어요."
    detail = ""
    try:
        detail = str((r.json().get("error") or {}).get("message", ""))[:140]
    except Exception:
        detail = (r.text or "")[:140]
    return False, f"확인 실패({r.status_code}){': ' + detail if detail else ''}."


def _anthropic_extract(body: dict) -> tuple[str, int, int, bool, bool]:
    """Anthropic 응답 → (text, tokens_in, tokens_out, refused, truncated).

    truncated = 모델이 max_tokens에 걸려 도중에 끊긴 것. ★thinking을 쓰는 모델은 그
    토큰도 max_tokens를 함께 소비하므로, 예산이 빠듯하면 본문(text)이 중간에 잘린다.
    잘린 JSON을 정상처럼 돌려주면 호출부가 파싱에서 터지고 원인이 안 보인다(2026-08-06)."""
    usage = body.get("usage") or {}
    text = "".join(
        block.get("text", "")
        for block in body.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )
    return (
        text,
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
        body.get("stop_reason") == "refusal",
        body.get("stop_reason") == "max_tokens",
    )


def _openai_extract(body: dict) -> tuple[str, int, int, bool, bool]:
    """OpenAI Chat Completions 응답 → (text, tokens_in, tokens_out, refused, truncated)."""
    usage = body.get("usage") or {}
    choices = body.get("choices") or []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    msg = first.get("message") or {}
    refused = bool(msg.get("refusal")) or first.get("finish_reason") == "content_filter"
    return (
        str(msg.get("content") or ""),
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
        refused,
        first.get("finish_reason") == "length",  # 예산 초과로 도중에 끊김
    )


def _post_messages(
    key: str,
    prompt: str,
    *,
    max_tokens: int,
    models: list[dict] | None = None,
    on_usage=None,
    openai_key: str | None = None,
) -> str:
    """LLM 호출 → 응답 텍스트. provider별로 API를 가른다. 실패는 AiGenerationError로 전파.

    생성(generate)과 자기검증(solve)이 공유하는 단일 호출 경로.

    models = 운영자가 고른 후보 목록 [{"config_id", "model_id", "provider"}] (우선순위 순).
    provider=OpenAI면 OpenAI Chat Completions(openai_key), 그 외는 Anthropic Messages(key).
    None/빈 목록이면 .env LLM_MODEL(Anthropic) 단일 시도(하위호환).

    **자동 스왑**: 앞 후보가 네트워크 오류·비200(429·529·400 등)·비JSON 200·해당 provider 키
    미설정이면 다음 후보로 넘어간다(가용성 문제). 단, 200을 받았는데 거절/빈 응답이면
    스왑하지 않는다 — 모델 가용성이 아니라 요청 내용 문제라 다른 모델도 같을 가능성이 크다.

    on_usage(config_id, tokens_in, tokens_out): 200을 받을 때마다 호출(토큰 누적 기록용)."""
    settings = get_settings()
    attempts = models or [
        {"config_id": None, "model_id": settings.LLM_MODEL, "provider": "anthropic"}
    ]
    last_err: Exception | None = None
    for cand in attempts:
        is_oa = _is_openai(cand.get("provider"))
        model_id = (cand.get("model_id") or settings.LLM_MODEL).strip()
        prov_key = ((openai_key if is_oa else key) or "").strip()
        if not prov_key:
            # 이 후보 provider의 키가 없음 — 못 쓰는 모델이니 다음 후보로(스왑)
            last_err = AiGenerationError(
                f"{'OpenAI' if is_oa else 'Anthropic'} API 키가 없어 모델을 쓸 수 없습니다({model_id})."
            )
            continue
        try:
            resp = (_openai_request if is_oa else _anthropic_request)(
                prov_key, model_id, prompt, max_tokens
            )
        except httpx.HTTPError as e:
            last_err = AiGenerationError(f"LLM API 호출 실패(네트워크): {e}")
            continue  # 자동 스왑 — 다음 후보
        if resp.status_code != 200:
            last_err = AiGenerationError(f"LLM API 오류(HTTP {resp.status_code}): {resp.text[:300]}")
            continue  # 자동 스왑 — 다음 후보
        try:
            body = resp.json()
        except ValueError as e:
            # 200인데 본문이 JSON이 아님(프록시·CDN이 200으로 HTML 반환 등) — 원시 ValueError를
            # 누수하면 500 + 자동 스왑 무력화. 비200과 동일하게 다음 후보로 넘긴다.
            last_err = AiGenerationError(f"LLM 응답이 JSON이 아닙니다(HTTP 200): {e}")
            continue  # 자동 스왑 — 다음 후보
        if not isinstance(body, dict):
            last_err = AiGenerationError("LLM 응답이 JSON 객체가 아닙니다(HTTP 200).")
            continue
        text, tin, tout, refused, truncated = (
            _openai_extract if is_oa else _anthropic_extract
        )(body)
        # 토큰 사용량 기록(성공 호출) — 거절/빈 응답이어도 입력 토큰은 소비됐다
        if on_usage is not None:
            on_usage(cand.get("config_id"), tin, tout)
        if refused:
            raise AiGenerationError("LLM이 요청을 거절했습니다.")
        if truncated:
            # ★잘린 응답은 '요청 내용 문제'가 아니라 이 모델의 예산 문제다 → 다음 후보로 스왑.
            # 잘린 JSON을 그대로 돌려주면 호출부가 파싱에서 터지고 원인이 가려진다.
            # (thinking을 쓰는 모델은 thinking 토큰도 max_tokens를 함께 먹는다 — 2026-08-06
            #  sonnet-5가 1024 중 763~1002를 thinking에 써 답변이 중간에 끊겼다.)
            last_err = AiGenerationError(
                f"LLM 응답이 max_tokens에 걸려 잘렸습니다({model_id}, 출력 {tout}토큰)."
            )
            continue
        if not text.strip():
            # 텍스트가 비는 것도 대개 예산 문제(thinking이 전부 먹음)라 다음 후보를 시도한다.
            last_err = AiGenerationError(f"LLM 응답에 텍스트가 없습니다({model_id}).")
            continue
        return text
    # 모든 후보 실패 — 마지막 오류를 정직하게 전파
    raise last_err or AiGenerationError("LLM 호출 가능한 모델이 없습니다.")


def _solve_prompt(
    questions: list[dict],
    *,
    context: dict | None = None,
    transcript: list[dict] | None = None,
    rules_override: str | None = None,
) -> str:
    """자기검증(solver)용 프롬프트 — 정답·해설은 항상 숨긴다.

    - context(제목·과목·설명): 실제 공격자가 강의 화면에서 '보는' 공개 정보 — 블라인드
      판정에도 항상 준다(안 주면 검증자가 공격자보다 불리해 판정이 후해진다).
    - transcript 없음 = 블라인드(상식으로 풀리는지 = 봇도 풀 수 있는지).
    - transcript 있음 = 자막 기준으로 풀리는지(못 풀면 문항 자체가 불량이라는 신호).
    - rules_override: 운영자가 콘솔에서 바꾼 '판정 지침'(비었으면 DEFAULT_VERIFY_RULES).
      근거 소스(블라인드/자막)와 JSON 출력 형식은 여기 고정 — 판정 태도만 대체된다."""
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
    # 운영자가 판정 지침을 바꿨으면 그것을, 아니면 기본값을 쓴다(생성 _prompt와 동일 규약).
    over = (rules_override or "").strip()
    rules = (over + "\n\n") if over else (DEFAULT_VERIFY_RULES + "\n")
    if transcript is None:
        head = (
            "당신은 아래 강의를 '전혀 보지 않았습니다'. 강의 페이지에서 보이는 공개 정보"
            "(과목·제목·설명)와 문제·보기 텍스트만으로, 일반 상식과 추론을 총동원해 "
            "각 문제의 정답 보기 번호를 고르세요.\n"
        )
    else:
        lines = "\n".join(f"[{seg['start']:.0f}s~{seg['end']:.0f}s] {seg['text']}" for seg in transcript)
        head = (
            "아래는 어느 강의의 음성 전사(자막)입니다. 자막에 나온 내용을 근거로 "
            "각 문제의 정답 보기 번호를 고르세요.\n\n---\n" + lines + "\n---\n\n"
        )
    return (
        head
        + rules
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
    models: list[dict] | None = None,
    on_usage=None,
    openai_key: str | None = None,
    rules_override: str | None = None,
) -> list[bool]:
    """solver 1회 호출 — questions 순서대로 '맞혔는지'(bool) 리스트.

    transcript=None이면 블라인드(공개 맥락만), 있으면 자막 기준 풀이.
    파싱 실패로 특정 문항의 답을 못 얻으면 그 문항은 False(미정답 처리).
    키가 하나도(Anthropic·OpenAI) 없으면 AiNotConfiguredError. (다수결·판정 조합은
    verify_questions가 담당.) models/on_usage/openai_key는 _post_messages로 그대로 넘긴다
    (검증 슬롯 모델·provider별 호출·자동 스왑·토큰 기록)."""
    if not questions:
        return []
    settings = get_settings()
    key = (api_key if api_key is not None else settings.ANTHROPIC_API_KEY or "").strip()
    oa = (openai_key or "").strip()
    if not key and not oa:
        raise AiNotConfiguredError("LLM API 키가 설정되지 않았습니다.")

    # ★max_tokens 산정: 답변 자체는 문항당 ~18토큰(20문항 ≈ 360)이면 충분하지만, 검증 슬롯
    # 모델이 thinking을 쓰면 그 토큰도 같은 예산을 먹는다(실측 763~1289). 종전 1024 고정은
    # thinking이 예산을 다 써 JSON이 중간에 잘렸다. max_tokens는 '상한'이라 실제 과금은 출력한
    # 만큼만 되므로 넉넉히 잡는 편이 안전하다.
    solve_max_tokens = 4096 + 64 * len(questions)
    text = _post_messages(
        key,
        _solve_prompt(questions, context=context, transcript=transcript, rules_override=rules_override),
        max_tokens=solve_max_tokens,
        models=models,
        on_usage=on_usage,
        openai_key=oa,
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
    models: list[dict] | None = None,
    on_usage=None,
    openai_key: str | None = None,
    rules_override: str | None = None,
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
        result = solve_questions(
            variants, api_key=api_key, context=context, models=models,
            on_usage=on_usage, openai_key=openai_key, rules_override=rules_override,
        )
        for i, ok in enumerate(result):
            if ok:
                blind_hits[i] += 1
    majority = trials // 2 + 1
    blind_passed = [h >= majority for h in blind_hits]

    transcript_passed: list[bool] | None = None
    if transcript:
        transcript_passed = solve_questions(
            questions,
            api_key=api_key,
            context=context,
            transcript=transcript,
            models=models,
            on_usage=on_usage,
            openai_key=openai_key,
            rules_override=rules_override,
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
        # ★정답 위치 무작위화 — LLM이 정답을 자꾸 1번(answer_index=0)에 두는 편향을 서버에서 없앤다
        #   (프롬프트로 '고르게 흩어라' 지시해도 편중이 남았다). 보기 순서만 섞고 정답의 새 위치를
        #   다시 찾는다(보기·정답 '내용'은 그대로 — 해설은 위치가 아니라 내용을 근거로 하니 안 깨진다).
        correct_opt = parsed["options"][answer_index]
        random.shuffle(parsed["options"])
        parsed["answer_index"] = parsed["options"].index(correct_opt)
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
    models: list[dict] | None = None,
    on_usage=None,
    openai_key: str | None = None,
    rules_override: str | None = None,
    should_cancel=None,
    is_duplicate=None,
) -> list[dict]:
    """강의 메타(+전사)에서 확인 문항 n개 생성.

    반환 [{prompt, options, answer_index, explain[, position_sec, content_start_sec]}] —
    시점 필드는 전사가 있을 때만 제안된다(초 단위, content_start < position 보장).
    api_key는 호출자가 해석해 넘긴다(운영 콘솔 입력(DB) → .env 폴백 —
    settings_service.resolve_anthropic_key). None이면 .env만 본다(하위호환).
    키가 없으면 AiNotConfiguredError, 호출/파싱 실패는 AiGenerationError — 어떤 경우에도
    stub 문항을 지어내 반환하지 않는다.

    is_duplicate(prompt, kept_prompts)->bool: 채택 전 중복 판정 훅. 호출부가 기존 강의 문항·
    근접중복(difflib) 기준을 주입해, 리필이 '진짜 새 문항'으로만 채워지게 한다. None이면
    정확일치 폴백(하위호환)."""
    settings = get_settings()
    key = (api_key if api_key is not None else settings.ANTHROPIC_API_KEY or "").strip()
    oa = (openai_key or "").strip()
    if not key and not oa:
        raise AiNotConfiguredError("LLM API 키가 설정되지 않았습니다.")

    n = max(1, min(int(n), 100))
    # 한 배치를 작게(12개) 잡고, 요청 개수(n)에 못 미치면 '부족분'을 다시 요청해 채운다(리필).
    # 왜 작게: 한 번에 많이 뽑으면 max_tokens에 걸려 JSON이 잘리기 쉽고(→ 통째 실패), 품질도
    # 떨어진다. 왜 리필: 짧은 강의는 모델이 스스로 적게 반환하는데, 종전엔 한 번 부족하면 그대로
    # 끝나 '20개 요청 → 12개'가 됐다. 이미 낸 대목(position_sec)과 이미 채택한 문항(is_duplicate)을
    # 다음 배치에 알려 겹침·중복을 줄인다. 한 라운드가 새 문항을 하나도 못 보태면 멈춘다 —
    # 내용이 허용하는 만큼만 만들고(중복 남발 금지), 호출부가 '요청 n개 중 몇 개'를 정직히 안내한다.
    # should_cancel()은 배치 사이에 호출된다 — '생성 중지'를 누르면 여기서 예외로 멈춘다.
    BATCH = 12

    def _default_dup(prompt: str, kept: list[str]) -> bool:
        return prompt in kept  # 하위호환: 훅이 없으면 정확일치만 거른다

    is_dup = is_duplicate or _default_dup
    collected: list[dict] = []
    kept_prompts: list[str] = []
    max_attempts = (n + BATCH - 1) // BATCH + 5  # 리필 여유 + 무한루프 안전 상한(실 종료는 added==0)
    for _ in range(max_attempts):
        if len(collected) >= n:
            break
        if should_cancel is not None:
            should_cancel()
        want = min(BATCH, n - len(collected))
        avoid = [q["position_sec"] for q in collected if q.get("position_sec") is not None]
        text = _post_messages(
            key,
            _prompt(lecture_title, description, subject, want, transcript, rules_override, avoid),
            max_tokens=8192,
            models=models,
            on_usage=on_usage,
            openai_key=oa,
        )
        added = 0
        for q in _parse_questions(text, want):
            p = (q.get("prompt") or "").strip()
            if p and not is_dup(p, kept_prompts):
                kept_prompts.append(p)
                collected.append(q)
                added += 1
        if added == 0:  # 새 문항을 못 보탬(짧은 강의 등) — 무한 반복·중복 누적 방지
            break
    return collected[:n]


def _course_exam_prompt(course_title: str, subject: str, lectures: list[dict], n: int) -> str:
    # 강의 전사(자막)가 있으면 그 '실제 내용'을 근거로 출제(제목·설명만 쓸 때보다 깊은 문항).
    # 없으면 제목·설명만으로 폴백. lectures 각 항목의 transcript 유무로 갈린다.
    has_tr = any((l.get("transcript") or "").strip() for l in lectures)
    if has_tr:
        blocks = []
        for l in lectures:
            head = f"■ 강의: {l.get('title', '')}"
            if l.get("description"):
                head += f" — {l['description']}"
            body = (l.get("transcript") or "").strip() or "(자막 없음 — 제목·설명만 참고)"
            blocks.append(head + "\n" + body)
        source = "아래는 이 코스 각 강의의 실제 내용(자막)입니다.\n\n" + "\n\n".join(blocks)
        basis = (
            "위 강의 자막에 실제로 나온 내용을 근거로, 코스를 수료한 학생이 그 내용을 이해했는지"
            " 확인하는"
        )
        extra = "- 자막에 없는 내용을 지어내지 마세요(자막 근거 문항).\n"
    else:
        lines = "\n".join(
            f"- {l.get('title', '')}" + (f": {l['description']}" if l.get("description") else "")
            for l in lectures
        ) or "- (강의 정보 없음)"
        source = f"이 코스는 다음 강의들로 구성됩니다:\n{lines}"
        basis = "이 강의들에서 다룬 내용을 종합적으로 이해했는지 확인하는"
        extra = ""
    return (
        "당신은 온라인 강의 코스의 '수료 시험' 출제자입니다.\n"
        f"과목: {subject}\n코스 제목: {course_title}\n\n"
        f"{source}\n\n"
        f"{basis} 4지선다 수료 시험 문제 {n}개를 만드세요.\n"
        "규칙:\n"
        "- 코스 전체 범위를 골고루 다루되, 특정 강의 하나에만 치우치지 마세요.\n"
        "- 각 문제는 보기 4개, 정답은 하나.\n"
        "- 단순 암기보다 개념 이해·적용을 확인하는 문제를 우선하세요.\n"
        f"{extra}"
        "- explain에 정답 해설 1~2문장.\n\n"
        "다음 JSON 배열만 출력하세요(코드펜스·설명 없이):\n"
        '[{"prompt": "질문", "options": ["보기1", "보기2", "보기3", "보기4"], '
        '"answer_index": 0, "explain": "해설"}]'
    )


def generate_course_exam_questions(
    *,
    course_title: str,
    subject: str,
    lectures: list[dict],
    n: int = 5,
    api_key: str | None = None,
    openai_key: str | None = None,
    models: list[dict] | None = None,
    on_usage=None,
) -> list[dict]:
    """코스 강의 구성(제목·설명·자막)에서 수료 시험 문항 n개 생성 — 멀티프로바이더 _post_messages 재사용.

    lectures 각 항목에 transcript(자막 텍스트)가 있으면 그 실제 내용을 근거로 출제한다
    (제목·설명만 쓸 때보다 깊은 문항). 없으면 제목·설명만으로 폴백(_course_exam_prompt).

    반환 [{prompt, options, answer_index, explain}]. **자기검증(봇저항)은 하지 않는다** —
    수료 시험은 시청 검증 캡차가 아니라 '지식·이해'를 확인하는 시험이라, 상식으로 풀리는
    문항도 정당하다(강의 캡차 파이프라인의 bank/captcha/discard 3분류와 목적이 다르다).
    생성 슬롯 모델·provider·키는 호출자가 넘긴다(lectures 생성과 동일). 키가 하나도 없으면
    AiNotConfiguredError, 호출/파싱 실패는 AiGenerationError — stub 문항을 지어내지 않는다."""
    settings = get_settings()
    key = (api_key if api_key is not None else settings.ANTHROPIC_API_KEY or "").strip()
    oa = (openai_key or "").strip()
    if not key and not oa:
        raise AiNotConfiguredError("LLM API 키가 설정되지 않았습니다.")
    n = max(1, min(int(n), 20))
    text = _post_messages(
        key,
        _course_exam_prompt(course_title, subject, lectures, n),
        max_tokens=8192,
        models=models,
        on_usage=on_usage,
        openai_key=oa,
    )
    return _parse_questions(text, n)
