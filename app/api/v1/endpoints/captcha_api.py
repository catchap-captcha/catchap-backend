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
from app.models import ApiKey, LearningAttempt, StudentProfile
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
    from app.api.v1.endpoints.students import _apply_attempt  # 지연 import (순환 회피)
    from app.core.permissions import Principal
    from app.schemas.student import AttemptCreate


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

    # 오답노트 별도 기록 은퇴(Q 통합 3단계, 결정 ④) — 오답은 _apply_attempt의 SRS 갱신이
    # wrong 상자에 남긴다('틀린 문제' 화면 = 그 상자의 뷰, 다시 맞히면 자동 이탈).

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
        # 문항 id — 원장 기록용. SRS 상태 갱신(bank_mode.record_answer)은 _apply_attempt
        # (단일 채점 싱크)가 이 content_id로 수행한다(설계: question-bank-scale-design.md).
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

    # (은퇴 0719, Q 통합 3단계-c) quiz_done(DailyQuizStatus 조회)·quiz_bonus·스티커 키 삭제 —
    # 과거 done 행이 True로 새어 나와 은퇴한 퀴즈 UI를 되살리는 것을 막는다(위젯은 이 키들을
    # 읽지 않고, 게임 화면의 소비부도 함께 제거됨). coins_earned는 0 고정 계약 유지.
    return {
        "answered": answered,
        "total": EDU_SESSION_TOTAL,
        "replay": replay,
        "coins_earned": saved.get("coins_earned", 0),
        "coins": saved.get("coins"),
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
    # 모든 문항이 고정 핀 — 체크포인트는 어떤 핀 시점에 잡혔고(next_checkpoint), 정확히
    # 그 시점(position_sec == cp)의 문항만 출제 대상이다. 같은 시점의 공개 문항 중복은
    # _reject_duplicate_pin이 막지만, 가드 이전의 레거시 중복이 남아 있으면 무작위로
    # 하나를 낸다(나머지는 사문 — 강사가 콘솔에서 시점을 옮겨 살린다).
    choices = (
        db.query(LectureQuestion)
        .filter(
            LectureQuestion.lecture_id == lec.id,
            LectureQuestion.status == "active",
            LectureQuestion.position_sec == cp,
        )
        .all()
    )
    if not choices:
        # 폴백 출제 금지 — 게이트를 열 문항이 없음을 정직하게 알린다
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="이 강의에 출제할 확인 문항이 없어요. 운영자에게 문항 등록을 요청해 주세요.",
        )
    q = random.choice(choices)
    from app.api.v1.endpoints.lectures import _question_image_url

    payload = q.payload or {}
    # 이미지 문항 — 화면 캡처 보기는 실제로 본 사람만 고를 수 있다(시청 검증의 핵심).
    # 이미지가 있을 때만 키를 실어 텍스트 전용 문항의 페이로드는 종전과 동일(하위호환).
    # URL은 무인증 서빙 엔드포인트 경로뿐 — 어떤 보기 이미지도 정오 신호를 담지 않는다
    # (answer는 지금처럼 서명 토큰에만 들어간다).
    opt_imgs = payload.get("option_images") or {}
    # ★보기 셔플(2026-07-22) — 정답이 항상 같은 위치(원래 answer_index)에 나오면 위치를
    #  외우거나 특정 id만 찍는 봇이 시청 없이 통과한다. 매 출제마다 보기 순서를 무작위로
    #  재배치하고 id도 '표시 위치'로 새로 부여한 뒤, 정답 id를 새 위치로 다시 매핑한다.
    raw_opts = payload.get("options", [])
    answer_orig = {int(i) for i in (q.answer_indexes or [q.answer_index])}
    perm = list(range(len(raw_opts)))
    random.shuffle(perm)  # new_pos → 원래 인덱스
    options = []
    new_answer_ids: list[int] = []
    for new_i, orig_i in enumerate(perm):
        opt = {"id": str(new_i), "text": str(raw_opts[orig_i])}
        ref = opt_imgs.get(str(orig_i))  # 이미지 참조는 '원래' 인덱스 키로 저장돼 있다
        if isinstance(ref, dict) and ref.get("id"):
            opt["image"] = _question_image_url(lec.id, q.id, ref)
        options.append(opt)
        if orig_i in answer_orig:
            new_answer_ids.append(new_i)
    # multi(끌어 담기) 렌더러 — boxLabel이 있으면 위젯이 보기를 상자로 드래그해 담는 모드로
    # 그린다(탭 토글 폴백 공존 — 접근성 유지). 단일 정답 문항도 multi로 낸다: 클릭 한 점과
    # 달리 드래그는 속도·경로 궤적이 남아 이상행동 판별 모델의 학습 데이터가 된다.
    # ★ 해설(explain)은 여기서 내려보내지 않는다 — 강의 게이트는 학습이 아니라 검증이고,
    # 화면 억제와 무관하게 네트워크 응답에 실리는 순간 봇이 읽는다(강사가 해설에 정답을
    # 적으면 그대로 유출). explain은 운영자 기록용으로 DB에만 남는다.
    public = {
        "type": "multi",
        "subject": lec.subject,
        "prompt": payload.get("prompt", ""),
        "options": options,
        "boxLabel": "정답을 여기로 끌어다 놓으세요",
        "boxHint": "정답이 여러 개일 수도 있어요 — 맞는 보기를 모두 담아 주세요",
        "lecture": lec.id,
        "checkpoint_sec": cp,
    }
    prompt_ref = payload.get("prompt_image")
    if isinstance(prompt_ref, dict) and prompt_ref.get("id"):
        public["prompt_image"] = _question_image_url(lec.id, q.id, prompt_ref)
    # qid — 오답 상한 도달 시 '이 문항'의 내용 시작 시점(content_start_sec)으로 되감기
    # 위해 어떤 문항이 출제됐는지 서명 토큰에 봉인한다(cp 역조회는 레거시 중복 핀 때문에
    # 모호하고, 클라이언트 신고는 신뢰하지 않는다).
    meta = {"subj": lec.subject, "lec": lec.id, "cp": cp, "qid": q.id, "bank": True}
    # 셔플된 '표시 위치' 기준 정답 id로 서명한다(원래 answer_index가 아니라 new_answer_ids).
    # select_all은 집합 정확 일치 채점이라 부분 선택·초과 선택은 오답, 단일 정답이면 1개만 담아 통과.
    ch = cs._wrap("select_all", sorted(str(i) for i in new_answer_ids), public, meta)
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
    bank: bool = False,  # 전체학습 문제은행 모드 — SRS 큐(만기>틀린>새) 출제, 코인·퀴즈 미반영
    early: bool = False,  # 문제은행 '복습 미리 하기' — 오늘 큐 소진 후 휴면 문항을 미리 복습
    course: str | None = None,  # 코스 Q — bank와 함께: 그 코스 강의 유래 문항만(1st-party 전용)
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
    # 화면 과목에 맞춰 요청별로 과목을 바꿀 수 있게 허용한다. (EDU_SUBJECTS 6개 + 코스가
    # declare한 동적 과목 — 과목 하드코딩 폐지 방침과 맞춰, 레거시 6과목만 허용하면 새
    # 코스(자격증·어학 등)의 문제은행 연습이 위젯에서 영영 안 열린다.)
    from app.services import subject_banks

    def _subject_ok(s: str | None) -> bool:
        return bool(s) and (s in cs.EDU_SUBJECTS or subject_banks.is_live(s))

    eff_subject = api.subject
    learning = False
    if api.product == "edu":
        # 과목 스코프 강제: 외부 판매 키(first_party=False)는 발급 과목에 고정한다 —
        # ?subject=로 다른 과목을 받아 구매 안 한 과목에 접근하는 것을 막는다.
        # 1st-party(우리 인앱) 키만 요청별 과목 전환을 허용한다(한 키로 여러 과목 게임화면).
        if api.first_party and _subject_ok(subject):
            eff_subject = subject
        # 교육형 키는 자기 과목의 실제 문제를 낸다(구매 고객 = 그 과목 학습 API).
        if _subject_ok(eff_subject):
            learning = True
    if day is not None:
        learning = True  # 커리큘럼 일차(생활 인앱)도 학습 세션
    if chapter is not None:
        learning = True  # 전체학습 주간 챕터도 학습 세션(조작형 대신 실문항)
    if bank and api.product == "edu" and _subject_ok(eff_subject):
        # 전체학습 문제은행 모드 — SRS 큐 출제(만기 복습>틀린>새, 설계:
        # question-bank-scale-design.md). 큐가 비면 '오늘 완료'를 정직하게 알린다(무한
        # 재순환 폐지). 인증 학생이 없으면(외부 임베드) 잠금 적용된 풀에서 랜덤.
        # chapter가 함께 오면(주차 목차 유지) 그 주차 풀 안에서 SRS 우선순위 + 휴면 폴백
        # (명시적 챕터 선택 = 의도적 복습이라 '오늘 완료'로 막지 않는다).
        from app.services import bank_mode

        student = _optional_student(db, request)
        if course is not None:
            # 코스 Q(Q 통합 3단계-b, 결정 ③) — 그 코스의 강의 유래 문항만 SRS 우선순위로.
            # 휴면 폴백 포함(pick_from) — 코스를 콕 집은 연습은 챕터와 같은 '의도적 복습'
            # 취급이라 '오늘 완료'로 막지 않는다(수료 시험 훈련장 역할). 1st-party 전용:
            # 코스 id는 인앱 화면 계약이라 외부 판매 키에는 열지 않는다.
            from app.models import Course

            crs = db.get(Course, course) if api.first_party else None
            if crs is None or crs.status != "active":
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="코스를 찾을 수 없습니다.")
            eff_subject = crs.subject  # 코스=과목 고정 — 화면이 준 subject보다 코스가 정본
            ids = bank_mode.course_question_ids(db, eff_subject, crs.id)
            if not ids:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    detail="이 코스의 연습 문제가 아직 없어요. 강의 문항이 은행에 배치되면 열려요.",
                )
            q = bank_mode.pick_from(db, student, eff_subject, ids)
            if q is None:
                # 후보는 있는데 전부 잠김 — 강의 완주 잠금(배움→연습 순서)을 정직하게 안내
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    detail="이 코스의 문제는 강의를 완주하면 열려요.",
                )
            ch = cs._wrap_bank_question(
                eff_subject, q, {"subj": eff_subject, "bank": True, "course": crs.id}
            )
            return _emit_challenge(db, api, eff_subject, ch)
        if chapter is not None:
            from app.services import chapters as _ch

            max_ch = _ch.unlocked_count(eff_subject)
            eff_chapter = min(max(1, chapter), max_ch) if max_ch >= 1 else chapter
            ids = _ch.chapter_all_question_ids(eff_subject, eff_chapter)
            q = bank_mode.pick_from(db, student, eff_subject, ids)
            bank_meta = {"subj": eff_subject, "bank": True, "chapter": eff_chapter}
        else:
            q = bank_mode.pick_question(db, student, eff_subject, early=early)
            bank_meta = {"subj": eff_subject, "bank": True}
        if q is None:
            # '오늘 완료'(큐 소진·휴면만 남음)와 '문항 없음'(풀 비었거나 전부 잠김)을 구분해
            # 알린다 — 완료를 에러처럼 보이게 하면 학생이 고장으로 오해한다(정직한 상태 표기).
            if student is not None and chapter is None:
                st = bank_mode.queue_status(db, student, eff_subject)
                if st["resting"] > 0 and not (st["due"] or st["wrong"] or st["new"]):
                    raise HTTPException(
                        status.HTTP_404_NOT_FOUND,
                        detail={
                            "all_done": True,
                            "next_review_at": st["next_review_at"],
                            "message": "오늘 몫을 다 끝냈어요! 복습할 때가 되면 문제가 다시 열려요.",
                        },
                    )
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
    from app.models import LectureQuestion, LectureWatchProgress
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
    # 오답 시 되감을 지점 — 토큰에 봉인된 qid의 문항이 지정한 내용 시작 시점.
    # 미지정(None)이면 record_checkpoint가 cp-REWIND_SEC 폴백을 쓴다. 문항이 그새
    # 지워졌거나 구버전 토큰(qid 없음)이어도 폴백으로 안전하게 동작한다.
    rewind_to = None
    if not success and meta.get("qid"):
        q = db.get(LectureQuestion, str(meta["qid"]))
        if q is not None and q.content_start_sec is not None:
            rewind_to = int(q.content_start_sec)
    updated = lecture_service.record_checkpoint(
        db, student_id=student.id, lecture_id=lecture_id,
        position_sec=int(cp or 0), passed=success, rewind_to_sec=rewind_to,
        question_id=str(meta["qid"]) if meta.get("qid") else None,  # 문항별 통계 계측
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
    # 강의 체크포인트 토큰 — 발급 게이트(_lecture_challenge: edu 1st-party 전용)와 대칭으로
    # verify 자격도 강제한다. 이 검사가 없으면 아무 사이트키(무료 요금제가 자가 발급하는
    # captcha 키로 충분)로 강의 토큰을 채점시킬 수 있고, 그 경로는 아래 edu 분기(정답·해설
    # 제거 + 체크포인트 기록)를 통째로 건너뛰어 오답 응답에 정답 집합이 그대로 실린다.
    # 실패 기록도 안 남아 대가 없이 정답을 수확한 뒤 게이트를 여는 우회가 성립한다
    # (적대적 검토에서 PoC로 실증). 토큰 소비 전에 막아 채점 자체를 시키지 않는다.
    if cs.peek_is_lecture(req.challenge_token) and (
        api.product != "edu" or not api.first_party
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="이 키로는 강의 시청 검증을 사용할 수 없어요.",
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
            # ★ 정답·해설 미반환 — 학습형 verify는 오답 피드백용으로 정답을 내리지만,
            # 강의 게이트는 검증이라 위젯도 표시하지 않는다(eduFeedback lectureMode 규약).
            # 풀 문항은 체크포인트마다 반복 출제되므로 오답 응답의 정답이 곧 파밍 재료다:
            # 일부러 틀려 정답 집합을 수집한 봇이 같은 문항 재등장 때 통과하는 우회를 막는다.
            result.pop("answer", None)
            result.pop("explain", None)
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
