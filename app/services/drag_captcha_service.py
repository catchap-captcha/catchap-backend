"""메인 캡차(사람 확인) — ms '다중 객체 드래그' 캡차의 런타임을 우리 백엔드로 자체 이식.

왜: 종전 메인 캡차(forest)를 드래그 캡차로 통째 교체한다. 외부 ms 서비스/GPU에 의존하지
않도록 챌린지 발급·에셋 서빙·검증·토큰을 전부 우리 백엔드+DB가 처리한다(자체 완결 → VPC
재구성/서버 교체와 무관하게 동작). 문제 저작(라벨링·생성)은 이식 범위 밖이며 승인된 문제
데이터만 우리 DB(captcha_questions/objects)와 media/captcha 이미지로 들여온다.

이식 원본: ms drag-captcha/app/{db.py,captcha.py,main.py}. DB 접근은 우리 SQLAlchemy
엔진의 raw 커넥션(DictCursor)로 ms의 검증된 쿼리를 그대로 사용한다. 토큰 해시 시크릿은
JWT_SECRET_KEY에서 파생(별도 env 불필요) — 키가 동일 복원되면 토큰 규약도 유지된다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import random
import secrets
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

from app.core.config import get_settings
from app.services import media_storage
from app.db.session import engine

CANVAS_TYPE = "object_drag"

_log = logging.getLogger(__name__)

# 만료 행 청소 — 레거시 forest 캡차(forest_captcha._sweep)와 같은 '확률적 청소' 관례를 따른다.
# 발급 요청마다 DELETE를 돌리면 핫패스가 무거워지므로 2%만 수행하고, 한 번에 지우는 양도 묶는다.
_PURGE_PROBABILITY = 0.02
_PURGE_BATCH = 500
# 만료 후 이만큼 더 지난 것만 지운다. 토큰 수명이 챌린지 수명보다 길기 때문에 필요한 유예다(아래 참조).
_PURGE_GRACE_SECONDS = 60


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _secret_bytes() -> bytes:
    # captcha 전용 시크릿을 JWT_SECRET_KEY에서 파생(도메인 분리). 별도 키 관리 불필요.
    return hashlib.sha256(("drag-captcha:" + get_settings().JWT_SECRET_KEY).encode()).digest()


def hash_value(value: str) -> str:
    return hmac.new(_secret_bytes(), value.encode(), hashlib.sha256).hexdigest()


# 캡차 자산 확장자 화이트리스트 — 래스터 이미지만(SVG 금지: <img> 인라인 서빙이라 스크립트
# 삽입 위험). 강의 문항 이미지와 같은 원칙.
_ASSET_MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
}


def safe_asset_key(relative: str) -> tuple[str, str]:
    """DB에 저장된 상대경로(`images/x.jpg`) → (저장소 키, Content-Type).

    왜 경로가 아니라 키인가: 파일이 서버 디스크가 아니라 Object Storage 에 있을 수 있다.
    K8s 에서 파드를 늘리면 파드마다 로컬 디스크가 달라 자산을 못 찾기 때문이다.
    실제 위치는 저장소 구현(media_storage.py)이 안다.

    ★경로 조작 차단은 그대로다 — 종전에는 resolve() 후 루트 하위인지 봤고, 지금은
    저장소 계층의 _validate_key 가 `..`·절대경로·비허용문자를 막는다(ValueError).
    ★확장자 화이트리스트를 여기서 한 번 더 본다 — DB 값이 오염돼도 임의 파일이 못 나간다."""
    rel = (relative or "").strip().replace("\\", "/").lstrip("/")
    ext = Path(rel).suffix.lower()
    media_type = _ASSET_MEDIA_TYPES.get(ext)
    if not rel or media_type is None:
        raise FileNotFoundError(relative)
    key = f"captcha/{rel}"
    try:
        media_storage._validate_key(key)
    except ValueError as e:
        raise FileNotFoundError(relative) from e
    return key, media_type


@contextmanager
def _cursor() -> Iterator[Any]:
    raw = engine.raw_connection()
    try:
        cur = raw.cursor(DictCursor)
        try:
            yield raw, cur
        finally:
            cur.close()
    finally:
        raw.close()


# ── DB 접근(ms db.py 이식) ──
def active_question() -> dict[str, Any] | None:
    # 챌린지 구성에 실제로 쓰는 컬럼만 선택(SELECT * 대신) — 안 쓰는 reviewer/source 등 전송 제거.
    # image_path는 발급 시점엔 불필요(에셋 서빙에서만 조회)하므로 여기서 안 가져온다.
    with _cursor() as (_, cur):
        cur.execute(
            "SELECT id, instruction_ko, image_width, image_height FROM captcha_questions "
            "WHERE status='active' AND review_status='approved' ORDER BY RAND() LIMIT 1"
        )
        question = cur.fetchone()
        if not question:
            return None
        cur.execute(
            "SELECT id, role, bbox_x, bbox_y, bbox_width, bbox_height FROM captcha_objects "
            "WHERE question_id=%s ORDER BY id",
            (question["id"],),
        )
        question["objects"] = cur.fetchall()
        return question


def _request_pattern(cur: Any, session_id: str, ip_hash: str) -> dict[str, int]:
    cur.execute(
        "SELECT COUNT(*) n FROM captcha_challenges_v2 WHERE client_ip_hash=%s "
        "AND created_at>UTC_TIMESTAMP(6)-INTERVAL 1 MINUTE",
        (ip_hash,),
    )
    ip_1m = int(cur.fetchone()["n"])
    cur.execute(
        "SELECT COUNT(*) n FROM captcha_challenges_v2 WHERE session_id=%s "
        "AND created_at>UTC_TIMESTAMP(6)-INTERVAL 10 MINUTE",
        (session_id,),
    )
    sess_10m = int(cur.fetchone()["n"])
    cur.execute(
        "SELECT COUNT(*) n FROM captcha_attempts a JOIN captcha_challenges_v2 c ON c.id=a.challenge_id "
        "WHERE c.session_id=%s AND a.is_correct=0 AND a.created_at>UTC_TIMESTAMP(6)-INTERVAL 10 MINUTE",
        (session_id,),
    )
    fail_10m = int(cur.fetchone()["n"])
    return {"ip_challenges_1m": ip_1m, "session_challenges_10m": sess_10m, "session_failures_10m": fail_10m}


def _challenge_for_verify(cur: Any, challenge_id: str) -> dict[str, Any] | None:
    cur.execute("SELECT * FROM captcha_challenges_v2 WHERE id=%s", (challenge_id,))
    challenge = cur.fetchone()
    if not challenge:
        return None
    cur.execute(
        "SELECT m.temporary_object_id, m.object_id, o.role, o.piece_path FROM captcha_challenge_objects m "
        "JOIN captcha_objects o ON o.id=m.object_id WHERE m.challenge_id=%s",
        (challenge_id,),
    )
    challenge["objects"] = cur.fetchall()
    return challenge


# ── 행동 위험 점수(ms main.py summarize 이식) ──
def summarize(events: list[Any], selected: set[str], targets: set[str], duration_ms: int,
              correct: bool, pattern: dict[str, int], ip_changed: bool) -> dict:
    segments: list[list[Any]] = []
    current: list[Any] = []
    for e in events:
        if e.type == "drag_start" and e.x is not None and e.y is not None:
            if current:
                segments.append(current)
            current = [e]
        elif current and e.type in {"pointer_move", "drop"} and e.x is not None and e.y is not None:
            current.append(e)
            if e.type == "drop":
                segments.append(current)
                current = []
    if current:
        segments.append(current)
    distances: list[float] = []
    speeds: list[float] = []
    turns = 0.0
    pause_count = 0
    for segment in segments:
        for a, b in zip(segment, segment[1:]):
            distance = math.hypot((b.x or 0) - (a.x or 0), (b.y or 0) - (a.y or 0))
            dt = max(1, b.timestamp_ms - a.timestamp_ms)
            distances.append(distance)
            speeds.append(distance / dt)
            pause_count += dt > 450
        for a, b, c in zip(segment, segment[1:], segment[2:]):
            ab = math.atan2((b.y or 0) - (a.y or 0), (b.x or 0) - (a.x or 0))
            bc = math.atan2((c.y or 0) - (b.y or 0), (c.x or 0) - (b.x or 0))
            turns += abs(math.atan2(math.sin(bc - ab), math.cos(bc - ab)))
    average = sum(speeds) / len(speeds) if speeds else 0.0
    variance = sum((x - average) ** 2 for x in speeds) / len(speeds) if speeds else 0.0
    loaded = next((e.timestamp_ms for e in events if e.type == "challenge_loaded"), None)
    down = next((e.timestamp_ms for e in events if e.type == "pointer_down"), None)
    reaction = max(0, down - loaded) if down is not None and loaded is not None else None
    speed_cv = (math.sqrt(variance) / average) if average else 0.0
    max_jump = max(distances, default=0.0)
    move_count = sum(e.type == "pointer_move" for e in events)
    removal_order = [e.object_id for e in events if e.type == "object_removed" and e.object_id]
    components = {"answer_accuracy": 0, "drag_behavior": 0, "reaction_exploration": 0,
                 "selection_correction": 0, "session_behavior": 0, "api_pattern": 0}
    if not correct:
        components["answer_accuracy"] = 30
    if move_count < 3:
        components["drag_behavior"] += 15
    if move_count >= 3 and turns < 0.04:
        components["drag_behavior"] += 7
    if move_count >= 3 and speed_cv < 0.035:
        components["drag_behavior"] += 7
    if max_jump > 0.45:
        components["drag_behavior"] += 8
    components["drag_behavior"] = min(25, components["drag_behavior"])
    if reaction is None:
        components["reaction_exploration"] = 12
    elif reaction < 300:
        components["reaction_exploration"] = 15
    elif reaction < 600:
        components["reaction_exploration"] = 10
    elif reaction < 1000:
        components["reaction_exploration"] = 5
    if len(selected - targets):
        components["selection_correction"] += 7
    if not removal_order and reaction is not None and reaction < 600:
        components["selection_correction"] += 3
    components["selection_correction"] = min(10, components["selection_correction"])
    if pattern["session_challenges_10m"] >= 8:
        components["session_behavior"] = 10
    elif pattern["session_challenges_10m"] >= 4:
        components["session_behavior"] = 5
    if pattern["session_failures_10m"] >= 3:
        components["session_behavior"] = 10
    if pattern["ip_challenges_1m"] >= 10:
        components["api_pattern"] = 10
    elif pattern["ip_challenges_1m"] >= 5:
        components["api_pattern"] = 5
    if ip_changed:
        components["api_pattern"] = min(10, components["api_pattern"] + 5)
    risk_score = sum(components.values())
    risk_level = ("normal" if risk_score < 30 else "suspicious" if risk_score < 60
                  else "high" if risk_score < 80 else "automated")
    return {
        "reaction_time_ms": reaction, "drag_count": sum(e.type == "drag_start" for e in events),
        "wrong_object_count": len(selected - targets), "average_speed": average,
        "speed_variance": variance, "path_length": sum(distances), "path_curvature": turns,
        "pause_count": pause_count, "total_duration_ms": duration_ms,
        "risk_components": components, "risk_score": risk_score, "risk_level": risk_level,
    }


# ── 고수준 API(ms main.py 엔드포인트 로직 이식) ──
def _maybe_purge_expired() -> None:
    """만료된 챌린지를 확률적으로 청소한다(자식 행은 FK CASCADE로 함께 삭제).

    왜 필요한가: 이식 원본에는 이 루틴이 없어 captcha_challenges_v2 / challenge_objects /
    attempts / tokens 가 영구히 쌓였다. 레거시 forest 캡차는 같은 문제를 `_sweep`(확률 2%)로
    해결하고 있었으므로 그 관례를 그대로 따른다.

    ★유예(_PURGE_GRACE_SECONDS)가 반드시 필요한 이유: 토큰 수명
    (CAPTCHA_VERIFICATION_TTL_SECONDS=300초)이 챌린지 수명(CAPTCHA_CHALLENGE_TTL_SECONDS=180초)
    보다 길다. captcha_tokens 는 challenge_id 에 ON DELETE CASCADE 로 묶여 있어서, 만료 즉시
    챌린지를 지우면 방금 캡차를 푼 사용자가 로그인 요청을 보내는 사이에 토큰이 증발해
    **정상 로그인이 막힌다**. 그래서 '토큰이 아직 살아 있을 수 있는 최대 시간'만큼 지난
    것만 지운다.

    청소는 부가 작업이라 발급과 트랜잭션을 분리하고, 실패해도 캡차 발급을 막지 않는다.
    """
    if random.random() >= _PURGE_PROBABILITY:
        return
    s = get_settings()
    cutoff = utcnow() - timedelta(seconds=s.CAPTCHA_VERIFICATION_TTL_SECONDS + _PURGE_GRACE_SECONDS)
    try:
        with _cursor() as (raw, cur):
            # ORDER BY + LIMIT 으로 idx_challenge_expiry 를 타면서 한 번에 지우는 양을 묶는다.
            cur.execute(
                "DELETE FROM captcha_challenges_v2 WHERE expires_at < %s ORDER BY expires_at LIMIT %s",
                (cutoff, _PURGE_BATCH),
            )
            raw.commit()
    except Exception:  # noqa: BLE001 — 유지보수 작업이 사용자 요청을 깨뜨리면 안 된다
        _log.warning("drag captcha 만료 챌린지 청소 실패(무시하고 진행)", exc_info=True)


def create_challenge(purpose: str, session_id: str, ip: str) -> dict[str, Any]:
    """새 챌린지 발급. 실패 시 예외 코드는 라우터가 HTTP로 매핑."""
    s = get_settings()
    ip_hash = hash_value(ip)
    _maybe_purge_expired()
    with _cursor() as (raw, cur):
        pattern = _request_pattern(cur, session_id, ip_hash)
        if pattern["ip_challenges_1m"] >= s.CAPTCHA_MAX_CHALLENGES_PER_MINUTE:
            raise _CaptchaError(429, "Too many CAPTCHA requests")
    question = active_question()
    if not question:
        raise _CaptchaError(503, "No approved CAPTCHA questions")
    challenge_id = str(uuid.uuid4())
    now = utcnow()
    expires = now + timedelta(seconds=s.CAPTCHA_CHALLENGE_TTL_SECONDS)
    mappings = [(obj["id"], f"tmp_{secrets.token_urlsafe(8)}")
                for obj in question["objects"] if obj["role"] != "invalid"]
    temporary = {object_id: temp for object_id, temp in mappings}
    with _cursor() as (raw, cur):
        cur.execute(
            "INSERT INTO captcha_challenges_v2 (id,question_id,session_id,purpose,expires_at,status,created_at,client_ip_hash) "
            "VALUES(%s,%s,%s,%s,%s,'issued',%s,%s)",
            (challenge_id, question["id"], session_id, purpose, expires, now, ip_hash),
        )
        cur.executemany(
            "INSERT INTO captcha_challenge_objects(challenge_id,object_id,temporary_object_id) VALUES(%s,%s,%s)",
            [(challenge_id, oid, tmp) for oid, tmp in mappings],
        )
        raw.commit()
    objects = [{"object_id": temporary[obj["id"]],
                "hit_region": [obj["bbox_x"], obj["bbox_y"], obj["bbox_width"], obj["bbox_height"]],
                "preview_url": f"/api/v1/captcha/drag/assets/{challenge_id}/{temporary[obj['id']]}"}
               for obj in question["objects"] if obj["id"] in temporary]
    secrets.SystemRandom().shuffle(objects)
    return {
        "challenge_id": challenge_id, "type": CANVAS_TYPE, "instruction": question["instruction_ko"],
        "image_url": f"/api/v1/captcha/drag/assets/{challenge_id}/image",
        "width": question["image_width"], "height": question["image_height"], "objects": objects,
        "drop_zone": {"x": 0.72, "y": 0.68, "width": 0.25, "height": 0.25},
        "expires_at": expires.isoformat() + "Z",
    }


def asset_key(challenge_id: str, asset_id: str) -> tuple[str, str]:
    """챌린지의 자산 → (저장소 키, Content-Type).

    ★이름을 asset_path → asset_key 로 바꿨다. 반환형이 Path 에서 (키, 타입) 튜플로 바뀌는데
    이름을 그대로 두면 안 고친 호출부가 튜플을 Path 처럼 다뤄 조용히 잘못 동작한다."""
    # 핫패스(챌린지당 이미지 1 + 조각 N회 호출) — 필요한 경로 한 개만 단일 쿼리로 조회.
    # 종전엔 챌린지+객체맵+질문+질문객체까지 4쿼리·2커넥션을 읽었으나 실제로 필요한 건 경로뿐.
    with _cursor() as (_, cur):
        if asset_id == "image":
            cur.execute(
                "SELECT q.image_path FROM captcha_challenges_v2 c JOIN captcha_questions q ON q.id=c.question_id "
                "WHERE c.id=%s",
                (challenge_id,),
            )
            row = cur.fetchone()
            if not row:
                raise FileNotFoundError("challenge")
            return safe_asset_key(row["image_path"])
        cur.execute(
            "SELECT o.piece_path FROM captcha_challenge_objects m JOIN captcha_objects o ON o.id=m.object_id "
            "WHERE m.challenge_id=%s AND m.temporary_object_id=%s",
            (challenge_id, asset_id),
        )
        row = cur.fetchone()
        if not row or not row["piece_path"]:
            raise FileNotFoundError("piece")
        return safe_asset_key(row["piece_path"])


def verify(challenge_id: str, selected_ids: list[str], session_id: str, duration_ms: int,
           events: list[Any], ip: str) -> dict[str, Any]:
    s = get_settings()
    ip_hash = hash_value(ip)
    with _cursor() as (raw, cur):
        challenge = _challenge_for_verify(cur, challenge_id)
        if not challenge or challenge["session_id"] != session_id:
            raise _CaptchaError(404, "Challenge not found")
        if challenge["status"] == "passed":
            raise _CaptchaError(409, "Challenge already used")
        if challenge["expires_at"] <= utcnow():
            raise _CaptchaError(410, "Challenge expired")
        if challenge["attempt_count"] >= s.CAPTCHA_MAX_ATTEMPTS:
            raise _CaptchaError(429, "No attempts remaining")
        submitted = set(selected_ids)
        targets = {o["temporary_object_id"] for o in challenge["objects"] if o["role"] == "target"}
        valid = {o["temporary_object_id"] for o in challenge["objects"]}
        correct = submitted == targets and submitted <= valid
        reason = None if correct else ("unknown_object" if not submitted <= valid else "incorrect_selection")
        pattern = _request_pattern(cur, session_id, ip_hash)
        summary = summarize(events, submitted, targets, duration_ms, correct, pattern,
                            ip_hash != challenge["client_ip_hash"])
        # attempt + behavior 기록, 챌린지 상태 갱신 (ms record_attempt 이식)
        cur.execute(
            "INSERT INTO captcha_attempts(challenge_id,selected_object_ids,is_correct,failure_reason,"
            "duration_ms,behavior_summary,raw_event_path,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (challenge_id, json.dumps(list(submitted)), correct, reason, duration_ms,
             json.dumps(summary), None, utcnow()),
        )
        # 행동 요약은 captcha_attempts.behavior_summary(JSON)에 이미 저장했다. ms의 별도
        # behavior_summaries 테이블은 우리 백엔드의 동명 테이블과 충돌하므로 이식하지 않는다.
        cur.execute(
            "UPDATE captcha_challenges_v2 SET attempt_count=attempt_count+1,status=%s,verified_at=%s WHERE id=%s",
            ("passed" if correct else "failed", utcnow() if correct else None, challenge_id),
        )
        raw.commit()
        attempt_no = challenge["attempt_count"] + 1

    if not correct:
        return {"success": False, "remaining_attempts": max(0, s.CAPTCHA_MAX_ATTEMPTS - attempt_no)}
    if summary["risk_score"] >= s.CAPTCHA_BLOCK_SCORE:
        return {"success": False, "blocked": True, "risk_level": summary["risk_level"]}
    if summary["risk_score"] >= s.CAPTCHA_STEP_UP_SCORE:
        return {"success": False, "step_up": True, "risk_level": summary["risk_level"]}
    token = secrets.token_urlsafe(32)
    with _cursor() as (raw, cur):
        cur.execute(
            "INSERT INTO captcha_tokens(challenge_id,token_hash,purpose,session_id,expires_at,created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            (challenge_id, hash_value(token), challenge["purpose"], session_id,
             utcnow() + timedelta(seconds=s.CAPTCHA_VERIFICATION_TTL_SECONDS), utcnow()),
        )
        raw.commit()
    return {"success": True, "captcha_token": token, "expires_in": s.CAPTCHA_VERIFICATION_TTL_SECONDS}


def verify_and_consume_token(token: str | None, purpose: str | None = None,
                             session_id: str | None = None) -> bool:
    """캡차 토큰을 검증·소비한다(1회용). purpose/session_id가 주어지면 바인딩까지 검사하고,
    None이면 그 조건은 생략한다 — 로그인/회원가입 스텝업(auth_service)은 요청에 session_id가
    없어 토큰 유효성(미소비·미만료)만으로 소비한다. verify가 발급한 토큰만 존재하므로 안전.
    """
    if not token:
        return False
    where = ["token_hash=%s", "consumed_at IS NULL", "expires_at>%s"]
    params: list[Any] = [hash_value(token), utcnow()]
    if purpose is not None:
        where.insert(1, "purpose=%s")
        params.insert(1, purpose)
    if session_id is not None:
        where.append("session_id=%s")
        params.append(session_id)
    sql = "UPDATE captcha_tokens SET consumed_at=%s WHERE " + " AND ".join(where)
    with _cursor() as (raw, cur):
        cur.execute(sql, (utcnow(), *params))
        ok = cur.rowcount == 1
        raw.commit()
    return ok


def has_active_questions() -> bool:
    return active_question() is not None


class _CaptchaError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


CaptchaError = _CaptchaError
