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

# 문항당 풀이시간 상한(ms) — '넉넉한 백스톱'. 방치 제외는 주로 위젯이 담당한다
# (탭을 떠나 있으면 카운트 정지 → 화면을 보고 실제로 푸는 시간만 보냄). 이 상한은 화면을
# 켠 채 오래 자리를 비운 극단만 자른다. 수학처럼 오래 걸리는 문항을 자르지 않도록 넉넉히 둔다.
SOLVE_TIME_CAP_MS = 15 * 60 * 1000  # 15분

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


def _emit_challenge(db: Session, api: ApiKey, subject: str, ch: dict) -> dict:
    """챌린지 발급 공통 꼬리 — 사용량 로그 적립·커밋·응답 조립(세 갈래 공통)."""
    cs.log_call(db, api, "captcha/challenge", 200, subject=subject)
    db.commit()
    return {"product": api.product, "subject": subject, **ch}


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
    from app.api.v1.endpoints.students import (  # 지연 import (순환 회피)
        _apply_attempt,
        _mark_reviewed,
        _record_wrong,
    )
    from app.core.permissions import Principal
    from app.schemas.student import AttemptCreate
    from app.services import subject_banks

    subject = str(meta.get("subj") or "")
    replay = bool(meta.get("rp"))
    qid = meta.get("qid")
    # 전체학습 문제은행 모드(0713): 기록·정답률·오답노트만 — 코인·오늘의퀴즈·연속도전 미반영.
    is_bank = bool(meta.get("bank"))
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

    # 뱅크 문항 오답 → 오답노트·취약추천 (전 문제 유형 — 정답 텍스트는 _record_wrong가
    # 유형별로 렌더, route/trace/swipe 등 텍스트 정답 없는 유형은 개념(explain)으로). 복습 제외.
    # 정답이면 그 문항의 미복습 오답노트를 복습완료로 승격(복습 순환 완성).
    if qid and not replay:
        q = subject_banks.get_question(subject, str(qid))
        if q is not None:
            if not correct:
                _record_wrong(db, student, subject, q, answer, chapter_no=meta.get("chapter"))
            else:
                _mark_reviewed(db, student, q)

    answered_before = (
        db.query(func.count(LearningAttempt.id))
        .filter(
            LearningAttempt.student_id == student.id,
            LearningAttempt.subject == subject,
            func.date(LearningAttempt.created_at) == date.today(),
            # 오늘의퀴즈 세션 판정은 데일리 시도(chapter_no NULL)만 — 챕터/은행 플레이가
            # 세어지면 5문항 완료가 조기/오판정된다
            LearningAttempt.chapter_no.is_(None),
        )
        .scalar()
        or 0
    )
    answered = answered_before + 1
    attempt_req = AttemptCreate(
        subject=subject,
        # 주차 플레이는 실제 챕터 번호, 자유 은행은 0 마커 — 둘 다 오늘의퀴즈
        # 진행바(chapter_no IS NULL 집계)에 안 섞인다
        chapter_no=meta.get("chapter") if is_chapter else (0 if is_bank else None),
        # 문항 id — 문제은행 모드의 '안 푼/틀린/맞춘' 분류 원천(bank_mode._last_results).
        # 컬럼(80자) 초과분은 잘라 저장 — 초과로 verify 전체가 500나는 것보다 낫다.
        content_id=str(qid)[:80] if qid else None,
        result="correct" if correct else "incorrect",
        score=20 if correct else 0,  # 5문 기준 100점 만점 (game-answer와 동일)
        completed=answered >= EDU_SESSION_TOTAL and not replay and not is_bank,
        replay=replay,
        daily=not is_chapter and not is_bank,  # 챕터·은행 플레이는 오늘의퀴즈/연속도전 미갱신
        no_coin=is_bank,  # 문제은행은 무보상(제품 결정) — 코인은 오늘의퀴즈 전용
        behavior=None,  # 행동데이터는 record_behavior(edu-api)로 이미 적재 — 이중 기록 방지
        # 문항 풀이시간(위젯 실측) — 0이면 학생홈 '학습 시간'·요일별 그래프가 전부 0분이 된다
        solve_time_ms=solve_time_ms,
    )
    # 위젯 verify는 서버가 챌린지 정답을 검증한 경로 → graded=True(점수 부수효과 대상).
    saved = _apply_attempt(attempt_req, student, db, graded=True)

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
        # 오늘의퀴즈 완료 보상(광고된 reward_coins) — 완료 승격 문항에서만 >0
        "quiz_bonus": saved.get("quiz_bonus", 0),
    }


def _lecture_challenge(db: Session, request: Request, api: ApiKey, lecture_id: str) -> dict:
    """강의 체크포인트 확인 문제 발급 — 시청 검증 게이트(1st-party edu 키 + 학생 인증 전용).

    서버가 ① 강의 존재·active ② 그 학생의 next_checkpoint_sec 도달을 확인한 뒤,
    그 강의의 active 문항 중 position_sec ≤ 체크포인트인 것에서 무작위 1개를 낸다.
    문항이 없으면 명확한 4xx — 과목 은행 문제로 폴백하지 않는다(강의와 무관한 문제를
    풀게 하는 것은 시청 검증이 아니다).
    meta(lec/cp)는 토큰에 서명돼 verify에서 위조 없이 복원된다. bank=True는 verify의
    코인·오늘의퀴즈 미오염 스위치(이중 안전장치 — lec 분기는 애초에 적립을 안 탄다).
    """
    import random

    from app.models import Lecture, LectureQuestion, LectureWatchProgress

    # 외부 판매 키 차단 — 시청 검증은 우리 인앱(1st-party) 전용 도메인
    if api.product != "edu" or not api.first_party:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="이 키로는 강의 시청 검증을 사용할 수 없어요."
        )
    student = _optional_student(db, request)
    if student is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="강의 확인 문제는 학생 로그인이 필요해요."
        )
    lec = db.get(Lecture, lecture_id)
    if lec is None or lec.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="강의를 찾을 수 없어요.")
    progress = (
        db.query(LectureWatchProgress)
        .filter(
            LectureWatchProgress.student_id == student.id,
            LectureWatchProgress.lecture_id == lec.id,
        )
        .first()
    )
    if progress is None or progress.next_checkpoint_sec is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="지금은 확인 문제가 필요한 지점이 아니에요."
        )
    cp = int(progress.next_checkpoint_sec)
    if int(progress.watched_max_sec or 0) < cp:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="아직 확인 문제 지점까지 시청하지 않았어요."
        )
    candidates = (
        db.query(LectureQuestion)
        .filter(
            LectureQuestion.lecture_id == lec.id,
            LectureQuestion.status == "active",
            LectureQuestion.position_sec <= cp,
        )
        .all()
    )
    if not candidates:
        # 폴백 출제 금지 — 게이트를 열 문항이 없음을 정직하게 알린다
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="이 강의에 출제할 확인 문항이 없어요. 운영자에게 문항 등록을 요청해 주세요.",
        )
    q = random.choice(candidates)
    payload = q.payload or {}
    public = {
        "type": "single",  # 위젯 기본(단일 선택) 렌더러 — prompt + options
        "subject": lec.subject,
        "prompt": payload.get("prompt", ""),
        "hint": payload.get("explain") or "",
        "options": [
            {"id": str(i), "text": str(t)} for i, t in enumerate(payload.get("options", []))
        ],
        "lecture": lec.id,
        "checkpoint_sec": cp,
    }
    meta = {"subj": lec.subject, "lec": lec.id, "cp": cp, "bank": True}
    ch = cs._wrap("single", str(int(q.answer_index)), public, meta)
    return _emit_challenge(db, api, lec.subject, ch)


@router.post("/challenge")
def challenge(
    request: Request,
    x_site_key: str | None = Header(default=None),
    subject: str | None = None,  # edu 전용 과목 오버라이드 (?subject=수학) — 1st-party 인앱 임베드용
    day: int | None = None,  # edu·생활: 커리큘럼 일차 문항 (미래 일차는 잠금 에러)
    replay: bool = False,  # edu: 복습 세션 — verify 적립 시 코인·퀴즈 상태 미반영
    chapter: int | None = None,  # 전체학습 주간 챕터 — 그 챕터 문항만 + 오늘의퀴즈 미오염
    stage: int | None = None,  # 챕터 단계(1~5) — 단계 문항 슬라이스
    bank: bool = False,  # 전체학습 문제은행 모드 — 안 푼>틀린>맞춘 우선 출제, 코인·퀴즈 미반영
    lecture: str | None = None,  # 강의 시청 검증 — 체크포인트 확인 문제(1st-party edu 전용)
    db: Session = Depends(get_db),
):
    _throttle(db, request, "chall", RATE_CHALLENGE_PER_MIN)
    api = _key(db, x_site_key)
    _origin_guard(db, request, api)
    cs.assert_entitled(db, api)  # 요금제·quota 검사
    if lecture is not None:
        return _lecture_challenge(db, request, api, lecture)
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
    if bank and api.product == "edu" and eff_subject in cs.EDU_SUBJECTS:
        # 전체학습 문제은행 모드 — 학생 이력 기반 우선순위 출제(안 푼>틀린>맞춘), 단계 없이 무한.
        # 인증 학생이 없으면(외부 임베드) 은행 전체 랜덤으로 동작한다.
        # chapter가 함께 오면(전체학습 = 주차 목차 유지) 그 주차 문항 풀 안에서만 우선순위 출제한다
        # (사용자 결정 0714: 주차는 목차로 유지, 그 안은 안푼>틀린>푼 무한순환 — 5단계 게이팅 제거).
        from app.services import bank_mode

        student = _optional_student(db, request)
        if chapter is not None:
            from app.services import chapters as _ch

            max_ch = _ch.unlocked_count(eff_subject)
            eff_chapter = min(max(1, chapter), max_ch) if max_ch >= 1 else chapter
            ids = _ch.chapter_all_question_ids(eff_subject, eff_chapter)
            q = bank_mode.pick_from(db, student, eff_subject, ids)
            bank_meta = {"subj": eff_subject, "bank": True, "chapter": eff_chapter}
        else:
            q = bank_mode.pick_question(db, student, eff_subject)
            bank_meta = {"subj": eff_subject, "bank": True}
        if q is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="플레이할 문항이 없어요.")
        # meta.bank → verify가 코인·오늘의퀴즈를 건드리지 않고 기록·오답노트만 남긴다.
        # chapter도 실으면 그 주차로 오답노트·통계가 기록된다(무보상은 유지).
        ch = cs._wrap_bank_question(eff_subject, q, bank_meta)
        return _emit_challenge(db, api, eff_subject, ch)
    if (
        chapter is not None and stage is not None
        and api.product == "edu" and api.first_party and eff_subject in cs.EDU_SUBJECTS
    ):
        # 주차 커리큘럼 하이브리드(0713): 주차 구조(월요일 잠금)·5단계 페이스는 유지하되,
        # 문항 선별은 그 챕터 풀 전체에서 학생별 우선순위(안 푼>틀린>맞춘)로 —
        # 챕터당 문항이 늘어나도 고정 슬라이스 없이 학생마다 필요한 문제를 먼저 낸다.
        # meta.bank=True → 무보상·오늘의퀴즈 미오염(전체학습 공통 규칙).
        from app.services import bank_mode
        from app.services import chapters as _ch

        student = _optional_student(db, request)
        # 과목 이동 자동 보정: 과목마다 열린 주차 수가 달라(문제은행 크기 차이),
        # 다른 과목에 없는 주차를 요청하면(예: 국어 5주차→과학은 3주차뿐) 그 과목의
        # 마지막 열린 주차로 clamp한다. 1 미만도 1로. verify 적립은 보정된 주차로 기록.
        max_ch = _ch.unlocked_count(eff_subject)
        eff_chapter = min(max(1, chapter), max_ch) if max_ch >= 1 else chapter
        ids = _ch.chapter_all_question_ids(eff_subject, eff_chapter)
        q = bank_mode.pick_from(db, student, eff_subject, ids)
        if q is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="플레이할 문항이 없어요.")
        ch = cs._wrap_bank_question(
            eff_subject, q,
            {"subj": eff_subject, "rp": bool(replay), "chapter": eff_chapter, "stage": stage, "bank": True},
        )
        return _emit_challenge(db, api, eff_subject, ch)
    ch = cs.make_challenge(
        api.product, eff_subject, day=day, replay=replay, learning=learning,
        chapter=chapter, stage=stage,
    )
    return _emit_challenge(db, api, eff_subject, ch)


def _store_scratch(db: Session, student: StudentProfile, meta: dict, scratch) -> None:
    """연습장 필기 원본 1건 저장 — 과목·문항(content_id)별. 아동 필적이라 민감:
    재생 스코프(본인·교사·보호자)·보존/파기·동의는 별도 계층. 저장 실패는 채점을 막지 않는다.
    원본(strokes)은 무제한 저장(사용자 방침 — 아이가 아무리 많이 그어도 다 저장)."""
    from app.models import ScratchRecord

    if not isinstance(scratch, dict):
        return
    strokes = scratch.get("strokes")
    if not strokes:
        return  # 그린 게 없으면 저장하지 않는다(빈 레코드 방지)
    try:
        from app.services import scratch_access

        # 새 레코드의 보존 여부는 저장 시점의 보호자 동의로 결정 — 동의 상태면 탈퇴 후에도 유지.
        retain = scratch_access.has_retain_consent(db, student.id)
        db.add(
            ScratchRecord(
                student_id=student.id,
                organization_id=student.organization_id,
                subject=str(meta.get("subj") or "")[:20],
                content_id=(str(meta.get("qid"))[:80] if meta.get("qid") else None),
                strokes=strokes,
                stroke_count=int(scratch.get("strokeCount") or 0),
                distance_px=int(scratch.get("distancePx") or 0),
                first_write_ms=int(scratch.get("firstWriteMs") or 0),
                draw_ms=int(scratch.get("drawMs") or 0),
                consent_retain=retain,
            )
        )
    except Exception:
        pass  # 부가 기능 — 필기 저장 실패가 채점/응답을 막지 않게


def _verify_lecture_checkpoint(
    db: Session, api: ApiKey, student: StudentProfile | None, meta: dict,
    success: bool, behavior: dict | None,
) -> dict:
    """강의 체크포인트 채점 후처리 — 학습 적립(_credit_student)을 아예 호출하지 않는다.

    LearningAttempt·코인·오늘의퀴즈·오답노트 전부 비생성. 대신 체크포인트 이벤트를
    기록하고 통과 시 다음 지점을 재예약한다. meta.cp와 진행 행의 현재
    next_checkpoint_sec 일치를 검증해 오래된 토큰 재사용(이미 지난 체크포인트로
    카운트 올리기)을 차단한다. 행동데이터는 source_type='lecture'로 적재.
    """
    from app.models import LectureWatchProgress
    from app.services import lecture_service

    if not api.first_party:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="이 키로는 강의 시청 검증을 사용할 수 없어요."
        )
    if student is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="강의 확인 문제는 학생 로그인이 필요해요."
        )
    lecture_id = str(meta.get("lec"))
    cp = meta.get("cp")
    progress = (
        db.query(LectureWatchProgress)
        .filter(
            LectureWatchProgress.student_id == student.id,
            LectureWatchProgress.lecture_id == lecture_id,
        )
        .first()
    )
    if progress is None or progress.next_checkpoint_sec != cp:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="이미 지나간 확인 문제예요. 새 문제를 받아 주세요."
        )
    cs.record_behavior_event(
        db,
        organization_id=student.organization_id or api.organization_id,
        student_id=student.id,
        source_type="lecture",
        behavior=behavior,
        correct=success,
    )
    updated = lecture_service.record_checkpoint(
        db, student_id=student.id, lecture_id=lecture_id,
        position_sec=int(cp or 0), passed=success,
    )
    return {
        "watched_max_sec": int(updated.watched_max_sec or 0),
        "next_checkpoint_sec": updated.next_checkpoint_sec,
        "checkpoints_passed": int(updated.checkpoints_passed or 0),
        "status": updated.status,
    }


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
        # 풀이시간 클램프(자기신고 + 방치 시간 포함) — 문항당 SOLVE_TIME_CAP_MS 상한으로 잘라
        # 학습기록·행동데이터 양쪽이 같은 값을 쓰게 한다(홈 '학습 시간'·속도 통계 팽창 방지).
        raw_ms = (behavior or {}).get("solve_time_ms")
        solve_ms = min(SOLVE_TIME_CAP_MS, raw_ms) if isinstance(raw_ms, int) and raw_ms > 0 else 0
        behavior = {**(behavior or {}), "solve_time_ms": solve_ms}
        # 끌어다 놓기의 드롭 거리는 서버 채점값을 기록 (클라이언트 자기신고 대체)
        if "drop_distance_norm" in result:
            behavior = {**behavior, "drop_distance_norm": result["drop_distance_norm"]}
        if meta.get("lec"):
            # 강의 시청 체크포인트 — 학습 적립 경로(_credit_student)를 타지 않는다
            # (LearningAttempt·코인·오늘의퀴즈·오답노트 비생성). 이벤트 기록 + 재예약만.
            result["lecture"] = _verify_lecture_checkpoint(
                db, api, student, meta, bool(result.get("success")), behavior
            )
        else:
            # 인증 학생의 행동데이터는 본인 귀속 — JWT로 검증된 신원을 명시 전달.
            # (behavior dict에 student_id를 실어 보내던 방식은 record_behavior의
            #  '키 기관 일치' 재검증에 걸려 인앱(1st-party) 학생이 전부 익명 적재되던 버그)
            cs.record_behavior(
                db, api, behavior, bool(result.get("success")), verified_student=student
            )
            # 연습장 필기 원본 저장 — 인증 학생 + scratch가 있을 때만(과목·문항별, B 백엔드).
            # 원본은 무제한 저장(사용자 방침). 재생 스코프·보존/파기·동의는 별도 계층에서 처리.
            if student is not None and isinstance(behavior, dict):
                _store_scratch(db, student, meta, behavior.get("scratch"))
            # 인앱(인증 학생) 풀이는 학습기록으로 적립 — 코인·진도·오늘의퀴즈 (실전 모드 대체)
            if student is not None and meta.get("subj"):
                result["session"] = _credit_student(
                    db, student, meta, bool(result.get("success")), req.answer,
                    solve_time_ms=solve_ms,
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
