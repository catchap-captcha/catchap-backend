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

CHALLENGE_TTL = 180  # 초
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
            expires_at=datetime.utcfromtimestamp(exp) if exp else None,
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
    """이 키의 제품이 기관 요금제로 허용되는지 + 이번 달 quota 확인."""
    plan = plan_for_org(db, api.organization_id)
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
    now = datetime.utcnow()
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
    api.last_used_at = datetime.utcnow()


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
    """meta(subj/qid/day/rp)는 토큰에만 서명 포함 — verify에서 학생 적립·오답노트에 쓴다."""
    token = _sign(
        {"k": kind, "a": answer, "exp": time.time() + CHALLENGE_TTL, "n": secrets.token_hex(16),
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
                         "reference", "mapStyle", "compass", "start", "layout", "audio",
                         "template", "glyph", "character", "dest", "dangers",
                         "flag", "cols", "rows", "slots", "pieces")


def _in_box(px: float, py: float, box: dict, pad: float = 0.0) -> bool:
    """점(px,py)이 box(x,y,w,h, 0~1)의 [±pad] 안에 있는지."""
    return (box["x"] - pad <= px <= box["x"] + box["w"] + pad
            and box["y"] - pad <= py <= box["y"] + box["h"] + pad)


def _grade_route(answer, dest: dict, dangers: list) -> bool:
    """길찾기 채점 — 그린 경로가 (1) 도착 지점에서 끝나고 (2) 어떤 위험존도 지나지 않는다.

    시작점 근처에서 출발해 도착 상자 안에서 끝나야 하며, 경로 어느 점도 위험존을 통과하면 실패.
    """
    pts = _clean_xy_points(answer, 600)
    if len(pts) < TRACE_MIN_USER_POINTS:
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

    t = q["type"]
    if t == "multi":
        return _wrap("select_all", sorted(q["answer"]), public, meta)
    if t in ("connect", "sort", "puzzle"):
        # 매핑 채점 — {leftId:rightId}/{itemId:binId}/{slotId:pieceId} 딕셔너리 정확 비교
        return _wrap("match", dict(q["answer"]), public, meta)
    if t == "order":
        # 순서 채점 — 위젯이 [cardId,...] 제출, 서버가 리스트 정확 비교
        return _wrap("sequence", list(q["answer"]), public, meta)
    if t == "trace":
        # 따라쓰기 — 위젯 trace_path 렌더러가 궤적 제출, _grade_trace로 채점.
        # template(안내 점선)은 비밀이 아니라 public에 노출한다(정답 유출 아님).
        public = {**public, "type": "trace_path", "path": q["template"]}
        return _wrap("trace", q["template"], public, meta)
    if t == "route":
        # 길찾기 — 위젯이 경로 궤적 제출, 끝점 dest 도달 + 위험존 회피로 채점.
        # dest/dangers는 화면에 보이는 요소라 노출 정상(정답 좌표가 아님).
        return _wrap("route", {"dest": q["dest"], "dangers": q.get("dangers", [])}, public, meta)
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
    if subject not in subject_banks.LIVE_SUBJECTS:
        # 뱅크 없는 과목은 동작형만 낸다 — '정답 예시' 데모 문항은 폐기
        # (문제은행 연동 전까지: 데모는 정답이 뻔해 학습기록·코인 적립 대상이 될 수 없음)
        if random.random() < 0.5:
            return _edu_drag_challenge(subject, meta)
        return _edu_trace_challenge(subject, meta)
    # 외부 임베드(learning=False): 드래그·그리기 궤적이 아동용 캡차 학습셋의 핵심 재료라
    # 절반은 동작형 문제를 낸다. 1st-party 인앱 학습(learning=True)은 과목 무관 조작형이
    # 학습 흐름을 깨므로 건너뛰고 실제 문제만 낸다(예: 영어엔 영어 문제만).
    if not learning:
        roll = random.random()
        if roll < 0.25:
            return _edu_drag_challenge(subject, meta)
        if roll < 0.5:
            return _edu_trace_challenge(subject, meta)
    # 실문항 뱅크 (생활=ms / 수학·과학=my / 사회=sw / 영어=ms english / 국어=jy — capcha_service 이식)
    pool = subject_banks.playable_pool(subject)
    q = random.choice(pool)
    return _wrap_bank_question(subject, q, meta)


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
    # 발급 시 서명해 둔 문항 메타(과목·문항id·일차·복습) — 엔드포인트가 학생 적립에 쓰고 응답 전 제거
    extra: dict = {"meta": {k: data[k] for k in ("subj", "qid", "day", "rp", "chapter", "stage") if k in data}}
    if kind == "select_all":
        # answer 타입 미방어 시 정수 등 비반복형 입력이 TypeError → 공개 엔드포인트 500
        picked = sorted(str(x) for x in answer) if isinstance(answer, (list, tuple)) else []
        ok = len(picked) > 0 and picked == sorted(str(x) for x in target)
    elif kind == "match":
        # connect/sort — {leftId:rightId}/{itemId:binId} 딕셔너리 정확 일치.
        # 비-dict 입력(위조·정수)은 조용히 오답 처리(500 방지). 부분 정답 없음.
        sub = {str(k): str(v) for k, v in answer.items()} if isinstance(answer, dict) else {}
        ok = bool(target) and sub == {str(k): str(v) for k, v in target.items()}
    elif kind == "sequence":
        # order — [cardId,...] 순서 정확 일치. 비-list 입력은 오답.
        seq = [str(x) for x in answer] if isinstance(answer, (list, tuple)) else []
        ok = len(seq) > 0 and seq == [str(x) for x in target]
    elif kind == "drop":
        # 끌어다 놓기 — 드롭 지점 거리로 채점. 거리는 서버 진실값으로 행동 데이터에 기록
        ok, dist = _grade_drop(answer, target)
        extra["drop_distance_norm"] = dist
    elif kind == "trace":
        # 따라 그리기 — 궤적 유사도(커버리지+이탈)로 채점
        ok = _grade_trace(answer, target)
    elif kind == "route":
        # 길찾기 — 끝점이 도착지 + 위험존 미통과
        ok = _grade_route(answer, target.get("dest", {}), target.get("dangers", [])) if isinstance(target, dict) else False
    else:  # single
        ok = str(answer) == str(target)
    if not ok:
        # 오답에도 정답을 내린다(교육형 피드백 "정답은 X"용) — 챌린지는 1회 소비돼
        # 이미 무효라 재제출 오라클이 되지 않고, 매 챌린지 정답이 새로 나와 파밍 가치도 없다.
        return {"success": False, "answer": target, **extra}
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
    move_ms = 0
    pauses = 0
    for i in range(1, len(pts)):
        t0, x0, y0 = pts[i - 1]
        t1, x1, y1 = pts[i]
        dt = t1 - t0
        path += math.hypot((x1 - x0) * w, (y1 - y0) * h)
        if dt >= TRACE_PAUSE_GAP_MS:
            pauses += 1
        else:
            move_ms += dt
    return {
        "path_length": round(min(PATH_LENGTH_CAP, path), 1),
        "avg_speed": round(min(AVG_SPEED_CAP, path / move_ms), 3) if move_ms > 0 else 0.0,
        "pause_count": min(1000, pauses),
        "duration_ms": max(0, int(pts[-1][0] - pts[0][0])),
    }


def record_behavior_event(
    db: Session,
    *,
    organization_id: str,
    student_id: str | None,
    source_type: str,
    behavior: dict | None,
    correct: bool,
) -> None:
    """행동 이벤트 1건 적재 (+원시 궤적) — 인앱 게임('game')과 교육형 API('edu-api') 공용.

    궤적(trace)이 있으면 요약 지표는 서버가 궤적에서 직접 계산하고 원본을
    behavior_traces에 함께 남긴다. commit은 호출자 책임.
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
    bid = new_uuid()
    db.add(
        BehaviorSummary(
            id=bid,
            organization_id=organization_id,
            student_id=student_id,
            source_type=source_type,
            input_type=input_type,
            # sample_label은 모델 기본값 'organic'(실트래픽·미검증) — 봇 주입 시에만 명시 설정.
            solve_time_ms=min(3_600_000, max(0, _i("solve_time_ms"))),
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


def record_behavior(db: Session, api: ApiKey, behavior: dict | None, correct: bool) -> None:
    """교육형 API — 학습 중 수집된 행동데이터를 behavior_summaries에 적재.

    통과/실패가 목적이 아니라 이 데이터 수집이 목적. student_id는 외부 임베드 시 None(익명).
    """
    from app.models import StudentProfile

    b = behavior or {}

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
