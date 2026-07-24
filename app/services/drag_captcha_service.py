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
import math
import secrets
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

from app.core.config import get_settings
from app.db.session import engine

CANVAS_TYPE = "object_drag"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _secret_bytes() -> bytes:
    # captcha 전용 시크릿을 JWT_SECRET_KEY에서 파생(도메인 분리). 별도 키 관리 불필요.
    return hashlib.sha256(("drag-captcha:" + get_settings().JWT_SECRET_KEY).encode()).digest()


def hash_value(value: str) -> str:
    return hmac.new(_secret_bytes(), value.encode(), hashlib.sha256).hexdigest()


def _final_dir() -> Path:
    # media/captcha 아래 final(images/, pieces/) 구조. image_path/piece_path는 이 루트 상대경로.
    root = Path(get_settings().CAPTCHA_MEDIA_DIR)
    return root if root.is_absolute() else (Path.cwd() / root)


def safe_asset(relative: str) -> Path:
    root = _final_dir().resolve()
    candidate = (root / relative).resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise FileNotFoundError(relative)
    return candidate


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
    with _cursor() as (_, cur):
        cur.execute(
            "SELECT * FROM captcha_questions WHERE status='active' AND review_status='approved' "
            "ORDER BY RAND() LIMIT 1"
        )
        question = cur.fetchone()
        if not question:
            return None
        cur.execute("SELECT * FROM captcha_objects WHERE question_id=%s ORDER BY id", (question["id"],))
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


def get_question(question_id: str) -> dict[str, Any] | None:
    with _cursor() as (_, cur):
        cur.execute("SELECT * FROM captcha_questions WHERE id=%s", (question_id,))
        question = cur.fetchone()
        if not question:
            return None
        cur.execute("SELECT * FROM captcha_objects WHERE question_id=%s ORDER BY id", (question_id,))
        question["objects"] = cur.fetchall()
        return question


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
def create_challenge(purpose: str, session_id: str, ip: str) -> dict[str, Any]:
    """새 챌린지 발급. 실패 시 예외 코드는 라우터가 HTTP로 매핑."""
    s = get_settings()
    ip_hash = hash_value(ip)
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


def asset_path(challenge_id: str, asset_id: str) -> Path:
    with _cursor() as (_, cur):
        challenge = _challenge_for_verify(cur, challenge_id)
    if not challenge:
        raise FileNotFoundError("challenge")
    question = get_question(challenge["question_id"])
    if question is None:
        raise FileNotFoundError("question")
    if asset_id == "image":
        return safe_asset(question["image_path"])
    mapping = next((m for m in challenge["objects"] if m["temporary_object_id"] == asset_id), None)
    if not mapping or not mapping.get("piece_path"):
        raise FileNotFoundError("piece")
    return safe_asset(mapping["piece_path"])


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
