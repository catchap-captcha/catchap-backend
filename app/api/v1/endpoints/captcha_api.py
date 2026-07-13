"""공개 캡차 API — 외부 사이트가 site_key로 호출 (메인 캡차 + 교육형).

  POST /captcha/v1/challenge   site_key(헤더) → 챌린지 발급 (요금제 게이팅·사용량 기록)
  POST /captcha/v1/verify      site_key + challenge_token + answer → 서버 채점 → verdict 토큰
  POST /captcha/v1/validate    secret_key + verdict_token → 최종 통과 검증 (고객 서버용, 1회용)

교육형도 같은 경로에 product='edu' 키를 쓰면 동작 (키에 과목이 박혀 있음).
"""

import re
from datetime import date
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import Response
from jwt import PyJWTError
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import decode_token, sha256_hash
from app.db.session import get_db
from app.models import ApiKey, DailyQuizStatus, LearningAttempt, StudentProfile
from app.services import auth_service
from app.services import captcha_service as cs

router = APIRouter(prefix="/captcha/v1", tags=["captcha-api"])

# 교육형 인앱 세션 길이 — 오늘의퀴즈는 과목당 이 문항 수를 채우면 완료 신고
EDU_SESSION_TOTAL = 5

# 공개 엔드포인트 IP 레이트리밋 (분당) — 월 quota와 별개로 버스트/스크래핑 억제.
# 학교 NAT 뒤 다수 학생을 감안해 넉넉히, 봇 폭주는 막는 수준.
RATE_CHALLENGE_PER_MIN = 120
RATE_VERIFY_PER_MIN = 120
RATE_VALIDATE_PER_MIN = 240

# 듣기(영어 sound-match) 오디오 서빙 — 불투명 파일명만 화이트리스트 허용(경로조작·정답유출 차단)
_AUDIO_DIR = Path(__file__).resolve().parents[3] / "static" / "audio"  # app/static/audio


@lru_cache(maxsize=128)
def _audio_bytes(name: str) -> bytes | None:
    """오디오 파일 프로세스당 1회 읽기 — 화이트리스트 파일만 캐시되므로 크기 유한."""
    path = _AUDIO_DIR / name
    return path.read_bytes() if path.exists() else None


@router.get("/audio/{name}")
def audio(name: str):
    """듣기 문항 오디오(.m4a) 서빙. 파일명은 불투명(snd-NN)이라 정답 단어를 노출하지 않는다.
    화이트리스트(english_listen.AUDIO_FILES) 밖 이름은 404 — 임의 파일 접근 차단."""
    from app.services.english_listen import AUDIO_FILES

    if name not in AUDIO_FILES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    content = _audio_bytes(name)
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    return Response(
        content=content,
        media_type="audio/mp4",
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=86400"},
    )


_FLAG_DIR = Path(__file__).resolve().parents[3] / "static" / "flags"  # app/static/flags


@lru_cache(maxsize=512)
def _flag_bytes(code: str) -> bytes | None:
    """국기 SVG 프로세스당 1회 읽기 — 2글자 코드만 오므로 키 공간 유한."""
    path = _FLAG_DIR / f"{code}.svg"
    return path.read_bytes() if path.exists() else None


@router.get("/flag/{code}")
def flag(code: str):
    """국기 조각 맞추기 문항용 국기 SVG 서빙. 화이트리스트(2글자 국가코드) 밖은 404."""
    if not code.isalpha() or len(code) != 2:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    content = _flag_bytes(code.lower())
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    return Response(
        content=content,
        media_type="image/svg+xml",
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=86400"},
    )


_IMG_DIR = Path(__file__).resolve().parents[3] / "static" / "captcha-img"  # app/static/captcha-img
_IMG_RE = re.compile(r"^(symbols|cpr|aed)/[a-z0-9-]+\.png$")


@lru_cache(maxsize=256)
def _img_bytes(rel: str) -> bytes | None:
    path = _IMG_DIR / rel
    return path.read_bytes() if path.exists() else None


@router.get("/img/{folder}/{name}")
def captcha_img(folder: str, name: str):
    """문항 이미지(지도기호·CPR/AED 사진) 서빙 — 정규식 화이트리스트 밖은 404(경로조작 차단)."""
    rel = f"{folder}/{name}"
    if not _IMG_RE.match(rel):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    content = _img_bytes(rel)
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    return Response(
        content=content,
        media_type="image/png",
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=86400"},
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _key(db: Session, x_site_key: str | None) -> ApiKey:
    if not x_site_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="X-Site-Key 헤더가 필요합니다.")
    return cs.auth_site_key(db, x_site_key)


def _throttle(db: Session, request: Request, kind: str, limit: int) -> None:
    """IP 레이트리밋 — site_key 인증보다 먼저 실행해 무효 키 연타(DB 조회 DoS)도 막는다."""
    auth_service.rate_limit(db, f"cap{kind}:{_client_ip(request)}", limit=limit, window_seconds=60)


def _origin_guard(db: Session, request: Request, api: ApiKey) -> None:
    cs.assert_origin_allowed(
        db, api, request.headers.get("origin"), request.headers.get("referer")
    )


def _optional_student(db: Session, request: Request) -> StudentProfile | None:
    """Authorization 헤더가 유효한 학생 토큰이면 학생을 돌려준다 — 없거나 무효면 None.

    공개 API라 무효 토큰으로 401을 내지 않는다(외부 임베드는 인증 없이 동작해야 함).
    인증되면 verify가 채점 결과를 그 학생의 학습기록(코인·진도·오늘의퀴즈)에 적립한다.
    """
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = decode_token(auth[7:])
    except PyJWTError:
        return None
    if payload.get("type") != "access" or payload.get("role") != "student":
        return None
    student = db.get(StudentProfile, str(payload.get("sub", "")))
    if student is None or student.status == "disabled":
        return None
    return student


def _credit_student(
    db: Session, student: StudentProfile, meta: dict, correct: bool, answer,
    solve_time_ms: int = 0,
) -> dict:
    """교육형 채점 결과 1건을 학생 학습기록으로 적립 — 실전 모드(game-answer)와 동일 부수효과.

    서버 채점 결과만 기록하므로 자기신고 위조가 없다. 오늘 EDU_SESSION_TOTAL번째
    문항부터 완료 신고(completed) — 오늘의퀴즈 done은 save_attempt가 '정답일 때만' 승격한다.
    복습(rp: 발급 토큰에 서명된 값)은 코인·퀴즈 상태에 반영하지 않는다.
    """
    from app.api.v1.endpoints.students import _record_wrong, save_attempt  # 지연 import (순환 회피)
    from app.core.permissions import Principal
    from app.schemas.student import AttemptCreate
    from app.services import subject_banks

    subject = str(meta.get("subj") or "")
    replay = bool(meta.get("rp"))
    qid = meta.get("qid")
    # 전체학습 주간 챕터 플레이면 오늘의퀴즈(습관)를 건드리지 않는다(학습·습관 분리).
    is_chapter = meta.get("chapter") is not None
    # 챕터 복습은 서버가 판정한다 — 이미 완주한 단계(stages_done 이상)의 재플레이는
    # 클라이언트가 replay 플래그를 빼고 보내도 미적립. (day 경로의 서버 is_replay 판정과 동형 —
    # 안 막으면 완주 챕터를 일반 모드로 다시 열어 코인을 재적립하는 파밍 루트가 생긴다)
    if is_chapter and not replay:
        stage_meta = meta.get("stage")
        if isinstance(stage_meta, int):
            from app.models import ChapterProgress

            cp = (
                db.query(ChapterProgress)
                .filter(
                    ChapterProgress.student_id == student.id,
                    ChapterProgress.subject == subject,
                    ChapterProgress.chapter_no == meta.get("chapter"),
                )
                .first()
            )
            if cp is not None and stage_meta <= (cp.stages_done or 0):
                replay = True

    principal = Principal(kind="student", id=student.id, role="student", student=student)

    # 뱅크 문항 오답 → 오답노트·취약추천 (동작형 drag/trace는 대상 아님, 복습 제외)
    if qid and not correct and not replay:
        q = subject_banks.get_question(subject, str(qid))
        if q is not None:
            if q["type"] == "multi":
                picked_ids = [str(x) for x in (answer or [])]
                answer_ids = [str(a) for a in (q["answer"] or [])]
            else:
                picked_ids = [str(answer)] if answer is not None else []
                answer_ids = [str(q["answer"])]
            _record_wrong(db, student, subject, q, picked_ids, answer_ids)

    answered_before = (
        db.query(func.count(LearningAttempt.id))
        .filter(
            LearningAttempt.student_id == student.id,
            LearningAttempt.subject == subject,
            func.date(LearningAttempt.created_at) == date.today(),
        )
        .scalar()
        or 0
    )
    answered = answered_before + 1
    attempt_req = AttemptCreate(
        subject=subject,
        chapter_no=meta.get("chapter"),
        result="correct" if correct else "incorrect",
        score=20 if correct else 0,  # 5문 기준 100점 만점 (game-answer와 동일)
        completed=answered >= EDU_SESSION_TOTAL and not replay,
        replay=replay,
        daily=not is_chapter,  # 챕터 플레이는 오늘의퀴즈 done/연속도전 미갱신
        behavior=None,  # 행동데이터는 record_behavior(edu-api)로 이미 적재 — 이중 기록 방지
        # 문항 풀이시간(위젯 실측) — 0이면 학생홈 '학습 시간'·요일별 그래프가 전부 0분이 된다
        solve_time_ms=solve_time_ms,
    )
    saved = save_attempt(attempt_req, principal, db)

    quiz_done = (
        db.query(DailyQuizStatus)
        .filter(
            DailyQuizStatus.student_id == student.id,
            DailyQuizStatus.quiz_date == date.today(),
            DailyQuizStatus.subject == subject,
            DailyQuizStatus.status == "done",
        )
        .first()
        is not None
    )
    return {
        "answered": answered,
        "total": EDU_SESSION_TOTAL,
        "quiz_done": quiz_done,
        "replay": replay,
        "coins_earned": saved.get("coins_earned", 0),
        "coins": saved.get("coins"),
        # 6과목 완주 스티커 — 이 문항 적립으로 오늘 전 과목 done이 된 순간 함께 지급됨
        "sticker_awarded": saved.get("sticker_awarded", False),
        "sticker_coins": saved.get("sticker_coins", 0),
    }


@router.post("/challenge")
def challenge(
    request: Request,
    x_site_key: str | None = Header(default=None),
    subject: str | None = None,  # edu 전용 과목 오버라이드 (?subject=수학) — 1st-party 인앱 임베드용
    day: int | None = None,  # edu·생활: 커리큘럼 일차 문항 (미래 일차는 잠금 에러)
    replay: bool = False,  # edu: 복습 세션 — verify 적립 시 코인·퀴즈 상태 미반영
    chapter: int | None = None,  # 전체학습 주간 챕터 — 그 챕터 문항만 + 오늘의퀴즈 미오염
    stage: int | None = None,  # 챕터 단계(1~5) — 단계 문항 슬라이스
    db: Session = Depends(get_db),
):
    _throttle(db, request, "chall", RATE_CHALLENGE_PER_MIN)
    api = _key(db, x_site_key)
    _origin_guard(db, request, api)
    cs.assert_entitled(db, api)  # 요금제·quota 검사
    # 교육형 키는 발급 시 과목이 박혀 있지만, 우리 앱(과목별 게임화면)이 붙을 땐
    # 화면 과목에 맞춰 요청별로 과목을 바꿀 수 있게 허용한다. (EDU_SUBJECTS 안에서만)
    eff_subject = api.subject
    learning = False
    if api.product == "edu":
        # 과목 스코프 강제: 외부 판매 키(first_party=False)는 발급 과목에 고정한다 —
        # ?subject=로 다른 과목을 받아 구매 안 한 과목에 접근하는 것을 막는다.
        # 1st-party(우리 인앱) 키만 요청별 과목 전환을 허용한다(한 키로 6과목 게임화면).
        if api.first_party and subject and subject in cs.EDU_SUBJECTS:
            eff_subject = subject
        # 교육형 키는 자기 과목의 실제 문제를 낸다(구매 고객 = 그 과목 학습 API).
        if eff_subject in cs.EDU_SUBJECTS:
            learning = True
    if day is not None:
        learning = True  # 커리큘럼 일차(생활 인앱)도 학습 세션
    if chapter is not None:
        learning = True  # 전체학습 주간 챕터도 학습 세션(조작형 대신 실문항)
    ch = cs.make_challenge(
        api.product, eff_subject, day=day, replay=replay, learning=learning,
        chapter=chapter, stage=stage,
    )
    cs.log_call(db, api, "captcha/challenge", 200, subject=eff_subject)
    db.commit()
    return {"product": api.product, "subject": eff_subject, **ch}


class _VerifyReq(BaseModel):
    challenge_token: str
    answer: object  # 문자열 또는 배열(그림 다중선택)
    behavior: dict | None = None  # 교육형: 반응시간·재시도·조작 등 행동데이터


@router.post("/verify")
def verify(
    req: _VerifyReq,
    request: Request,
    x_site_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    _throttle(db, request, "verify", RATE_VERIFY_PER_MIN)
    api = _key(db, x_site_key)
    _origin_guard(db, request, api)
    # 과목 스코프 심층 방어: 외부 판매 키(first_party=False)는 발급 과목의 토큰만 verify한다.
    # challenge 게이트가 이미 과목을 강제하지만, 1st-party 토큰이 유출돼도 외부 키로 구매
    # 안 한 과목의 채점·행동데이터 수집에 재사용되지 못하게 verify에서도 다시 막는다.
    if api.product == "edu" and not api.first_party:
        tok_subj = cs.peek_subject(req.challenge_token)
        if tok_subj and tok_subj != api.subject:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="이 키로는 다른 과목의 챌린지를 검증할 수 없어요.",
            )
    result = cs.verify_challenge(db, req.challenge_token, req.answer)
    meta = result.pop("meta", {})  # 발급 토큰에 서명된 문항 메타 — 클라이언트 응답에는 내리지 않음
    # 교육형 API는 통과/실패보다 '행동데이터 수집'이 목적 — 정답 여부와 무관하게 적재
    if api.product == "edu":
        student = _optional_student(db, request)
        behavior = req.behavior
        # 끌어다 놓기의 드롭 거리는 서버 채점값을 기록 (클라이언트 자기신고 대체)
        if "drop_distance_norm" in result:
            behavior = {**(behavior or {}), "drop_distance_norm": result["drop_distance_norm"]}
        # 인증 학생의 행동데이터는 본인 귀속 — JWT로 검증된 신원을 명시 전달.
        # (behavior dict에 student_id를 실어 보내던 방식은 record_behavior의
        #  '키 기관 일치' 재검증에 걸려 인앱(1st-party) 학생이 전부 익명 적재되던 버그)
        cs.record_behavior(
            db, api, behavior, bool(result.get("success")), verified_student=student
        )
        # 인앱(인증 학생) 풀이는 학습기록으로 적립 — 코인·진도·오늘의퀴즈 (실전 모드 대체)
        if student is not None and meta.get("subj"):
            # 풀이시간은 자기신고 — 스키마 상한(1시간)과 동일하게 클램프해 통계 오염 방지
            raw_ms = (req.behavior or {}).get("solve_time_ms")
            solve_ms = raw_ms if isinstance(raw_ms, int) and 0 <= raw_ms <= 3_600_000 else 0
            result["session"] = _credit_student(
                db, student, meta, bool(result.get("success")), req.answer, solve_time_ms=solve_ms
            )
    cs.log_call(db, api, "captcha/verify", 200 if result["success"] else 400)
    db.commit()
    return result


class _PairReq(BaseModel):
    challenge_token: str
    a: str
    b: str


@router.post("/pair")
def pair(
    req: _PairReq,
    request: Request,
    x_site_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """메모리 카드게임(영어 07) 짝 확인 — 토큰 미소비 판정. 원본 /match 설계 이식.

    verify와 동일 스로틀을 태워 전 쌍 열거(n²) 봇의 속도를 원본 수준으로 제한한다.
    """
    _throttle(db, request, "verify", RATE_VERIFY_PER_MIN)
    api = _key(db, x_site_key)
    _origin_guard(db, request, api)
    return cs.pair_check(req.challenge_token, req.a, req.b)


class _ValidateReq(BaseModel):
    verdict_token: str


@router.post("/validate")
def validate(
    req: _ValidateReq,
    request: Request,
    x_secret_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """고객 서버가 secret_key로 최종 검증 — 브라우저에서 받은 verdict가 진짜 통과인지.

    서버-대-서버 호출이라 Origin 검증은 없음(secret 자체가 인증). IP 레이트리밋만 건다.
    """
    if not x_secret_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="X-Secret-Key 헤더가 필요합니다.")
    auth_service.rate_limit(
        db, f"capvalidate:{_client_ip(request)}", limit=RATE_VALIDATE_PER_MIN, window_seconds=60,
    )
    api = (
        db.query(ApiKey)
        .filter(ApiKey.secret_key_hash == sha256_hash(x_secret_key), ApiKey.status == "active")
        .first()
    )
    if api is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 secret_key 입니다.")
    ok = cs.validate_verdict(db, req.verdict_token)
    cs.log_call(db, api, "captcha/validate", 200 if ok else 400)
    db.commit()
    return {"success": ok}
