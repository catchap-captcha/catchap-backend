"""메인 캡차 API + 교육형 캡차 API — 키 인증·요금제 게이팅·챌린지 생성/검증.

두 제품:
  - 'captcha' (메인 캡차): 봇 차단용(사람 판별) — 통과/실패. 그림 다중선택 / 간단 셈.
  - 'edu' (교육형 API): 통과/실패가 아니라 아이가 학습하는 동안 '행동데이터'를 모으는 API.
    과목별 학습 문항 + 반응시간·재시도·조작 데이터 → behavior_summaries 적재(행동분석 AI 재료).

챌린지는 무상태(stateless) 토큰으로 관리하되, 정답 페이로드는 서버 키로 **암호화**해
클라이언트가 디코드할 수 없다(과거 base64는 복원 가능한 결함이었음).
1회용: challenge nonce·verdict jti 소비를 DB(UNIQUE)에 원자적으로 기록해 리플레이 차단
(인메모리 used-set은 멀티워커/재시작에 무효라 폐기).
"""

import base64
import hashlib
import json
import math
import re
import secrets
import time
from datetime import datetime
from functools import lru_cache
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import sha256_hash
from app.models import ApiKey, ApiUsageLog, CaptchaConsumedToken, Plan, Site, Subscription

# ── 제품 · 요금제 엔타이틀먼트 ─────────────────────────────────
PRODUCTS = {"captcha": "메인 캡차 API", "edu": "교육형 API (행동데이터 수집)"}
EDU_SUBJECTS = ["국어", "영어", "수학", "과학", "사회", "생활"]

# 요금제(key)별 사용 가능한 제품. Basic=메인만, Pro↑=교육형까지.
PLAN_PRODUCTS = {
    "Basic": ["captcha"],
    "Pro": ["captcha", "edu"],
    "Enterprise": ["captcha", "edu"],
}
DEFAULT_PRODUCTS = ["captcha"]  # 구독 없으면 메인만

CHALLENGE_TTL = 180  # 초 — 메인 캡차(봇 방어): 짧게 유지
# 교육형 학습 문항: 문제당 시간 제한 없음(사용자 결정 0714). 인앱 학습은 로그인 학생이라
# 봇 방어용 만료가 불필요 → 사실상 무제한(24시간)으로 둬서 어떤 과목이든 오래 풀어도
# 만료로 "다시 시도"가 뜨지 않는다. (토큰은 1회용 _consume이라 재사용은 여전히 차단)
EDU_CHALLENGE_TTL = 24 * 60 * 60  # 초(24시간 = 사실상 무제한)
VERDICT_TTL = 300  # 초


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    # JWT_SECRET_KEY에서 파생한 32바이트 → Fernet 키(암호화+인증). 정답이 토큰에서 복원 불가.
    digest = hashlib.sha256(("captcha:" + get_settings().JWT_SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _sign(payload: dict) -> str:
    """페이로드(정답 포함)를 암호화한 불투명 토큰. 클라이언트는 복호화 불가."""
    raw = json.dumps(payload, ensure_ascii=False).encode()
    return _fernet().encrypt(raw).decode()


def _unsign(token: str) -> dict | None:
    try:
        raw = _fernet().decrypt(token.encode())
        data = json.loads(raw.decode())
    except (InvalidToken, ValueError, TypeError):
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data


def _consume(db: Session, kind: str, token_id: str, exp: float) -> bool:
    """1회용 토큰 소비 기록 — INSERT 성공=최초 사용, IntegrityError=이미 사용됨(리플레이)."""
    db.add(
        CaptchaConsumedToken(
            kind=kind,
            token_id=token_id,
            # epoch → 로컬(KST) naive. created_at과 같은 규약이라 같은 행에서 빼도 된다.
            # (utcfromtimestamp면 만료가 생성보다 9시간 이르게 찍힌다 — 현재 이 값을
            #  읽는 곳은 없지만, 나중에 만료 청소를 붙이면 그때 9시간 어긋난다)
            expires_at=datetime.fromtimestamp(exp) if exp else None,
        )
    )
    try:
        db.flush()
        return True
    except IntegrityError:
        db.rollback()
        return False


# ── 키 발급/인증 ───────────────────────────────────────────────
def issue_key(
    db: Session, org_id: str, product: str, subject: str | None, label: str | None,
    site_name: str | None = None, domain: str | None = None, created_by: str | None = None,
    first_party: bool = False,
) -> dict:
    """API 키 발급. secret 원문은 이 반환에서만 노출(이후 hash만 보관).

    first_party: 우리 인앱 키(요청별 과목 전환 허용). 외부 판매 키는 False → 발급 과목 고정.
    """
    if product not in PRODUCTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="알 수 없는 제품입니다.")
    if product == "edu" and subject not in EDU_SUBJECTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="교육형은 과목을 지정해야 합니다.")
    # 사이트(도메인) 레코드 — 키가 붙을 대상
    site = Site(
        organization_id=org_id,
        name=site_name or (label or "새 사이트"),
        domain=domain or "",
        allowed_origins=[domain] if domain else [],
        status="active",
    )
    db.add(site)
    db.flush()
    site_key = f"ck_{product}_{secrets.token_hex(12)}"
    secret = f"cs_{secrets.token_hex(20)}"
    api = ApiKey(
        organization_id=org_id, site_id=site.id, site_key=site_key,
        secret_key_hash=sha256_hash(secret), product=product,
        subject=subject if product == "edu" else None, label=label, status="active",
        first_party=bool(first_party),
    )
    db.add(api)
    db.commit()
    return {
        "id": api.id, "site_key": site_key, "secret_key": secret, "product": product,
        "subject": api.subject, "label": label, "site_id": site.id,
        "first_party": api.first_party,
    }


def rotate_secret(db: Session, api: ApiKey) -> str:
    """secret_key만 재발급 — site_key는 유지(위젯 재배포 불필요). 새 secret은 이 반환에서만 노출."""
    secret = f"cs_{secrets.token_hex(20)}"
    api.secret_key_hash = sha256_hash(secret)
    db.add(api)
    db.commit()
    return secret


def auth_site_key(db: Session, site_key: str) -> ApiKey:
    api = db.query(ApiKey).filter(ApiKey.site_key == site_key, ApiKey.status == "active").first()
    if api is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 site_key 입니다.")
    return api


def _origin_host(value: str | None) -> str | None:
    """Origin/Referer 헤더나 저장된 도메인 문자열에서 호스트명만 추출 (소문자)."""
    if not value:
        return None
    v = value.strip()
    if "://" not in v:
        v = "//" + v  # 스킴 없는 "example.com" 형태도 urlparse가 호스트로 읽게
    host = urlparse(v).hostname
    return host.lower() if host else None


def assert_origin_allowed(db: Session, api: ApiKey, origin: str | None, referer: str | None) -> None:
    """발급 시 지정한 허용 도메인 강제 — site_key는 공개값이라 이게 없으면 아무 사이트나
    남의 키로 quota를 소진할 수 있다.

    - 도메인 미지정 키: 모든 출처 허용 (개발·테스트용, 발급 화면에 안내됨)
    - 도메인 지정 키: Origin(없으면 Referer)의 호스트가 허용 도메인 또는 그 서브도메인이어야 함.
      브라우저 밖(curl)에서는 헤더 위조가 가능하지만, 이 검증의 목적은 '다른 사이트'의
      브라우저가 남의 키를 쓰는 것을 막는 것이다 (reCAPTCHA의 도메인 검증과 동일한 모델).
    """
    site = db.get(Site, api.site_id) if api.site_id else None
    if site is None:
        # 키에 사이트가 연결돼 있는데 행이 없다 = 데이터 이상 — 무제한으로 열지 않고 차단
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="키의 사이트 설정을 찾을 수 없어요. 키를 다시 발급해 주세요."
        )
    raw = site.allowed_origins or []
    if isinstance(raw, str):  # 수동 조작 등으로 배열이 아닌 문자열이 저장된 경우
        raw = [raw]
    if not isinstance(raw, list):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="키의 도메인 설정이 손상됐어요. 키를 다시 발급해 주세요.")
    allowed = [h for h in (_origin_host(a) for a in raw) if h]
    if not allowed:
        return
    host = _origin_host(origin) or _origin_host(referer)
    if host is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="이 키는 허용 도메인이 지정돼 있어요. 브라우저(Origin 헤더)에서만 호출할 수 있어요.",
        )
    if not any(host == a or host.endswith("." + a) for a in allowed):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="허용되지 않은 도메인이에요. 키 발급 시 등록한 도메인에서만 쓸 수 있어요.",
        )


def plan_for_org(db: Session, org_id: str) -> Plan | None:
    sub = (
        db.query(Subscription)
        .filter(Subscription.organization_id == org_id, Subscription.status == "active")
        .first()
    )
    return db.get(Plan, sub.plan_id) if sub else None


def allowed_products(plan: Plan | None) -> list[str]:
    if plan is None:
        return DEFAULT_PRODUCTS
    return PLAN_PRODUCTS.get(plan.key, DEFAULT_PRODUCTS)


def org_entitlements(db: Session, org_id: str) -> dict:
    """이 기관이 발급 가능한 범위 — 요금제 허용 제품 + 구매한 교육형 과목.

    기관 관리자 자율 발급은 이 범위로 제한된다(구매 안 한 과목·제품 발급 차단).
    """
    from app.models import Organization

    plan = plan_for_org(db, org_id)
    org = db.get(Organization, org_id)
    subjects = [s for s in (org.edu_subjects or []) if s in EDU_SUBJECTS] if org else []
    return {
        "products": allowed_products(plan),
        "edu_subjects": subjects,
        "plan": plan.name if plan else "미구독",
    }


def assert_entitled(db: Session, api: ApiKey) -> Plan | None:
    """이 키의 제품이 기관 요금제로 허용되는지 + 이번 달 quota 확인.

    1st-party(우리 앱 자체 소비 = 인앱 학습 dogfooding) 키는 외부 판매 고객이 아니라
    플랫폼 자체 사용이므로 요금제·quota 게이트를 적용하지 않는다. 이게 없으면 학교가
    Basic 요금제일 때 학생 인앱 학습이 '교육형 API를 쓸 수 없어요'로 막힌다.
    """
    plan = plan_for_org(db, api.organization_id)
    if api.first_party:
        return plan
    if api.product not in allowed_products(plan):
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"현재 요금제로는 '{PRODUCTS.get(api.product, api.product)}'를 쓸 수 없어요. 요금제를 올려주세요.",
        )
    if plan and plan.api_quota:
        used = _usage_this_month(db, api.organization_id)
        if used >= plan.api_quota:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"이번 달 API 호출 한도({plan.api_quota:,})를 다 썼어요.",
            )
    return plan


def _usage_this_month(db: Session, org_id: str) -> int:
    # 과금/quota 단위는 'challenge 발급 1건 = 1'. verify·validate 로그까지 세면
    # 한 번의 통과가 3건으로 부풀어 quota가 3배로 왜곡된다 → challenge만 집계한다.
    # created_at은 로컬(datetime.now)로 저장되므로 월초도 로컬 기준으로 잡는다.
    # utcnow로 잡으면 로컬 새 달 첫 ~9시간(KST) 동안 전월이 포함돼 quota가 과다
    # 차단되고, 로컬 월초로 리셋되는 대시보드 집계(aggregate.*_this_month)와도 어긋난다.
    now = datetime.now()
    start = datetime(now.year, now.month, 1)
    return (
        db.query(func.count(ApiUsageLog.id))
        .filter(
            ApiUsageLog.organization_id == org_id,
            ApiUsageLog.created_at >= start,
            ApiUsageLog.endpoint.like("%challenge%"),
        )
        .scalar()
        or 0
    )


def log_call(
    db: Session, api: ApiKey, endpoint: str, status_code: int, latency_ms: int = 0,
    subject: str | None = None,
) -> None:
    # subject: 그 요청의 '효과 과목'(1st-party 키가 ?subject=로 전환한 실제 과목). 미지정 시
    # 키에 박힌 과목. 과목별 사용량이 실제 출제 과목과 맞게 집계된다.
    db.add(
        ApiUsageLog(
            organization_id=api.organization_id, site_id=api.site_id,
            api_key_id=api.id, product=api.product, subject=subject or api.subject,
            endpoint=endpoint, method="POST", status_code=status_code, latency_ms=latency_ms,
        )
    )
    # created_at(로컬=KST)과 같은 규약 — utcnow면 같은 응답에서 발급일과 9시간 어긋난다
    api.last_used_at = datetime.now()


# ── 챌린지 생성 ────────────────────────────────────────────────
import random  # noqa: E402

_MAIN_CATEGORIES = [
    {"label": "고양이", "target": "🐱", "distractors": ["🐶", "🐰", "🐻", "🦊", "🐼", "🐸", "🐵", "🐷"]},
    {"label": "자동차", "target": "🚗", "distractors": ["🚲", "✈️", "🚂", "🛴", "⛵", "🚁", "🏍️", "🚀"]},
    {"label": "과일", "target": "🍎", "distractors": ["🥕", "🍄", "🌽", "🥦", "🧀", "🍞", "🥚", "🍟"]},
    {"label": "꽃", "target": "🌸", "distractors": ["🌵", "🍁", "🌿", "🪨", "🐚", "⭐", "☁️", "🔧"]},
]


def make_challenge(
    product: str, subject: str | None, day: int | None = None,
    replay: bool = False, learning: bool = False,
    chapter: int | None = None, stage: int | None = None,
) -> dict:
    """공개 응답용 챌린지(정답 미포함) + 검증용 서명 토큰.

    day/replay(교육형·인앱): 커리큘럼 일차 문항 발급 + 복습 표시. 토큰에 서명돼
    verify 시점에 위조 없이 복원된다.
    chapter/stage(전체학습 주간 챕터): 그 단계 문항만 출제 + 토큰에 서명 →
    verify가 오늘의퀴즈를 건드리지 않게(학습·습관 분리) 판별한다.
    learning(1st-party 인앱): 뱅크 있는 과목은 조작형 대신 실제 문제만 낸다.
    """
    if product == "edu":
        return _edu_challenge(subject, day, replay, learning, chapter, stage)
    return _main_challenge()


def _wrap(kind: str, answer, public: dict, meta: dict | None = None) -> dict:
    """meta(subj/qid/day/rp)는 토큰에만 서명 포함 — verify에서 학생 적립·오답노트에 쓴다.

    meta가 있으면 교육형 학습 문항(연습장 계산 등 오래 풀 수 있음) → 넉넉한 TTL,
    meta가 없으면 메인 캡차(봇 방어) → 짧은 TTL. (main은 _wrap에 meta를 안 넘긴다)"""
    ttl = EDU_CHALLENGE_TTL if meta else CHALLENGE_TTL
    token = _sign(
        {"k": kind, "a": answer, "exp": time.time() + ttl, "n": secrets.token_hex(16),
         **(meta or {})}
    )
    return {"challenge_token": token, **public}


def _main_challenge() -> dict:
    if random.random() < 0.5:
        # 그림 다중 선택
        cat = random.choice(_MAIN_CATEGORIES)
        n_target = random.randint(2, 3)
        cells = [{"id": f"c{i}", "emoji": cat["target"]} for i in range(n_target)]
        for i, e in enumerate(random.sample(cat["distractors"], 9 - n_target)):
            cells.append({"id": f"c{n_target + i}", "emoji": e})
        random.shuffle(cells)
        answer = sorted(c["id"] for c in cells if c["emoji"] == cat["target"])
        return _wrap(
            "select_all", answer,
            {"type": "image_select", "prompt": f"{cat['label']}을(를) 모두 골라주세요", "cells": cells},
        )
    # 간단 셈 (단일 선택)
    a, b = random.randint(1, 9), random.randint(1, 9)
    ans = a + b
    opts = {ans}
    while len(opts) < 4:
        opts.add(random.randint(2, 18))
    options = [{"id": str(v), "text": str(v)} for v in sorted(opts)]
    random.shuffle(options)
    return _wrap(
        "single", str(ans),
        {"type": "arithmetic", "prompt": f"{a} + {b} = ?", "options": options},
    )


# ── 드래그형 문제 (행동 데이터의 핵심 재료: 드래그·그리기 궤적) ─────────
# 따라 그리기 템플릿 — 전부 한 획으로 그릴 수 있는 글자/도형 (좌표는 그리기 영역 기준 0~1)
_CIRCLE = [
    [0.5, 0.22], [0.68, 0.28], [0.78, 0.45], [0.75, 0.62], [0.62, 0.76],
    [0.45, 0.78], [0.3, 0.7], [0.23, 0.54], [0.26, 0.37], [0.38, 0.25], [0.5, 0.22],
]
_STAR = [
    [0.5, 0.18], [0.58, 0.42], [0.83, 0.42], [0.63, 0.58], [0.71, 0.83],
    [0.5, 0.68], [0.29, 0.83], [0.37, 0.58], [0.17, 0.42], [0.42, 0.42], [0.5, 0.18],
]
_TRACE_GLYPHS: dict[str, list[tuple[str, list[list[float]]]]] = {
    "국어": [
        ("ㄱ", [[0.28, 0.3], [0.7, 0.3], [0.7, 0.78]]),
        ("ㄴ", [[0.3, 0.22], [0.3, 0.75], [0.74, 0.75]]),
        ("ㄷ", [[0.72, 0.26], [0.3, 0.26], [0.3, 0.75], [0.72, 0.75]]),
        ("ㅁ", [[0.3, 0.26], [0.7, 0.26], [0.7, 0.75], [0.3, 0.75], [0.3, 0.26]]),
    ],
    "영어": [
        ("C", [[0.7, 0.3], [0.55, 0.22], [0.38, 0.26], [0.28, 0.42], [0.28, 0.58], [0.38, 0.74], [0.55, 0.78], [0.7, 0.7]]),
        ("L", [[0.35, 0.2], [0.35, 0.78], [0.72, 0.78]]),
        ("V", [[0.28, 0.22], [0.5, 0.78], [0.72, 0.22]]),
        ("Z", [[0.28, 0.25], [0.72, 0.25], [0.28, 0.75], [0.72, 0.75]]),
        ("W", [[0.22, 0.25], [0.35, 0.75], [0.5, 0.42], [0.65, 0.75], [0.78, 0.25]]),
        ("S", [[0.68, 0.28], [0.5, 0.22], [0.35, 0.3], [0.4, 0.45], [0.6, 0.55], [0.65, 0.68], [0.5, 0.78], [0.32, 0.72]]),
    ],
    "수학": [
        ("1", [[0.4, 0.32], [0.52, 0.22], [0.52, 0.78]]),
        ("2", [[0.35, 0.32], [0.45, 0.22], [0.6, 0.24], [0.65, 0.38], [0.5, 0.55], [0.35, 0.75], [0.7, 0.75]]),
        ("3", [[0.35, 0.27], [0.55, 0.22], [0.65, 0.33], [0.52, 0.47], [0.65, 0.6], [0.55, 0.75], [0.35, 0.72]]),
        ("7", [[0.3, 0.25], [0.7, 0.25], [0.45, 0.78]]),
        ("동그라미", _CIRCLE),
    ],
}
_TRACE_SHAPES = [  # 국·영·수 외 과목 공용 도형
    ("동그라미", _CIRCLE),
    ("세모", [[0.5, 0.22], [0.75, 0.75], [0.25, 0.75], [0.5, 0.22]]),
    ("별", _STAR),
    ("지그재그", [[0.25, 0.3], [0.45, 0.7], [0.6, 0.35], [0.78, 0.72]]),
]

# 끌어다 놓기 세트 — {아이템}을 {목표}에 넣기
_DRAG_SETS = [
    {"item": "🍎", "item_label": "사과", "target": "🧺", "target_label": "바구니"},
    {"item": "⚽", "item_label": "공", "target": "🥅", "target_label": "골대"},
    {"item": "✉️", "item_label": "편지", "target": "📮", "target_label": "우체통"},
    {"item": "🐟", "item_label": "물고기", "target": "🪣", "target_label": "어항"},
    {"item": "🍪", "item_label": "쿠키", "target": "🍽️", "target_label": "접시"},
    {"item": "🐝", "item_label": "꿀벌", "target": "🌸", "target_label": "꽃"},
]

DROP_ZONE_R = 0.14  # 목표 반경 (놀이 영역 대각선 대비, 유아 손 조작 감안해 넉넉히)
TRACE_MIN_USER_POINTS = 8
# 채점 기준 (적대적 검증에서 평균 기반 기준이 '반만 그리기'와 '중앙 낙서'에 뚫려 교체):
TRACE_COVER_DIST = 0.10  # 템플릿 점이 '지나갔다'로 인정되는 거리
TRACE_COVER_FRAC = 0.85  # 템플릿 점 중 이 비율 이상을 지나가야 함 (부분 그리기 차단)
TRACE_STRAY_THRESHOLD = 0.16  # 사용자 점→템플릿 평균 거리 (동떨어진 낙서 차단)
TRACE_LEN_RATIO_MAX = 2.5  # 그린 길이 ≤ 템플릿 길이 × 2.5 (빽빽한 채우기 낙서 차단)
TRACE_LEN_RATIO_MIN = 0.5  # 그린 길이 ≥ 템플릿 길이 × 0.5 (점 몇 개 찍기 차단)


def _edu_drag_challenge(subject: str, meta: dict | None = None) -> dict:
    s = random.choice(_DRAG_SETS)
    # 시작(좌측 하단 부근)·목표(우측 상단 부근) 위치를 매번 조금씩 흔든다
    start = {"x": round(random.uniform(0.12, 0.3), 3), "y": round(random.uniform(0.55, 0.8), 3)}
    zone = {"cx": round(random.uniform(0.62, 0.85), 3), "cy": round(random.uniform(0.2, 0.45), 3), "r": DROP_ZONE_R}
    return _wrap(
        "drop", zone,
        {"type": "drag_drop", "subject": subject,
         "prompt": f"{s['item_label']}를 {s['target_label']}에 쏙 넣어주세요!",
         "hint": f"{s['item']}을(를) 꾹 누른 채로 {s['target']}까지 끌어다 놓아요.",
         "item": s["item"], "item_label": s["item_label"],
         "target": s["target"], "target_label": s["target_label"],
         "start": start, "zone": zone},
        meta,
    )


def _edu_trace_challenge(subject: str, meta: dict | None = None) -> dict:
    glyphs = _TRACE_GLYPHS.get(subject) or _TRACE_SHAPES
    name, path = random.choice(glyphs)
    return _wrap(
        "trace", path,
        {"type": "trace_path", "subject": subject,
         "prompt": f"점선을 따라 '{name}'을(를) 그려보세요!",
         "hint": "점선 위를 천천히 따라 그으면 돼요.",
         "glyph": name, "path": path},
        meta,
    )


def _resample(points: list, n: int) -> list[tuple[float, float]]:
    """폴리라인을 호 길이 기준 n개의 등간격 점으로 리샘플 — 채점 밀도 균일화."""
    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) < 2:
        return pts
    seg = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(seg)
    if total <= 0:
        return [pts[0]] * n
    out = []
    for i in range(n):
        d = total * i / (n - 1)
        acc = 0.0
        for j, sl in enumerate(seg):
            if acc + sl >= d or j == len(seg) - 1:
                t = 0.0 if sl == 0 else (d - acc) / sl
                out.append((
                    pts[j][0] + (pts[j + 1][0] - pts[j][0]) * t,
                    pts[j][1] + (pts[j + 1][1] - pts[j][1]) * t,
                ))
                break
            acc += sl
    return out


def _clean_xy_points(raw, cap: int) -> list[tuple[float, float]]:
    """answer로 온 [[x,y],...] 검증 — 숫자 아닌 점은 버리고 0~1 클램프, cap개로 자름."""
    if not isinstance(raw, list):
        return []
    out = []
    for p in raw[:cap]:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        out.append((max(0.0, min(1.0, x)), max(0.0, min(1.0, y))))
    return out


def _grade_drop(answer, zone: dict) -> tuple[bool, float]:
    """드롭 지점이 목표 반경 안인지 + 정규화 거리(서버 진실값) 반환."""
    try:
        x = float((answer or {}).get("x"))
        y = float((answer or {}).get("y"))
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        return False, 1.0
    dist = math.dist(
        (max(0.0, min(1.0, x)), max(0.0, min(1.0, y))),
        (zone.get("cx", 0.5), zone.get("cy", 0.5)),
    )
    return dist <= zone.get("r", DROP_ZONE_R), round(min(1.0, dist), 3)


def _polyline_len(pts: list) -> float:
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) if len(pts) > 1 else 0.0


def _grade_trace(answer, template: list) -> bool:
    """따라 그리기 채점 — 유아 손떨림에는 관대하되 형태는 실제로 검증한다.

    세 게이트 (적대적 검증으로 조정):
      1) 커버리지 비율: 템플릿 점의 85% 이상을 0.10 이내로 지나야 함 — '반만 그리기' 차단
      2) 이탈 평균: 사용자 점이 템플릿에서 평균 0.16 이내 — 동떨어진 낙서 차단
      3) 길이 비율: 그린 길이가 템플릿의 0.5~2.5배 — 빽빽한 채우기 낙서/점 찍기 차단
    """
    user = _clean_xy_points(answer, 600)
    if len(user) < TRACE_MIN_USER_POINTS:
        return False
    ref = _resample(template, 24)
    tpl_len = _polyline_len([(float(p[0]), float(p[1])) for p in template])
    user_len = _polyline_len(user)
    if tpl_len > 0 and not (
        tpl_len * TRACE_LEN_RATIO_MIN <= user_len <= tpl_len * TRACE_LEN_RATIO_MAX
    ):
        return False

    def _min_d(p, pts):
        return min(math.dist(p, q) for q in pts)

    covered = sum(1 for r in ref if _min_d(r, user) < TRACE_COVER_DIST) / len(ref)
    stray = sum(_min_d(u, ref) for u in user) / len(user)
    return covered >= TRACE_COVER_FRAC and stray < TRACE_STRAY_THRESHOLD


# 조작형 문항의 표시 필드(정답 아님) — verify에서 위젯이 렌더할 데이터. 추출 단계에서
# right/cards/items는 이미 셔플되어 정답 순서를 노출하지 않는다.
_WIDGET_RENDER_FIELDS = ("options", "left", "right", "bins", "items", "cards", "zones",
                         "reference", "mapStyle", "compass", "start", "layout", "audio", "mapRef",
                         "template", "glyph", "character", "dest", "dangers",
                         "flag", "cols", "rows", "slots", "pieces", "prefilled", "preview",
                         # 원본 유형 복원 — 국어(받아쓰기 tts·높임말 입력·문장부호 자리탭·십자말)
                         # 과학/수학(카드 드래그 target·장면 클릭 scene_svg/regions)
                         "tts", "before", "highlight", "after", "tokens", "gaps", "markLabel",
                         "size", "words", "tiles", "level", "reveal", "target", "scene_svg", "regions",
                         "sentence",  # 영문법 빈칸 문장(표시용) — 정답은 options/answer에만
                         "image", "meaning",  # 원본 그림(이모지)·한국어 뜻 힌트 (영어 08/09 등)
                         # 원본 프레이밍(표시용) — 상황 지문·담기 상자 라벨·폰 화면 제목/스타일·안내 화살문구
                         "scenario", "boxLabel", "boxHint", "screenTitle", "screenStyle", "arrow", "placeCount",
                         # 메모리 카드게임(미리보기·시간제한)·연속듣기·듣기 재생제한/라벨
                         "previewMs", "timeLimitMs", "audios", "showLabel", "plays",
                         "guideStyle", "showExample",  # 따라쓰기 가이드 5단계(원본 03)
                         "paragraph", "readFirst",  # 국어 중심생각 — 지문 읽기 2단계(원본 흐름)

                         # 수학 교체판 — figure(문제 위 도형 SVG, 표시용). answers는 정답이라 미노출.
                         "figure")


def _normalize_answer(v) -> str:
    """수학 입력형(input) 정답 정규화 — 원본 normalizeAnswer 이식.

    공백·쉼표·괄호·원문자·흔한 단위를 제거해 '75'와 '75도', '4시간25분'과
    '4시간 25분'을 같게 본다. (뱅크 answers에 변형이 있어도 방어적으로 정규화)
    """
    s = str(v).replace("﻿", "").strip().lower()  # 원본 JS \s는 ZWNBSP도 공백으로 봄
    s = re.sub(r"<[^>]*>", "", s)
    for a, b in zip("①②③④⑤⑥⑦⑧⑨⑩", ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]):
        s = s.replace(a, b)
    s = re.sub(r"[()\[\]{},]", "", s)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"(도|°|쪽|명|개|자루|분|번|살|마리|그루|원|회|cm|㎝|m|㎖|ml|l|kg|㎏|㎠)", "", s)
    return s


def _in_box(px: float, py: float, box: dict, pad: float = 0.0) -> bool:
    """점(px,py)이 box(x,y,w,h, 0~1)의 [±pad] 안에 있는지."""
    return (box["x"] - pad <= px <= box["x"] + box["w"] + pad
            and box["y"] - pad <= py <= box["y"] + box["h"] + pad)


ROUTE_START_R = 0.20  # 경로 첫 점이 시작 핀(캐릭터)에서 이 반경 안이어야 함(정규화 좌표)


def _grade_route(answer, dest: dict, dangers: list, start: dict | None = None) -> bool:
    """길찾기 채점 — 그린 경로가 (1) 시작점(캐릭터)에서 출발해 (2) 도착 지점에서 끝나고
    (3) 어떤 위험존도 지나지 않는다.

    원본은 캐릭터 토큰을 끌어 경로가 반드시 시작점에서 출발한다. 위젯이 선 그리기로 바뀌며
    이 제약이 사라져 도착지 옆에서 짧게 그려도 통과하던 것을 시작점 근접 검사로 되살린다.
    """
    pts = _clean_xy_points(answer, 600)
    if len(pts) < TRACE_MIN_USER_POINTS:
        return False
    if start and start.get("x") is not None and start.get("y") is not None:
        if math.dist((pts[0][0], pts[0][1]), (start["x"], start["y"])) > ROUTE_START_R:
            return False
    if not _in_box(pts[-1][0], pts[-1][1], dest):
        return False
    for (px, py) in pts:
        for d in dangers:
            if _in_box(px, py, d):
                return False
    return True


def _wrap_bank_question(subject: str, q: dict, meta: dict) -> dict:
    """뱅크 문항 → 위젯 챌린지 포맷. 토큰 meta에 qid를 실어 verify에서 오답노트에 쓴다.

    유형별 채점 kind(정답은 토큰에만 서명, public엔 미포함):
      single/place → single(등호) · multi → select_all(집합) ·
      connect/sort → match(딕셔너리 정확 일치) · order → sequence(순서 일치)
    """
    meta = {**meta, "qid": q["id"]}
    public: dict = {
        "type": q["type"], "subject": subject, "topic": q["topic"],
        "prompt": q["prompt"], "hint": q["hint"],
    }
    for f in _WIDGET_RENDER_FIELDS:
        if f in q:
            public[f] = q[f]

    # 선택형 보기(options)는 발급마다 순서를 섞는다. 왜:
    #  1) 정답 위치 암기 차단 — 순서가 고정이면 "매번 3번" 식으로 내용 없이 통과한다.
    #  2) 행동 데이터 품질 — 전체학습의 목적 하나가 마우스 궤적·선택 경로 수집인데,
    #     보기 위치가 고정이면 궤적이 늘 같은 좌표로 쏠려 봇/사람 판별 신호가 편향된다.
    #     매번 섞으면 "어느 위치의 정답이든 자연스럽게 찾아가는가"를 관측할 수 있다.
    # answer가 옵션 id("o3") 기반이라 순서를 바꿔도 채점은 안전하다(verify는 위치가
    # 아니라 id로 비교). 순서 자체가 정답인 유형(order/sequence)은 options 필드를 안
    # 쓰므로 영향이 없다. (강의 캡차는 answer_index 기반의 별도 경로라 여기 안 온다.)
    if isinstance(public.get("options"), list) and len(public["options"]) > 1:
        public["options"] = random.sample(public["options"], len(public["options"]))

    t = q["type"]
    if t == "multi":
        return _wrap("select_all", sorted(q["answer"]), public, meta)
    if t == "puzzle":
        # 국기 완성 — {slotId:pieceId} 딕셔너리 비교. 단, 같은 색·모양이라 구분 불가한 조각
        # (equivalentGroups)은 서로 바꿔 놓아도 정답(억울한 오답 방지). eqg는 토큰에만 서명.
        # 미리 채운 칸(prefilled)은 위젯이 함께 제출하므로 채점 대상(target)에 포함한다
        # (빠진 조각 넣기 단계). prefilled는 이미 public에 노출된 값이라 정답 유출 아님.
        ans = {**q.get("prefilled", {}), **dict(q["answer"])}
        m = {**meta, "eqg": q["equivalentGroups"]} if q.get("equivalentGroups") else meta
        return _wrap("match", ans, public, m)
    if t in ("connect", "sort"):
        # 매핑 채점 — {leftId:rightId}/{itemId:binId} 딕셔너리 정확 비교.
        # placeCount(방해 항목이 트레이에 남는 분류)는 표시용으로 이미 public에 노출됨.
        return _wrap("match", dict(q["answer"]), public, meta)
    if t == "memory":
        # 카드 뒤집기 기억 게임(영어 07) — 짝 확인은 pair_check(미소비 오라클, 원본 /match와
        # 동일 설계), 최종 제출은 {imageCardId: wordCardId} 매핑 정확 비교.
        # mem 플래그를 서명해 pair_check가 memory 토큰만 받게 한다 — connect/sort도 같은
        # kind(match)라서, 플래그 없이는 /pair가 그 유형들의 정답 오라클이 돼 버린다.
        return _wrap("match", dict(q["answer"]), public, {**meta, "mem": 1})
    if t == "listen_seq":
        # 연속 듣기(영어 02 sequence) — 오디오 순서대로 그림 탭, [optionId,...] 순서 비교.
        public = {**public, "slotCount": len(q["answer"])}
        return _wrap("sequence", list(q["answer"]), public, meta)
    if t == "order":
        # 순서 채점 — 위젯이 [cardId,...] 제출, 서버가 리스트 정확 비교.
        # slotCount(정답 길이)를 노출해 방해 카드가 섞인 문항에서 위젯이 슬롯 수만큼만
        # 채우면 제출하게 한다(방해카드 강제 배치로 정답 불가 버그 수정).
        public = {**public, "slotCount": len(q["answer"])}
        if q.get("cyclic"):
            # 순환 순서(달 위상·물의 순환) — 어느 지점에서 시작해도 방향만 맞으면 정답.
            return _wrap("sequence", list(q["answer"]), public, {**meta, "cy": 1})
        return _wrap("sequence", list(q["answer"]), public, meta)
    if t == "trace":
        # 따라쓰기 — 위젯 trace_path 렌더러가 궤적 제출, _grade_trace로 채점.
        # template(안내 점선)은 비밀이 아니라 public에 노출한다(정답 유출 아님).
        public = {**public, "type": "trace_path", "path": q["template"]}
        return _wrap("trace", q["template"], public, meta)
    if t == "route":
        # 길찾기 — 위젯이 경로 궤적 제출, 끝점 dest 도달 + 위험존 회피로 채점.
        # dest/dangers는 화면에 보이는 요소라 노출 정상(정답 좌표가 아님).
        return _wrap("route", {"dest": q["dest"], "dangers": q.get("dangers", []), "start": q.get("start")}, public, meta)
    if t in ("dictation", "type_in"):
        # 원본 입력형(국어 받아쓰기·높임말) — 위젯이 타이핑 문자열 제출, 서버 trim 정확 일치.
        # 받아쓰기는 tts(들려줄 문장)가 public에 필요해 정답이 페이로드에 포함된다 —
        # 원본(클라이언트 TTS+채점)과 동일한 트레이드오프로, 학습용 문항이라 수용한다.
        return _wrap("text", q["answer"], public, meta)
    if t == "input":
        # 수학 직접 입력 — 위젯이 타이핑 문자열 제출, 정규화 후 answers 중 하나와 일치.
        # 정답(answers)은 토큰에만 서명(public 미포함) → 봇이 정답을 못 본다.
        return _wrap("input", list(q["answers"]), public, meta)
    if t == "punct":
        # 문장부호 — 원본과 동일하게 어절 사이 자리(gap)를 모두 탭. 집합 일치 채점.
        return _wrap("select_all", sorted(q["answer"]), public, meta)
    if t == "crossword":
        # 십자말 — 위젯이 {w0: "낱말", ...} 제출, 딕셔너리 정확 일치. 낱말 정답은
        # 토큰에만 서명(public words에는 길이·힌트·초성만 노출).
        return _wrap("match", dict(q["answer"]), public, meta)
    if t == "swipe":
        # 사실·의견 스와이프 — 원본처럼 문장 1개를 발급 시점에 뽑아 카드로 낸다.
        # 정답 태그는 토큰에만 서명. (뱅크 1문항 = 문장 풀, 발급마다 다른 문장)
        st = random.choice(q["statements"])
        public = {**public, "card": st["text"], "leftLabel": "의견", "rightLabel": "사실"}
        return _wrap("single", st["tag"], public, meta)
    if t == "drag":
        # 카드 드래그(과학·수학) — 원본처럼 카드 여러 장 중 알맞은 것을 타겟에 끌어다 놓기.
        # 위젯이 {item, x, y} 제출 → 아이템 일치 + 드롭 존 거리로 채점.
        zone = {"cx": round(random.uniform(0.62, 0.85), 3),
                "cy": round(random.uniform(0.25, 0.5), 3), "r": DROP_ZONE_R}
        public = {**public, "type": "drag_pick", "zone": zone}
        return _wrap("drag_pick", {"item": q["answer"], "zone": zone}, public, meta)
    if t == "position":
        # 장면 클릭(과학·수학) — 원본 장면 SVG의 data-region 부위를 탭. 등호 비교.
        return _wrap("single", q["answer"], public, meta)
    # single·place·listen: 단일 값 등호 비교
    return _wrap("single", q["answer"], public, meta)


def _edu_challenge(
    subject: str, day: int | None = None, replay: bool = False, learning: bool = False,
    chapter: int | None = None, stage: int | None = None,
) -> dict:
    from app.services import subject_banks

    # 전체학습 주간 챕터: 그 (챕터,단계)의 2문항에서만 출제한다. chapter/stage를 토큰 meta에
    # 서명해 verify가 오늘의퀴즈(습관)를 건드리지 않게 판별한다(학습·습관 분리).
    if chapter is not None and stage is not None and subject in subject_banks.LIVE_SUBJECTS:
        from app.services import chapters as _ch

        ids = _ch.chapter_question_ids(subject, chapter, stage)
        pool = [q for q in (subject_banks.get_question(subject, i) for i in ids) if q is not None]
        if not pool:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="플레이할 문항이 없어요.")
        q = random.choice(pool)
        return _wrap_bank_question(
            subject, q, {"subj": subject, "rp": bool(replay), "chapter": chapter, "stage": stage}
        )

    # 커리큘럼 일차 지정(생활): 그 일차의 문항만 낸다 — 실전 세션과 동일 의미.
    # is_replay(지난 일차)는 서버가 판정해 토큰에 서명 — 클라이언트가 복습 여부를 위조 못 함.
    if day is not None and subject == "생활":
        from app.services import curriculum as _cur

        detail = _cur.day_detail(subject, day)
        if detail.get("locked"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="아직 잠긴 일차예요. 오늘 과제부터 풀어봐요!")
        playable = detail.get("playable", [])
        if not playable:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="플레이할 문항이 없어요.")
        pub = random.choice(playable)
        q = subject_banks.get_question(subject, pub["id"])
        if q is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="문항을 찾을 수 없어요.")
        rp = bool(replay or detail.get("is_replay"))
        ch = _wrap_bank_question(subject, q, {"subj": subject, "rp": rp, "day": day})
        return {**ch, "topic": detail.get("topic"), "day": day, "is_replay": rp}

    meta = {"subj": subject, "rp": bool(replay)}
    # 합성 조작형(_edu_drag_challenge·_edu_trace_challenge = 랜덤 이모지 드래그·점선 따라그리기)은
    # '아동 캡차 행동데이터 수집'용이었으나 아직 제품 계획에 없어 비활성화(2026-07-13).
    # 이제 교육형 출제는 항상 실문항 뱅크에서만 낸다. (뱅크 자체의 조작형 유형 문항 — 따라쓰기·
    # 연결·순서 등 — 은 _wrap_bank_question 경로라 그대로 유지된다.) 뱅크 없는 과목은 낼
    # 실문항이 없으므로 404. (합성 생성기 함수는 재사용 대비 남겨두되 호출하지 않는다.)
    if subject not in subject_banks.LIVE_SUBJECTS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="플레이할 문항이 없어요.")
    # 실문항 뱅크 (생활=ms / 수학·과학=my / 사회=sw / 영어=ms english / 국어=jy — capcha_service 이식)
    pool = subject_banks.playable_pool(subject)
    # 오늘의 퀴즈(인앱 학습): 아직 잠긴 주차의 문항은 내지 않는다 — 전체학습과 같은 달력
    # 잠금(unlocked_count)을 존중해 '열린 챕터 범위'에서만 랜덤 출제한다. 잠금 해제 플래그가
    # 켜지면 unlocked_count가 전 챕터라 전 범위에서 뽑는다(자동 반영). learning=False(외부
    # 판매 임베드)는 구매한 과목 은행 전체를 쓰므로 커리큘럼 달력과 무관하게 제한하지 않는다.
    if learning:
        from app.services import chapters as _ch

        limit = _ch.unlocked_count(subject) * _ch.CHAPTER_SIZE
        if 0 < limit < len(pool):
            pool = pool[:limit]
    q = random.choice(pool)
    return _wrap_bank_question(subject, q, meta)


def pair_check(challenge_token: str, a, b) -> dict:
    """메모리 카드게임 짝 확인 — 토큰을 소비하지 않는 판정 오라클(원본 07 /match와 동일 설계).

    서명된 match 타겟에서 (a,b)가 짝이면 {match, left(그림), right(단어)}를 준다.
    방향은 정규화해 위젯이 최종 제출 매핑({imageId: wordId})을 쌓게 한다. 봇이 전 쌍을
    열거(n²)할 수 있는 것도 원본과 동일한 트레이드오프 — 학습용 문항이라 수용한다.
    """
    data = _unsign(challenge_token)
    # 메모리 게임 토큰(mem 서명)만 허용 — connect/sort 등 다른 match 유형의 정답 오라클로
    # 오용되는 것을 막는다(원본도 /match는 memory 전용이었다).
    if data is None or data.get("k") != "match" or not data.get("mem"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="만료됐거나 잘못된 챌린지예요.")
    tgt = {str(k): str(v) for k, v in (data.get("a") or {}).items()}
    a, b = str(a), str(b)
    if tgt.get(a) == b:
        return {"match": True, "left": a, "right": b}
    if tgt.get(b) == a:
        return {"match": True, "left": b, "right": a}
    return {"match": False}


def peek_subject(challenge_token: str) -> str | None:
    """토큰을 소비하지 않고 서명된 과목(subj)만 확인 — verify 스코프 검사용.

    외부 판매 키가 구매 안 한 과목의 챌린지 토큰을 verify하려는 것을 막을 때,
    실제 채점(verify_challenge, 토큰 소비)에 앞서 과목을 미리 본다. 서명 검증에
    실패하면(위조·만료) None을 반환하고, 채점 단계에서 400으로 걸린다.
    """
    data = _unsign(challenge_token)
    return data.get("subj") if data else None


def peek_is_lecture(challenge_token: str) -> bool:
    """토큰을 소비하지 않고 '강의 체크포인트 토큰인지'만 확인 — verify 자격 검사용.

    강의 게이트는 발급(_lecture_challenge)에서 edu 1st-party 키만 허용한다. verify에도
    같은 자격을 대칭으로 걸지 않으면, 자격 없는 키(외부 판매 edu·일반 captcha 키)로
    강의 토큰을 채점시켜 오답 응답의 정답을 수확할 수 있다 — 그 경로는 체크포인트
    실패 기록조차 남기지 않아 파밍 대가가 0이다(적대적 검토에서 실증).
    서명 검증 실패(위조·만료)는 False — 채점 단계에서 400으로 걸린다.
    """
    data = _unsign(challenge_token)
    return bool(data and data.get("lec"))


def peek_lecture_meta(challenge_token: str) -> dict | None:
    """토큰을 소비하지 않고 강의 체크포인트 메타(lec/qid/cp)만 복원 — 문항 신고용.

    학생 화면에는 question_id가 나가지 않으므로(발급 시 서명 토큰에만 봉인), 신고 대상
    문항은 '학생이 실제로 받은 챌린지 토큰'에서만 확정할 수 있다. 서명 검증 실패(위조·
    만료)나 강의 토큰이 아니면 None — 신고 엔드포인트가 400으로 거른다. verify와 달리
    nonce를 소비하지 않는다(신고가 정상 풀이 흐름을 방해하지 않게).
    """
    data = _unsign(challenge_token)
    if not data or not data.get("lec") or not data.get("qid"):
        return None
    return {"lec": str(data["lec"]), "qid": str(data["qid"]), "cp": data.get("cp")}


def verify_challenge(db: Session, challenge_token: str, answer) -> dict:
    """제출 답 서버 채점 → 통과 시 verdict 토큰(1회용) 발급.

    챌린지는 답 제출 1회용: 오답이어도 nonce를 소비한다 — 같은 토큰으로 정답이 나올
    때까지 재시도(보기 4개면 4번 안에 통과)하는 브루트포스를 차단한다. 위젯은 오답 시
    새 챌린지를 받아오므로(load()) 정상 흐름은 영향 없다.
    """
    data = _unsign(challenge_token)
    if data is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="만료됐거나 잘못된 챌린지예요.")
    if not _consume(db, "challenge", data.get("n", ""), data.get("exp", 0)):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 사용된 챌린지예요.")
    kind, target = data["k"], data["a"]
    # 발급 시 서명해 둔 문항 메타(과목·문항id·일차·복습·은행·강의 체크포인트) —
    # 엔드포인트가 학생 적립/체크포인트 처리에 쓰고 응답 전 제거
    extra: dict = {"meta": {k: data[k] for k in ("subj", "qid", "day", "rp", "chapter", "stage", "bank", "lec", "cp") if k in data}}
    if answer is None:
        # '잘 모르겠어요'(무응답) — 채점 시도 없이 오답 처리. 아래에서 정답·해설을 함께 내려
        # 학생이 찍기 강요 없이 정직하게 오답 처리하고 해설로 공부하게 한다(운 좋은 정답 방지).
        ok = False
    elif kind == "select_all":
        # answer 타입 미방어 시 정수 등 비반복형 입력이 TypeError → 공개 엔드포인트 500
        picked = sorted(str(x) for x in answer) if isinstance(answer, (list, tuple)) else []
        ok = len(picked) > 0 and picked == sorted(str(x) for x in target)
    elif kind == "match":
        # connect/sort/puzzle — {leftId:rightId}/{itemId:binId}/{slotId:pieceId} 딕셔너리 일치.
        # 비-dict 입력(위조·정수)은 조용히 오답 처리(500 방지). 부분 정답 없음.
        sub = {str(k): str(v) for k, v in answer.items()} if isinstance(answer, dict) else {}
        tgt = {str(k): str(v) for k, v in target.items()}
        eqg = data.get("eqg")
        if eqg:
            # 국기퍼즐 동치 그룹 — 같은 그룹의 조각은 서로 바꿔 놓아도 정답으로 본다.
            rep: dict = {}
            for gi, grp in enumerate(eqg):
                for pid in grp:
                    rep[str(pid)] = "g%d" % gi

            def _canon(pid):
                return rep.get(str(pid), "p:" + str(pid))

            # 원본과 동일하게 같은 조각을 두 칸에 넣는 부정 제출은 오답(조각 중복 금지)
            no_dup = len(set(sub.values())) == len(sub)
            ok = bool(tgt) and no_dup and set(sub) == set(tgt) and all(_canon(sub.get(k)) == _canon(tgt[k]) for k in tgt)
        else:
            ok = bool(target) and sub == tgt
    elif kind == "sequence":
        # order — [cardId,...] 순서 정확 일치. 비-list 입력은 오답.
        seq = [str(x) for x in answer] if isinstance(answer, (list, tuple)) else []
        tgt = [str(x) for x in target]
        ok = len(seq) > 0 and seq == tgt
        if not ok and data.get("cy") and tgt and len(seq) == len(tgt):
            # 순환 순서 — 어느 지점에서 시작해도 같은 방향(회전)이면 정답. (달 위상·물의 순환)
            doubled = tgt + tgt
            ok = any(doubled[i:i + len(tgt)] == seq for i in range(len(tgt)))
    elif kind == "drop":
        # 끌어다 놓기 — 드롭 지점 거리로 채점. 거리는 서버 진실값으로 행동 데이터에 기록
        ok, dist = _grade_drop(answer, target)
        extra["drop_distance_norm"] = dist
    elif kind == "trace":
        # 따라 그리기 — 궤적 유사도(커버리지+이탈)로 채점
        ok = _grade_trace(answer, target)
    elif kind == "route":
        # 길찾기 — 끝점이 도착지 + 위험존 미통과
        ok = _grade_route(answer, target.get("dest", {}), target.get("dangers", []), target.get("start")) if isinstance(target, dict) else False
    elif kind == "text":
        # 입력형(받아쓰기·높임말) — trim 후 정확 일치. 받아쓰기는 내부 띄어쓰기가 채점
        # 대상이므로 내부 공백은 정규화하지 않는다. 비-str 입력은 오답(500 방지).
        ok = isinstance(answer, str) and answer.strip() == str(target).strip() and bool(answer.strip())
    elif kind == "input":
        # 수학 직접 입력 — 정규화(공백·쉼표·단위 제거) 후 정답 목록 중 하나와 일치.
        norm = _normalize_answer(answer) if isinstance(answer, str) else ""
        ok = bool(norm) and norm in {_normalize_answer(a) for a in (target or [])}
    elif kind == "drag_pick":
        # 카드 드래그 — 알맞은 카드(item id) + 드롭 지점이 존 안. 거리는 행동데이터 기록.
        sub = answer if isinstance(answer, dict) else {}
        tgt = target if isinstance(target, dict) else {}
        in_zone, dist = _grade_drop(sub, tgt.get("zone", {}))
        extra["drop_distance_norm"] = dist
        ok = in_zone and str(sub.get("item")) == str(tgt.get("item"))
    else:  # single
        ok = str(answer) == str(target)
    if not ok:
        # 오답에도 정답을 내린다(교육형 피드백 "정답은 X"용) — 챌린지는 1회 소비돼
        # 이미 무효라 재제출 오라클이 되지 않고, 매 챌린지 정답이 새로 나와 파밍 가치도 없다.
        # 해설(explain)도 함께 내려 '잘 모르겠어요'·오답 시 공부할 수 있게 한다(메모리 조회, DB無).
        exp = None
        subj, qid = data.get("subj"), data.get("qid")
        if subj and qid:
            from app.services import subject_banks

            qq = subject_banks.get_question(str(subj), str(qid))
            if qq:
                exp = qq.get("explain") or qq.get("hint")
        return {"success": False, "answer": target, "explain": exp, **extra}
    verdict = _sign({"v": 1, "exp": time.time() + VERDICT_TTL, "n": secrets.token_hex(16)})
    return {"success": True, "verdict_token": verdict, "answer": target, **extra}


# ── 행동 데이터 (아동용 캡차 판정 모델 학습 재료) ─────────────────
TRACE_MAX_POINTS = 2000  # 원시 궤적 저장 상한 (요청 본문 크기 제한이 없어 서버에서 캡)
TRACE_PAUSE_GAP_MS = 300  # 이 이상 입력이 멈춘 구간을 '멈춤' 1회로 센다
# 지표 상한 — 위조 페이로드(거대 box·자기신고 값)가 그룹 평균/학습셋 통계를 부풀리지 못하게
PATH_LENGTH_CAP = 100_000.0  # px
AVG_SPEED_CAP = 100.0  # px/ms
BOX_DIM_CAP = 4_000  # px (실제 화면 크기 수준)


def _parse_trace(b: dict) -> dict | None:
    """behavior.trace([[t_ms, x, y], ...]) 검증/정규화.

    t는 상호작용 시작 기준 ms, x/y는 캡처 영역 기준 0~1. 형식이 어긋난 점은 버리고
    TRACE_MAX_POINTS로 자른다. 유효 점이 2개 미만이면 궤적 없음으로 취급.
    """
    raw = b.get("trace")
    if not isinstance(raw, list) or not raw:
        return None
    box = b.get("box") if isinstance(b.get("box"), dict) else {}

    def _dim(k: str) -> int:
        try:
            # OverflowError: JSON은 Infinity를 허용 — int(inf)가 500으로 터지지 않게
            return max(0, min(BOX_DIM_CAP, int(box.get(k, 0))))
        except (TypeError, ValueError, OverflowError):
            return 0

    pts: list[list[float]] = []
    for p in raw[:TRACE_MAX_POINTS]:
        if not isinstance(p, (list, tuple)) or len(p) < 3:
            continue
        try:
            t = max(0, min(3_600_000, int(p[0])))
            x = max(0.0, min(1.0, float(p[1])))
            y = max(0.0, min(1.0, float(p[2])))
        except (TypeError, ValueError, OverflowError):
            continue
        pts.append([t, round(x, 4), round(y, 4)])
    if len(pts) < 2:
        return None
    pts.sort(key=lambda p: p[0])
    return {"points": pts, "box_w": _dim("w"), "box_h": _dim("h")}


def _trace_metrics(trace: dict) -> dict:
    """궤적으로부터 요약 지표를 서버가 직접 계산 — 클라이언트 자기신고 대신 신뢰 가능한 값.

    path_length: px (box 크기로 복원, box 없으면 정규화 단위), avg_speed: px/ms(움직인 시간 기준),
    pause_count: TRACE_PAUSE_GAP_MS 이상 멈춘 구간 수.
    """
    pts = trace["points"]
    w = trace["box_w"] or 1
    h = trace["box_h"] or 1
    path = 0.0
    move_path = 0.0  # 이동 구간(gap 미만)의 거리만 — avg_speed 분자
    move_ms = 0
    pauses = 0
    for i in range(1, len(pts)):
        t0, x0, y0 = pts[i - 1]
        t1, x1, y1 = pts[i]
        dt = t1 - t0
        d = math.hypot((x1 - x0) * w, (y1 - y0) * h)
        path += d
        if dt >= TRACE_PAUSE_GAP_MS:
            pauses += 1
        else:
            move_ms += dt
            # gap 구간의 거리는 속도에서 제외 — 멀리 떨어진 두 탭 사이 '비행'(시간은
            # gap으로 빠지고 거리만 남음)이 avg_speed를 부풀리던 왜곡 수정(0710 재검토)
            move_path += d
    return {
        "path_length": round(min(PATH_LENGTH_CAP, path), 1),
        "avg_speed": round(min(AVG_SPEED_CAP, move_path / move_ms), 3) if move_ms > 0 else 0.0,
        "pause_count": min(1000, pauses),
        "duration_ms": max(0, int(pts[-1][0] - pts[0][0])),
    }


# ---- 아동 보정 위험 스코어링 상수 ----
# 오탐(아이→봇) 비용이 크므로 보수적으로 잡는다. 값은 실데이터 검증 전 시작값 —
# 리뷰 큐에서 실아동 분포를 보며 조정한다. 느림·오답·멈춤 많음은 아이의 정상 행동이라
# 신호로 쓰지 않는다(봇 신호는 '너무 빠름·너무 곧음·너무 균일'뿐).
RISK_IMPOSSIBLE_MS = 800  # 이보다 빨리 정답 = 아이에게 물리적으로 불가능한 즉답
RISK_FAST_MS = 1500  # 이보다 빨리 정답 = 빠르지만 불가능하진 않음(약신호)
RISK_MOVE_MIN_PX = 40.0  # 이보다 작은 변위는 직진/순간이동 판정 제외(탭·미세 이동)
RISK_JITTER_MIN_PX = 3.0  # 직선에서 최대 이탈이 이 미만 = 손떨림 없는 기계 직선
RISK_SPEED_PX_MS = 10.0  # 이동 구간 평균 속도가 이보다 크면 사람 포인터 상식 밖
RISK_TELEPORT_MS = 400  # 이 시간 넘게 크게 움직였는데 점 ≤3개 = 순간이동(합성 궤적)


def _behavior_risk_level(
    *,
    solve_time_ms: int,
    correct: bool,
    trace: dict | None,
    metrics: dict | None,
    input_type: str,
) -> str:
    """행동 1건의 자동 위험 판정 (low|review|elevated) — 아동 보정 규칙.

    elevated는 강신호 2개 이상이 겹칠 때만(즉시 차단용이 아니라 사람 검토 큐 근거),
    강신호 1개·약신호 1개 이상은 review(표본 검토)까지만 — 아동 서비스라 오탐이
    미탐보다 비싸다. retry_count는 넣지 않는다: 재시도 많음은 봇이 아니라
    '어려워하는 아이'(학습 신호, 도움필요 지표의 몫)다.
    """
    strong = 0
    weak = 0

    # 즉답 정답 — 0은 미계측이라 제외. 800ms 미만은 강신호, 1500ms 미만은 약신호.
    if correct and 0 < solve_time_ms < RISK_IMPOSSIBLE_MS:
        strong += 1
    elif correct and 0 < solve_time_ms < RISK_FAST_MS:
        weak += 1

    if trace and metrics:
        pts = trace["points"]
        w = trace["box_w"] or 1
        h = trace["box_h"] or 1
        sx, sy = pts[0][1] * w, pts[0][2] * h
        ex, ey = pts[-1][1] * w, pts[-1][2] * h
        disp = math.hypot(ex - sx, ey - sy)  # 시작→끝 변위
        # 강신호: 기계 직선 — '경로비'가 아니라 '직선에서의 최대 수직이탈'로 판정한다.
        # 드래그 과제는 원래 경로가 곧아 경로비 기준은 자신 있는 아이도 걸리지만(0710
        # skeptic 재현), 사람 손은 픽셀 단위 떨림이 있어 이탈이 0에 붙지 못한다.
        # 중간 점이 없으면(≤3점) 이탈 계산이 무의미 — 그 영역은 순간이동 신호의 몫.
        if len(pts) >= 4 and disp >= RISK_MOVE_MIN_PX:
            maxdev = 0.0
            mid_travel = False  # 시작·끝에서 떨어진 '진짜 이동 중' 샘플이 있는가
            for p in pts[1:-1]:
                px, py = p[1] * w, p[2] * h
                # 점-직선(시작→끝) 수직 거리
                maxdev = max(maxdev, abs((ex - sx) * (sy - py) - (sx - px) * (ey - sy)) / disp)
                if (
                    math.hypot(px - sx, py - sy) >= RISK_MOVE_MIN_PX / 2
                    and math.hypot(px - ex, py - ey) >= RISK_MOVE_MIN_PX / 2
                ):
                    mid_travel = True
            # 중간점이 전부 양 끝 위치에 붙어 있으면(예: 떨어진 두 지점을 각각 탭)
            # '경로'가 없어 직선 판정이 무의미 — 아이의 두 번 탭이 우연히 일직선일 때
            # 기계 직선으로 오탐하지 않는다(0712 회귀 테스트가 재현).
            if maxdev < RISK_JITTER_MIN_PX and mid_travel:
                strong += 1
        # 강신호: 사람 포인터 상식 밖의 평균 속도(이동 구간 기준 — gap 왜곡은 _trace_metrics에서 제거)
        if metrics["avg_speed"] > RISK_SPEED_PX_MS:
            strong += 1
        # 강신호: 순간이동 — 크게 움직였는데 중간 샘플이 없다. 수집기는 down/up을 무조건
        # 기록하고 move를 16ms로 스로틀하므로, 40px 이상 움직이면 중간 점이 반드시 남는다.
        # 변위 0의 '지긋한 탭홀드'는 정상 아동 입력이라 걸지 않는다(0710 skeptic 재현).
        if (
            metrics["duration_ms"] > RISK_TELEPORT_MS
            and len(pts) <= 3
            and disp >= RISK_MOVE_MIN_PX
        ):
            strong += 1
        # 약신호: 멈춤 0 + 빠른 완료 — 아이는 보통 중간에 멈칫한다
        if metrics["pause_count"] == 0 and 0 < solve_time_ms < 3000:
            weak += 1

    # input_type=unknown은 신호로 쓰지 않는다 — 메타데이터 공백이지 행동 이상이 아니다.
    # (실측: 구형 클라이언트 데이터 97%가 unknown이라 단독 약신호로 두면 리뷰 큐가 범람)

    if strong >= 2:
        return "elevated"
    if strong == 1 or weak >= 1:
        return "review"
    return "low"


def _actor_band(db: Session, student_id: str | None) -> str | None:
    """행위자 연령대 태그(adult|minor|None) — 아동 데이터 파기 시 성인 생성분 보존 판별 축.

    생년월일(가입 수집)이 있으면 만 나이로 판정(경계 14세 = auth_service.GUARDIAN_CONSENT_AGE
    와 동일 — 순환 import라 상수만 재기술). 없으면 학교 입력 age(3~13)로 미성년만 확정.
    익명·정보 없음은 None(미상) — 'adult'로 추정하지 않는다(파기 판별에서 보수적으로 다룸)."""
    if not student_id:
        return None
    from app.models import StudentProfile

    st = db.get(StudentProfile, student_id)
    if st is None:
        return None
    if st.birth_date is not None:
        today = datetime.now().date()
        age = (
            today.year
            - st.birth_date.year
            - ((today.month, today.day) < (st.birth_date.month, st.birth_date.day))
        )
        return "adult" if age >= 14 else "minor"
    if st.age is not None:  # 학교가 입력하는 age는 3~13 범위 — 미성년 확정
        return "minor"
    return None


def record_behavior_event(
    db: Session,
    *,
    organization_id: str,
    student_id: str | None,
    source_type: str,
    behavior: dict | None,
    correct: bool,
    sample_label: str = "organic",
) -> None:
    """행동 이벤트 1건 적재 (+원시 궤적) — 인앱 게임('game')과 교육형 API('edu-api') 공용.

    궤적(trace)이 있으면 요약 지표는 서버가 궤적에서 직접 계산하고 원본을
    behavior_traces에 함께 남긴다. commit은 호출자 책임.

    sample_label: 실트래픽은 기본 'organic'(미검증). 레드팀 봇 주입만 'bot'을 명시해
    지도학습 음성 클래스를 만든다(그 외 값은 방어적으로 organic 처리).
    """
    from app.core.security import new_uuid
    from app.models import BehaviorSummary, BehaviorTrace

    b = behavior or {}

    def _f(k, d=0.0):
        try:
            v = float(b.get(k, d))
            return v if math.isfinite(v) else d  # NaN/inf → 기본값 (DB Float에 못 들어감)
        except (TypeError, ValueError):
            return d

    def _i(k, d=0):
        try:
            return int(b.get(k, d))
        except (TypeError, ValueError, OverflowError):  # int(inf)는 OverflowError
            return d

    trace = _parse_trace(b)
    m = _trace_metrics(trace) if trace else None
    # 입력 방식(mouse|touch|pen) — 그 외/미상은 unknown. 판정 모델의 기기 축.
    input_type = str(b.get("input_type") or "unknown").lower()
    if input_type not in ("mouse", "touch", "pen"):
        input_type = "unknown"
    solve_ms = min(3_600_000, max(0, _i("solve_time_ms")))
    label = sample_label if sample_label in ("organic", "bot", "human") else "organic"
    bid = new_uuid()
    db.add(
        BehaviorSummary(
            id=bid,
            organization_id=organization_id,
            student_id=student_id,
            source_type=source_type,
            input_type=input_type,
            # 적재 시점 자동 위험 판정 — 콘솔 위험 필터/리뷰 큐의 근거(이전엔 전부 low 고정)
            risk_level=_behavior_risk_level(
                solve_time_ms=solve_ms, correct=correct, trace=trace, metrics=m,
                input_type=input_type,
            ),
            # 실트래픽은 'organic'(미검증), 레드팀 봇 주입만 'bot' — 지도학습 정답표
            sample_label=label,
            # 행위자 연령대(adult|minor|None) — 아동 파기에서 성인 생성분을 지키는 축
            actor_band=_actor_band(db, student_id),
            solve_time_ms=solve_ms,
            # 자기신고 값도 상한 — 통계(그룹 평균) 부풀리기 차단
            path_length=m["path_length"] if m else min(PATH_LENGTH_CAP, max(0.0, _f("path_length"))),
            avg_speed=m["avg_speed"] if m else min(AVG_SPEED_CAP, max(0.0, _f("avg_speed"))),
            pause_count=m["pause_count"] if m else min(1000, max(0, _i("pause_count"))),
            retry_count=min(1000, max(0, _i("retry_count"))),
            drop_distance_norm=min(1.0, max(0.0, _f("drop_distance_norm"))),
            interaction_result="correct" if correct else "incorrect",
            # created_at과 같은 로컬 시각 (기존 utcnow는 콘솔 표시가 9시간 어긋났음)
            occurred_at=datetime.now(),
        )
    )
    if trace:
        db.add(
            BehaviorTrace(
                behavior_id=bid,
                points=trace["points"],
                point_count=len(trace["points"]),
                duration_ms=m["duration_ms"],
                box_w=trace["box_w"],
                box_h=trace["box_h"],
            )
        )


def record_behavior(
    db: Session,
    api: ApiKey,
    behavior: dict | None,
    correct: bool,
    verified_student=None,
) -> None:
    """교육형 API — 학습 중 수집된 행동데이터를 behavior_summaries에 적재.

    통과/실패가 목적이 아니라 이 데이터 수집이 목적. student_id는 외부 임베드 시 None(익명).

    verified_student: verify 라우트가 학생 JWT로 이미 검증한 학생(StudentProfile).
    인앱(1st-party) 풀이의 본인 귀속 경로 — 이게 없으면 아래 자기신고 검증 규칙만 적용된다.
    (기존엔 JWT 검증 학생도 '키 기관 일치' 재검증에 걸렸다: 1st-party 키의 기관은 CatChap,
    학생은 학교 소속이라 항상 불일치 → 인앱 학생 트래픽 전부가 익명으로 적재되던 버그.)
    """
    from app.models import StudentProfile

    b = behavior or {}

    if verified_student is not None:
        # 서버가 JWT로 검증한 신원 — 기관은 학생의 소속 학교로 기록해야
        # 기관별 집계·학년밴드가 실제 소속과 맞는다.
        record_behavior_event(
            db,
            organization_id=verified_student.organization_id or api.organization_id,
            student_id=verified_student.id,
            source_type="edu-api",
            behavior=b,
            correct=correct,
        )
        return

    # 클라이언트가 보낸 student_id는 신뢰하지 않는다 — 공개 site_key만으로 verify를 호출할 수
    # 있어, 위조 student_id가 아동/익명 통계(아동용 캡차 학습셋 근거)를 오염시킬 수 있다.
    # 실존하고 이 키의 기관 소속인 학생만 인정, 아니면 익명 처리.
    sid = b.get("student_id")
    if sid:
        sp = db.get(StudentProfile, str(sid))
        if sp is None or sp.organization_id != api.organization_id:
            sid = None

    record_behavior_event(
        db,
        organization_id=api.organization_id,
        student_id=sid,
        source_type="edu-api",
        behavior=b,
        correct=correct,
    )


def validate_verdict(db: Session, verdict_token: str) -> bool:
    """서버-대-서버 최종 검증 (고객 백엔드가 secret으로 호출) — 1회용.

    소비 기록을 DB(UNIQUE jti)에 원자적으로 남겨 멀티워커/재시작에도 리플레이를 차단한다.
    """
    data = _unsign(verdict_token)
    if data is None or data.get("v") != 1:
        return False
    return _consume(db, "verdict", data.get("n", ""), data.get("exp", 0))
