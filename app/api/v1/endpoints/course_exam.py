"""코스 수료 시험 — 완전학습(mastery) 응시·채점·수료.

설계: docs/course-exam-design.md (사용자 결정 2026-07-18). 학습 루프의 마지막 조각:
  배움(강의 시청 검증) → 연습(문제은행 Q) → 증명(수료 시험)

핵심 규칙(왜 이렇게 — 팀 학습용):
- **mastery**: 회차(최대 10문항)는 '아직 정복 못 한 문항'(안 푼 → 틀린)만 낸다.
  수료 = 전 활성 문항 누적 정답. 만점 1회 강제의 부작용(좌절·답 암기)을 빼고
  목표의식("다 맞춰야 완료")만 남긴 형태(Khan Academy식).
- **server-side permutation**: 보기 셔플 순열을 sitting.questions에 서버 보관.
  학생은 표시 순서 기준 선택을 내고 서버가 원본 인덱스로 복원해 채점 — 답 위치
  암기·위조 차단(발급 응답에 정답·해설 없음).
- **지표 격리(설계 §7)**: 시험 응답은 LearningAttempt·문제은행 정답률·코인에 반영하지
  않는다(재시험 루프라 정답률 오염). course_exam_attempts만 쓴다.
- 기출(origin=past_exam)은 source 필수 — 비영리 교육용 이용 전제(§2), 화면 상시 노출.
"""

import random
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.permissions import Principal, require_lecture_manager, require_student
from app.db.session import get_db
from app.models import (
    CourseCompletion,
    CourseExamAttempt,
    CourseExamQuestion,
    CourseExamSitting,
    Lecture,
    LectureQuestion,
    LectureTranscript,
)
from app.services import bank_mode
from app.utils.helpers import audit

router = APIRouter(tags=["course-exam"])

# 회차당 최대 문항 수 — 한 번에 다 풀게 하지 않는 이유는 좌절 방지 + '틀린 것만
# 다시'의 리듬을 만들기 위해(문제은행 세트 10문항과 같은 보폭).
EXAM_SITTING_SIZE = 10
ORIGINS = {"manual", "past_exam", "lecture", "llm"}
# LLM 시험 생성 시 강의 전사(자막)를 프롬프트에 넣는 예산 — 실제 내용 근거로 더 깊은 문항을
# 만들되, 코스에 강의가 많으면 전체 자막이 토큰을 폭발시키므로 총·강의별 상한으로 자른다.
_COURSE_EXAM_TR_TOTAL_CAP = 30000  # 총 문자 예산(대략 수천 토큰 규모)
_COURSE_EXAM_TR_PER_LECTURE = 5000  # 강의 하나당 상한(한 강의가 예산을 독식하지 않게)


# ---------------------------------------------------------------- 공통 로더·파생
def _active_questions(db: Session, course_id: str) -> list[CourseExamQuestion]:
    return (
        db.query(CourseExamQuestion)
        .filter(
            CourseExamQuestion.course_id == course_id,
            CourseExamQuestion.status == "active",
        )
        .order_by(CourseExamQuestion.order_no, CourseExamQuestion.created_at)
        .all()
    )


def _mastered_ids(db: Session, student_id: str, course_id: str) -> set[str]:
    """정복 집합 — 응답 원장에서 파생(정답 1건 이상). 별도 상태 테이블 없음(설계 §4)."""
    return {
        r[0]
        for r in db.query(CourseExamAttempt.question_id)
        .filter(
            CourseExamAttempt.student_id == student_id,
            CourseExamAttempt.course_id == course_id,
            CourseExamAttempt.result == "correct",
        )
        .distinct()
        .all()
    }


def _wrong_ever_ids(db: Session, student_id: str, course_id: str) -> set[str]:
    return {
        r[0]
        for r in db.query(CourseExamAttempt.question_id)
        .filter(
            CourseExamAttempt.student_id == student_id,
            CourseExamAttempt.course_id == course_id,
            CourseExamAttempt.result == "incorrect",
        )
        .distinct()
        .all()
    }


def _sitting_valid(sitting: CourseExamSitting, by_id: dict) -> bool:
    """이 회차의 저장된 순열이 현재 문항 구성과 정합한가 — 재사용·채점 전 불변식.

    order는 발급 시점 옵션 수의 순열이다. 그 뒤 강사가 문항을 삭제하거나 보기 수를 바꾸면
    order가 현재 옵션과 어긋나 [q.options[i] for i in order]·order.index(정답)가 터진다
    (skeptic CONFIRMED). 길이가 같으면 order는 여전히 range(len)의 유효 순열이라 안전하다."""
    for item in sitting.questions:
        q = by_id.get(item["question_id"])
        if q is None or len(item.get("order", [])) != len(q.options):
            return False
    return True


def _course_lecture_ids(db: Session, course_id: str) -> set[str]:
    return {
        r[0]
        for r in db.query(Lecture.id)
        .filter(Lecture.course_id == course_id, Lecture.status == "active")
        .all()
    }


def _completion(db: Session, student_id: str, course_id: str) -> CourseCompletion | None:
    return (
        db.query(CourseCompletion)
        .filter(
            CourseCompletion.student_id == student_id,
            CourseCompletion.course_id == course_id,
        )
        .first()
    )


def _grant_completion_if_mastered(
    db: Session, student_id: str, course_id: str, active_ids: set[str],
    *, perfect_sitting: bool = False,
) -> CourseCompletion | None:
    """전 활성 문항 정복이면 수료 부여(멱등). 수료 시점 스냅샷을 남긴다.

    **perfect(완벽 통과) = 현재 활성 전 문항을 '한 회차에 모두 맞힌 적'이 있는가**
    (0719 정책 재설계 — 재도전 경로+공정성). perfect_sitting=이번 제출이 그 완벽 회차였나.
    - 첫 회차에 전 문항을 다 담아 아싸면 → 수료와 동시에 perfect=True.
    - 여러 회차로 조금씩 정복해 수료하면 perfect=False(한 회차 무결점이 아님).
    - 수료 후 '완벽 도전'(전 문항 한 판)을 아싸면 기존 수료를 perfect로 **승급**한다
      — 한 번 틀렸다고 영구 박탈되던 옛 규칙의 가혹함을 없앤다.
    이 정의는 오답 이력을 보지 않으므로, 강사가 나중에 삭제한 문항의 오답이 완벽 통과를
    막던 불공정(skeptic 지적)도 자연히 사라진다. 문항이 0개면 수료 대상 아님(시험 없는 코스)."""
    if not active_ids:
        return None
    existing = _completion(db, student_id, course_id)
    if existing:
        # 이미 수료 — 완벽 도전으로 전 문항을 한 회차에 정복하면 perfect로 승급(멱등)
        if perfect_sitting and not existing.perfect:
            existing.perfect = True
            db.flush()
        return existing
    mastered = _mastered_ids(db, student_id, course_id)
    if not active_ids <= mastered:
        return None
    sittings = (
        db.query(CourseExamSitting)
        .filter(
            CourseExamSitting.student_id == student_id,
            CourseExamSitting.course_id == course_id,
            CourseExamSitting.submitted_at.isnot(None),
        )
        .count()
    )
    row = CourseCompletion(
        student_id=student_id,
        course_id=course_id,
        passed_at=datetime.now(),
        question_count=len(active_ids),
        sittings_count=sittings,
        perfect=perfect_sitting,
    )
    try:
        # 수료 삽입만 SAVEPOINT로 격리 — 동시 최종 제출(두 탭) 경합 시 UNIQUE(student,course)
        # 위반이 나도 500 대신 이미 부여된 수료를 돌려준다. 바깥 트랜잭션(이 회차 응답·제출)은
        # 보존된다(전체 rollback이 아니라 savepoint만 되감김).
        with db.begin_nested():
            db.add(row)
        return row
    except IntegrityError:
        return _completion(db, student_id, course_id)


# ---------------------------------------------------------------- 강사·운영자 CRUD
class _ExamQuestionCreate(BaseModel):
    prompt: str = Field(min_length=1)
    options: list[str]
    # 단일 정답도 [i]로 — 강의 문항의 answer_indexes 규약과 동일(다답=집합 정확 일치)
    answer_indexes: list[int]
    explain: str | None = None
    origin: str = "manual"
    source: str | None = Field(default=None, max_length=300)
    status: str = "draft"


class _ExamQuestionUpdate(BaseModel):
    prompt: str | None = Field(default=None, min_length=1)
    options: list[str] | None = None
    answer_indexes: list[int] | None = None
    explain: str | None = None
    origin: str | None = None
    source: str | None = Field(default=None, max_length=300)
    status: str | None = None  # draft|active
    order_no: int | None = None


def _validate_question(options: list[str], answer_indexes: list[int], origin: str, source: str | None):
    if not (2 <= len(options) <= 6) or not all(isinstance(o, str) and o.strip() for o in options):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="보기는 2~6개의 비지 않은 문항이어야 합니다.")
    idxs = sorted(set(int(i) for i in answer_indexes))
    if not idxs or any(i < 0 or i >= len(options) for i in idxs):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="정답 번호가 보기 범위를 벗어났습니다.")
    if origin not in ORIGINS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="origin이 올바르지 않습니다.")
    if origin == "past_exam" and not (source or "").strip():
        # 비영리 교육용 이용 전제(설계 §2) — 출처 표시는 선택이 아니라 강제다
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="기출 문항은 출처(source)가 필수입니다. 예: 2024학년도 수능 수학 15번",
        )
    return idxs


def _question_row(q: CourseExamQuestion) -> dict:
    return {
        "id": q.id,
        "course_id": q.course_id,
        "prompt": q.prompt,
        "options": q.options,
        "answer_indexes": q.answer_indexes,
        "explain": q.explain,
        "origin": q.origin,
        "source": q.source,
        "order_no": q.order_no,
        "status": q.status,
        "created_at": q.created_at.isoformat() if q.created_at else None,
    }


@router.get("/ops/courses/{course_id}/exam-questions")
def ops_list_exam_questions(
    course_id: str,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    from app.api.v1.endpoints.lectures import _get_ops_course

    _get_ops_course(db, course_id, principal)  # 소유 스코프 — 남의 코스 404
    rows = (
        db.query(CourseExamQuestion)
        .filter(
            CourseExamQuestion.course_id == course_id,
            CourseExamQuestion.status != "deleted",
        )
        .order_by(CourseExamQuestion.order_no, CourseExamQuestion.created_at)
        .all()
    )
    return [_question_row(q) for q in rows]


@router.post("/ops/courses/{course_id}/exam-questions")
def ops_create_exam_question(
    course_id: str,
    req: _ExamQuestionCreate,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    from app.api.v1.endpoints.lectures import _get_ops_course

    _get_ops_course(db, course_id, principal)
    idxs = _validate_question(req.options, req.answer_indexes, req.origin, req.source)
    if req.status not in ("draft", "active"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="status는 draft|active만 가능합니다.")
    max_order = (
        db.query(CourseExamQuestion)
        .filter(CourseExamQuestion.course_id == course_id)
        .count()
    )
    q = CourseExamQuestion(
        course_id=course_id,
        prompt=req.prompt.strip(),
        options=[o.strip() for o in req.options],
        answer_indexes=idxs,
        explain=(req.explain or "").strip() or None,
        origin=req.origin,
        source=(req.source or "").strip() or None,
        order_no=max_order + 1,
        status=req.status,
        created_by=principal.id,
    )
    db.add(q)
    db.flush()
    audit(db, action="course.exam_question.create", actor_user_id=principal.id,
          target_type="course_exam_question", target_id=q.id,
          after={"course_id": course_id, "origin": q.origin, "status": q.status})
    db.commit()
    return _question_row(q)


@router.put("/ops/courses/{course_id}/exam-questions/{question_id}")
def ops_update_exam_question(
    course_id: str,
    question_id: str,
    req: _ExamQuestionUpdate,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    from app.api.v1.endpoints.lectures import _get_ops_course

    _get_ops_course(db, course_id, principal)
    q = db.get(CourseExamQuestion, question_id)
    if q is None or q.course_id != course_id or q.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="문항을 찾을 수 없습니다.")
    options = req.options if req.options is not None else q.options
    answers = req.answer_indexes if req.answer_indexes is not None else q.answer_indexes
    origin = req.origin if req.origin is not None else q.origin
    source = req.source if req.source is not None else q.source
    idxs = _validate_question(options, answers, origin, source)
    if req.status is not None and req.status not in ("draft", "active"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="status는 draft|active만 가능합니다.")
    if req.prompt is not None:
        q.prompt = req.prompt.strip()
    q.options = [o.strip() for o in options]
    q.answer_indexes = idxs
    if req.explain is not None:
        q.explain = req.explain.strip() or None
    q.origin = origin
    q.source = (source or "").strip() or None
    if req.status is not None:
        q.status = req.status
    if req.order_no is not None:
        q.order_no = req.order_no
    audit(db, action="course.exam_question.update", actor_user_id=principal.id,
          target_type="course_exam_question", target_id=q.id,
          after={"status": q.status, "origin": q.origin})
    db.commit()
    return _question_row(q)


@router.delete("/ops/courses/{course_id}/exam-questions/{question_id}")
def ops_delete_exam_question(
    course_id: str,
    question_id: str,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    from app.api.v1.endpoints.lectures import _get_ops_course

    _get_ops_course(db, course_id, principal)
    q = db.get(CourseExamQuestion, question_id)
    if q is None or q.course_id != course_id or q.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="문항을 찾을 수 없습니다.")
    q.status = "deleted"  # 소프트 삭제 — 응답 기록(attempts)의 참조 대상을 보존
    audit(db, action="course.exam_question.delete", actor_user_id=principal.id,
          target_type="course_exam_question", target_id=q.id, after=None)
    db.commit()
    return {"ok": True}


# --------------------------------------------- 2단계: 문항 채우기 가속(to-exam · LLM 생성)
@router.post("/ops/courses/{course_id}/exam-questions/import-from-lectures")
def ops_import_exam_from_lectures(
    course_id: str,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """코스 소속 강의의 활성 확인 문항 → 시험 문항(origin=lecture, draft) 일괄 복사(to-exam).

    왜: 강사가 시험 문항을 하나씩 손으로 넣지 않게 — 이미 검수된 강의 확인 문항을 재활용한다.
    강의 문항·시험 문항은 둘 다 인덱스 기반(options[str]·answer_indexes)이라 형식 변환이
    없다(to-bank와 다른 점). **멱등**: 이미 가져온 강의 문항은 payload 마커(exam_imported)로
    건너뛴다(to-bank의 bank_placed와 동형). 이미지 문항·형식 불량은 조용히 건너뛰되(전체
    실패로 만들지 않음) 개수를 정직하게 반환한다. 가져온 문항은 draft — 강사 검수 후 active."""
    from app.api.v1.endpoints.lectures import _get_ops_course

    _get_ops_course(db, course_id, principal)
    lec_ids = _course_lecture_ids(db, course_id)
    if not lec_ids:
        return {"imported": 0, "skipped": 0}
    lec_titles = {
        r[0]: r[1]
        for r in db.query(Lecture.id, Lecture.title).filter(Lecture.id.in_(lec_ids)).all()
    }
    lqs = (
        db.query(LectureQuestion)
        .filter(LectureQuestion.lecture_id.in_(lec_ids), LectureQuestion.status == "active")
        .order_by(LectureQuestion.lecture_id, LectureQuestion.order_no)
        .all()
    )
    max_order = (
        db.query(CourseExamQuestion).filter(CourseExamQuestion.course_id == course_id).count()
    )
    imported = 0
    skipped = 0
    for lq in lqs:
        payload = lq.payload or {}
        if (payload.get("exam_imported") or {}).get("course_id") == course_id:
            skipped += 1  # 이미 이 코스 시험으로 가져옴(멱등)
            continue
        if payload.get("prompt_image") or payload.get("option_images"):
            skipped += 1  # 이미지 문항은 시험 형식도 미지원
            continue
        prompt = (payload.get("prompt") or "").strip()
        options = payload.get("options") or []
        answers = sorted({int(i) for i in (lq.answer_indexes or [lq.answer_index])})
        if not prompt or not (2 <= len(options) <= 6) or not all(
            isinstance(o, str) and o.strip() for o in options
        ):
            skipped += 1
            continue
        if not answers or any(a < 0 or a >= len(options) for a in answers):
            skipped += 1
            continue
        max_order += 1
        q = CourseExamQuestion(
            course_id=course_id,
            prompt=prompt,
            options=[o.strip() for o in options],
            answer_indexes=answers,
            explain=(payload.get("explain") or "").strip() or None,
            origin="lecture",
            source=f"강의: {lec_titles.get(lq.lecture_id, '')}".strip(),
            order_no=max_order,
            status="draft",
            created_by=principal.id,
        )
        db.add(q)
        db.flush()
        # 원 강의 문항에 가져옴 표식 — 멱등 + 콘솔 '시험으로 가져옴' 배지 근거
        lq.payload = {
            **payload,
            "exam_imported": {
                "course_id": course_id,
                "exam_qid": q.id,
                "at": datetime.now().isoformat(timespec="seconds"),
            },
        }
        imported += 1
    audit(db, action="course.exam_question.import_lectures", actor_user_id=principal.id,
          target_type="course", target_id=course_id,
          after={"imported": imported, "skipped": skipped})
    db.commit()
    return {"imported": imported, "skipped": skipped}


class _ExamGenerateReq(BaseModel):
    n: int = 5


@router.post("/ops/courses/{course_id}/exam-questions/generate")
def ops_generate_exam_questions(
    course_id: str,
    req: _ExamGenerateReq,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """코스 강의 구성(제목·설명) 기반 LLM 수료 시험 문항 자동 생성(origin=llm, draft).

    왜: 강사가 백지에서 시작하지 않게 — 운영자가 고른 생성 슬롯 모델(Anthropic/OpenAI, #26
    멀티프로바이더)로 코스 전체를 아우르는 초안을 만든다. **자기검증은 하지 않는다**(시험은
    시청 검증 캡차가 아니라 지식·이해 확인이라 상식으로 풀리는 문항도 정당). 학생 노출 전
    강사 검수(draft→active). 정직성: 키 없으면 503, 생성/파싱 실패 502 — stub 문항 금지."""
    from app.api.v1.endpoints.lectures import _get_ops_course
    from app.clients.ai_client import (
        AiGenerationError,
        AiNotConfiguredError,
        generate_course_exam_questions,
    )
    from app.services import ai_models_service, settings_service

    course = _get_ops_course(db, course_id, principal)
    llm_key = settings_service.resolve_anthropic_key(db)
    openai_key = settings_service.resolve_openai_key(db)
    if not llm_key and not openai_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM API 키가 설정되지 않아 문항 자동 생성을 쓸 수 없습니다. 운영 콘솔 '설정'에서 키를 입력해 주세요.",
        )
    # 강의 목록 + 각 강의의 전사(자막)를 근거로 넣는다 — 제목·설명만 쓰던 것보다 실제 내용에
    # 기반한 깊은 문항이 나온다. 자막이 있는 강의는 그 텍스트를, 없으면 제목·설명만 쓴다.
    # 총·강의별 문자 상한으로 토큰 폭발을 막는다(강의가 많은 코스 방어).
    lec_ids = _course_lecture_ids(db, course_id)
    lecture_rows = (
        db.query(Lecture.id, Lecture.title, Lecture.description)
        .filter(Lecture.id.in_(lec_ids))
        .order_by(Lecture.order_no)
        .all()
        if lec_ids
        else []
    )
    tr_map: dict[str, list] = {}
    if lec_ids:
        for t in (
            db.query(LectureTranscript).filter(LectureTranscript.lecture_id.in_(lec_ids)).all()
        ):
            tr_map[t.lecture_id] = t.segments or []
    lectures = []
    budget = _COURSE_EXAM_TR_TOTAL_CAP
    used_transcripts = 0
    for lid, title, desc in lecture_rows:
        text = " ".join(str(s.get("text") or "") for s in tr_map.get(lid, [])).strip()
        take = ""
        if text and budget > 0:
            take = text[: min(len(text), _COURSE_EXAM_TR_PER_LECTURE, budget)]
            budget -= len(take)
            used_transcripts += 1
        lectures.append({"title": title, "description": desc, "transcript": take})

    def _to_models(cands):
        return [
            {"config_id": m.id, "model_id": m.model_id, "provider": m.provider} for m in cands
        ] or None

    gen_models = _to_models(ai_models_service.resolve_candidates(db, "generate"))

    def _on_usage(config_id, tokens_in, tokens_out):
        if config_id:
            ai_models_service.record_usage(db, config_id, tokens_in, tokens_out)

    try:
        items = generate_course_exam_questions(
            course_title=course.title,
            subject=course.subject,
            lectures=lectures,
            n=req.n,
            api_key=llm_key,
            openai_key=openai_key,
            models=gen_models,
            on_usage=_on_usage,
        )
    except AiNotConfiguredError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM API 키가 설정되지 않아 문항 자동 생성을 쓸 수 없습니다. 운영 콘솔 '설정'에서 키를 입력해 주세요.",
        )
    except AiGenerationError as e:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"시험 문항 자동 생성에 실패했습니다: {e}"
        )

    max_order = (
        db.query(CourseExamQuestion).filter(CourseExamQuestion.course_id == course_id).count()
    )
    created: list[CourseExamQuestion] = []
    for item in items:
        max_order += 1
        q = CourseExamQuestion(
            course_id=course_id,
            prompt=item["prompt"],
            options=item["options"],
            answer_indexes=[int(item["answer_index"])],
            explain=item.get("explain") or None,
            origin="llm",
            source=None,
            order_no=max_order,
            status="draft",
            created_by=principal.id,
        )
        db.add(q)
        created.append(q)
    db.flush()
    audit(db, action="course.exam_question.generate", actor_user_id=principal.id,
          target_type="course", target_id=course_id,
          after={"created": len(created), "n": req.n, "transcripts": used_transcripts})
    db.commit()
    return {
        "created": len(created),
        # 자막을 근거로 쓴 강의 수 — 0이면 제목·설명만으로 생성(콘솔이 안내)
        "used_transcripts": used_transcripts,
        "lecture_count": len(lecture_rows),
        "questions": [_question_row(q) for q in created],
    }


# ------------------------------------------------------- 강사·운영자: 시험 통계(대시보드)
@router.get("/ops/courses/{course_id}/exam-stats")
def ops_exam_stats(
    course_id: str,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """코스 수료 시험 지표 — 강사(약한 문항·오래 걸린 문항)·운영자(수료율)용 대시보드 원천.

    왜: 문항을 넣고 끝이 아니라 '학생이 어디서 막히나'를 강사가 보고 문항을 고칠 수 있게 —
    통과율 낮은 문항 = 잘못 냈거나 강의가 부족한 대목, 오답 재시도 많은 문항 = 어려운 대목.
    운영자는 코스별 수료율로 커리큘럼 건강도를 본다. 스코프는 _get_ops_course 재사용(강사는
    자기 코스만·남의 코스 404, 운영자는 전체).

    집계 규칙:
    - 코스 레벨: 응시 학생(제출 회차가 있는 distinct 학생)·수료·완벽 통과·수료율(수료/응시).
    - 문항 레벨(활성 문항만 — 강사가 고칠 수 있는 대상): 통과율 = 그 문항을 맞힌 적 있는 학생 /
      시도한 학생(정복률), 오답 시도 수(재시도 부담), 평균 풀이 시간. **평균 풀이 시간은 근사값**
      — solve_time_ms가 회차 전체 시간을 문항 수로 나눈 값이라(제출 경로) 문항 고유 시간이
      아니다. 그래도 '오래 걸린 회차에 든 문항' 신호로는 쓸 만해 secondary로 노출한다.
    """
    from app.api.v1.endpoints.lectures import _get_ops_course

    _get_ops_course(db, course_id, principal)  # 소유 스코프 — 남의 코스 404
    active = _active_questions(db, course_id)

    # --- 코스 레벨: 응시·수료·완벽·수료율
    attempted_students = (
        db.query(func.count(func.distinct(CourseExamSitting.student_id)))
        .filter(
            CourseExamSitting.course_id == course_id,
            CourseExamSitting.submitted_at.isnot(None),
        )
        .scalar()
        or 0
    )
    completions = (
        db.query(func.count(CourseCompletion.id))
        .filter(CourseCompletion.course_id == course_id)
        .scalar()
        or 0
    )
    perfects = (
        db.query(func.count(CourseCompletion.id))
        .filter(CourseCompletion.course_id == course_id, CourseCompletion.perfect.is_(True))
        .scalar()
        or 0
    )

    # --- 문항 레벨: 한 번의 그룹 집계로 시도/정답/distinct 학생/평균 시간
    agg = {
        r[0]: r
        for r in db.query(
            CourseExamAttempt.question_id,
            func.count(CourseExamAttempt.id),  # 총 시도 수(재시도 포함)
            func.sum(case((CourseExamAttempt.result == "correct", 1), else_=0)),  # 정답 시도
            func.count(func.distinct(CourseExamAttempt.student_id)),  # 시도한 distinct 학생
            func.avg(CourseExamAttempt.solve_time_ms),  # 근사 평균 풀이 시간
        )
        .filter(CourseExamAttempt.course_id == course_id)
        .group_by(CourseExamAttempt.question_id)
        .all()
    }
    # 정복(맞힌 적 있는) distinct 학생 — 통과율 분자
    mastered = {
        r[0]: int(r[1])
        for r in db.query(
            CourseExamAttempt.question_id,
            func.count(func.distinct(CourseExamAttempt.student_id)),
        )
        .filter(
            CourseExamAttempt.course_id == course_id,
            CourseExamAttempt.result == "correct",
        )
        .group_by(CourseExamAttempt.question_id)
        .all()
    }

    questions = []
    for q in active:
        row = agg.get(q.id)
        total_attempts = int(row[1]) if row else 0
        correct_attempts = int(row[2] or 0) if row else 0
        students_attempted = int(row[3]) if row else 0
        avg_ms = int(row[4] or 0) if row else 0
        students_mastered = mastered.get(q.id, 0)
        questions.append({
            "id": q.id,
            "prompt": q.prompt,
            "origin": q.origin,
            "students_attempted": students_attempted,
            "students_mastered": students_mastered,
            # 통과율 = 정복 학생 / 시도 학생 (아무도 안 풀었으면 None — 0%로 오해 방지)
            "pass_rate": round(students_mastered / students_attempted, 3) if students_attempted else None,
            "total_attempts": total_attempts,
            "wrong_attempts": total_attempts - correct_attempts,  # 재시도 부담(어려움 신호)
            "avg_solve_ms": avg_ms,  # 근사값(회차 시간/문항 수)
        })

    return {
        "course_id": course_id,
        "attempted_students": int(attempted_students),
        "completions": int(completions),
        "perfects": int(perfects),
        # 수료율 = 수료 학생 / 응시 학생(응시 0이면 None — 분모 0 방지)
        "completion_rate": round(completions / attempted_students, 3) if attempted_students else None,
        "active_question_count": len(active),
        "questions": questions,
    }


# ---------------------------------------------------------------- 학생: 상태·발급·채점
def _exam_state(db: Session, student_id: str, course_id: str) -> dict:
    """시험 카드 상태의 단일 원천 — 강의 완주 게이트 + 풀/정복 + 수료."""
    active = _active_questions(db, course_id)
    active_ids = {q.id for q in active}
    lec_ids = _course_lecture_ids(db, course_id)
    done = bank_mode.completed_lecture_ids(db, student_id)
    lectures_done = len(lec_ids & done)
    completion = _completion(db, student_id, course_id)
    mastered = _mastered_ids(db, student_id, course_id) & active_ids
    available = bool(active_ids) and bool(lec_ids) and lec_ids <= done
    passed = completion is not None
    perfect = bool(completion.perfect) if completion else False
    return {
        "has_exam": bool(active_ids),
        "question_count": len(active_ids),
        "mastered_count": len(mastered),
        "lectures_total": len(lec_ids),
        "lectures_done": lectures_done,
        # 응시 자격 = 코스의 모든 활성 강의 완주(문제은행 잠금과 같은 정본)
        "available": available,
        "passed": passed,
        "perfect": perfect,
        "passed_at": completion.passed_at.isoformat() if completion else None,
        # 완벽 도전 가능 = 수료했지만 아직 완벽 통과 아님(재도전 경로 — 전 문항 한 판 아싸기)
        "can_perfect_challenge": passed and not perfect and available,
    }


# (제거됨 0719) GET /courses/completions — '나의 기록' 수료 현황이 수료한 것만이 아니라
# 진행 중·잠김 코스까지 함께 보여주도록 바뀌면서(사용자 결정), 원천을 GET /courses의
# exam{} 요약(passed_at 추가)으로 통일했다. 별도 완료 목록 엔드포인트는 소비처가 없어 삭제.


@router.get("/courses/{course_id}/exam")
def exam_state(
    course_id: str,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    from app.models import Course

    c = db.get(Course, course_id)
    if c is None or c.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="코스를 찾을 수 없습니다.")
    return {"course_id": course_id, "title": c.title, **_exam_state(db, principal.id, course_id)}


@router.get("/courses/{course_id}/exam/certificate")
def exam_certificate(
    course_id: str,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """수료증 데이터 — 실제 수료한 학생에게만 발급(위조 방지).

    수료증 이미지·PDF는 프론트에서 캔버스로 그리지만, **그 근거 데이터는 서버가 수료 사실을
    검증한 뒤에만 내려준다.** 이렇게 나눠야 미수료 학생이 클라이언트에서 값을 지어내 위조하는
    걸 막는다(수료 여부·수료일·문항수는 course_completions가 유일 원천). 학생 대면 산출물이라
    실명이 아니라 nickname(가명)을 싣는다 — 이 코드베이스의 학생 화면 규약(실명은 교사·기관
    화면 전용). serial은 completion.id에서 결정적으로 파생해 재발급해도 같은 번호가 나온다."""
    from app.models import Course, User

    c = db.get(Course, course_id)
    if c is None or c.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="코스를 찾을 수 없습니다.")
    completion = _completion(db, principal.id, course_id)
    if completion is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="아직 이 코스를 수료하지 않았어요. 모든 시험 문항을 정복하면 수료증이 발급돼요.",
        )
    instructor = db.get(User, c.instructor_id) if c.instructor_id else None
    return {
        "course_id": course_id,
        "course_title": c.title,
        "subject": c.subject,
        # 학생 화면 규약 — 실명 대신 가명(nickname). 학생 계정은 nickname이 항상 존재한다.
        "student_name": principal.student.nickname if principal.student else "",
        # 강사가 삭제됐어도 수료증은 유효 — 발급 주체를 서비스명으로 폴백(수료 사실은 불변).
        "instructor_name": instructor.name if instructor else "CatChap",
        "passed_at": completion.passed_at.isoformat(),
        "perfect": bool(completion.perfect),
        "question_count": completion.question_count,
        "sittings_count": completion.sittings_count,
        # 검증용 일련번호 — completion.id에서 결정적 파생(재발급해도 동일). 위·변조 대조용.
        "serial": "CATCHAP-" + completion.id.replace("-", "")[:12].upper(),
    }


def _shuffled_sitting(student_id: str, course_id: str, picked: list) -> CourseExamSitting:
    """회차 생성 헬퍼 — 문항별 보기 셔플 순열을 서버에 보관(표시 위치 → 원본 인덱스)."""
    return CourseExamSitting(
        student_id=student_id,
        course_id=course_id,
        questions=[
            {"question_id": q.id, "order": random.sample(range(len(q.options)), len(q.options))}
            for q in picked
        ],
    )


@router.post("/courses/{course_id}/exam/session")
def exam_session(
    course_id: str,
    perfect: bool = False,  # 완벽 도전 — 전 문항을 한 판에(수료 후 재도전 경로)
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """회차 발급 — 정답·해설 미포함, 보기는 문항별 셔플(순열은 서버 보관).

    일반 모드: 정복 못 한 문항(안 푼 → 틀린 순)을 최대 10문항. 미제출 회차 재사용(파밍 차단).
    완벽 도전(perfect=True): 현재 활성 **전 문항을 한 회차에**(10 상한 없음). 수료 후에도
    가능 — 한 회차에 다 맞히면 완벽 통과로 승급(0719 정책 재설계·재도전 경로)."""
    from app.models import Course

    c = db.get(Course, course_id)
    if c is None or c.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="코스를 찾을 수 없습니다.")
    st = _exam_state(db, principal.id, course_id)
    if not st["has_exam"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="이 코스에는 수료 시험이 없습니다.")
    # 완벽 도전은 '수료 후 재도전' 전용 — 미수료 학생은 일반 시험을 본다(perfect 파라미터
    # 무시). 이렇게 나누면 회차 종류가 학생 상태로 유일하게 갈려(미수료=일반·수료=완벽 도전)
    # 커버리지로 모드를 헷갈릴 여지가 없다(작은 코스의 일반 회차도 전 문항 커버라 애매해짐 방지).
    if st["passed"] and (not perfect or st["perfect"]):
        return {"passed": True, "perfect": st["perfect"], "passed_at": st["passed_at"]}
    if not st["available"]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"수료 시험은 강의를 전부 완주하면 열려요. ({st['lectures_done']}/{st['lectures_total']} 완주)",
        )
    challenge = st["passed"]  # 이 지점에 온 수료 학생 = 완벽 도전(위 게이트가 그 외를 걸러냄)

    active = _active_questions(db, course_id)
    by_id = {q.id: q for q in active}
    active_ids = set(by_id)

    # 미제출 회차 재사용 — 단, 문항이 삭제·비활성됐으면 그 회차를 닫고 새로 낸다
    open_sitting = (
        db.query(CourseExamSitting)
        .filter(
            CourseExamSitting.student_id == principal.id,
            CourseExamSitting.course_id == course_id,
            CourseExamSitting.submitted_at.is_(None),
        )
        .first()
    )
    if challenge:
        # 완벽 도전 회차는 전 문항 커버여야 재사용(강사가 문항을 더했으면 새로 낸다)
        open_full = bool(open_sitting) and {i["question_id"] for i in open_sitting.questions} == active_ids
        reusable = bool(open_sitting) and _sitting_valid(open_sitting, by_id) and open_full
    else:
        reusable = bool(open_sitting) and _sitting_valid(open_sitting, by_id)
    if reusable:
        sitting = open_sitting
    else:
        if open_sitting:
            # 재사용 불가 회차 폐기 — 문항 소실/보기 수 변경으로 순열이 어긋났거나(skeptic
            # CONFIRMED: 그대로 두면 채점 시 IndexError/ValueError로 시험 영구 봉쇄) 또는
            # 완벽 도전인데 커버리지가 어긋난 경우.
            db.delete(open_sitting)
        if challenge:
            # 완벽 도전 — 전 문항(정복 여부 무관·10 상한 없음)을 한 회차에
            picked = list(active)
            random.shuffle(picked)
        else:
            mastered = _mastered_ids(db, principal.id, course_id)
            wrong = _wrong_ever_ids(db, principal.id, course_id)
            unmastered = [q for q in active if q.id not in mastered]
            if not unmastered:
                # 전부 정복인데 수료가 없는 상태(예: 틀렸던 문항을 강사가 삭제) — 정합 회복
                comp = _grant_completion_if_mastered(db, principal.id, course_id, active_ids)
                db.commit()
                st2 = _exam_state(db, principal.id, course_id)
                return {"passed": comp is not None, "perfect": st2["perfect"], "passed_at": st2["passed_at"]}
            # 안 푼 것(오답 이력도 없는 것) 먼저, 그다음 틀린 것 — 각 그룹 안에서 섞는다
            fresh = [q for q in unmastered if q.id not in wrong]
            retry = [q for q in unmastered if q.id in wrong]
            random.shuffle(fresh)
            random.shuffle(retry)
            picked = (fresh + retry)[:EXAM_SITTING_SIZE]
        sitting = _shuffled_sitting(principal.id, course_id, picked)
        db.add(sitting)
        db.commit()

    questions = []
    for item in sitting.questions:
        q = by_id[item["question_id"]]
        questions.append(
            {
                "question_id": q.id,
                "prompt": q.prompt,
                # 표시 순서 = order[i]번째 원본 보기. 정답·해설은 제출 후에만.
                "options": [q.options[i] for i in item["order"]],
                "multi": len(q.answer_indexes) > 1,
                "origin": q.origin,
                "source": q.source,  # 기출 출처 — 비영리 이용 전제라 응시 화면에도 상시 노출
            }
        )
    return {
        "passed": False,
        "sitting_id": sitting.id,
        "questions": questions,
        # 완벽 도전 회차인가 — 화면이 '완벽 도전(전 문항 한 판)'으로 안내한다
        "perfect_challenge": challenge,
        "progress": {"mastered": st["mastered_count"], "total": st["question_count"]},
    }


class _ExamAnswer(BaseModel):
    question_id: str
    picks: list[int] = Field(default_factory=list)  # 표시 순서 기준 선택(무응답=빈 목록)


class _ExamSubmit(BaseModel):
    sitting_id: str
    answers: list[_ExamAnswer] = Field(default_factory=list)
    solve_time_ms: int = 0


@router.post("/courses/{course_id}/exam/submit")
def exam_submit(
    course_id: str,
    req: _ExamSubmit,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """회차 채점 → 결과지(문항별 정오·해설·출처) + 진행 + (전 문항 정복 시) 수료.

    무응답은 오답으로 채점(운 좋은 정답 없음 — 틀린 것으로 남아 다음 회차에 재출제).
    제출된 회차 재제출은 409(재채점·파밍 방지)."""
    sitting = db.get(CourseExamSitting, req.sitting_id)
    if sitting is None or sitting.student_id != principal.id or sitting.course_id != course_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="응시 회차를 찾을 수 없습니다.")
    if sitting.submitted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 제출된 회차입니다.")

    picks_by_q = {a.question_id: a.picks for a in req.answers}
    per_ms = max(0, int(req.solve_time_ms)) // max(1, len(sitting.questions))
    results = []
    correct_n = 0
    stale = 0  # 발급 후 강사가 편집·삭제해 채점 불가한 문항 수(학생에게 정직히 안내)
    graded_ids: set[str] = set()  # 이번 회차에서 실제 채점된 문항(완벽 회차 판정용)
    for item in sitting.questions:
        q = db.get(CourseExamQuestion, item["question_id"])
        order: list[int] = item.get("order", [])
        # 발급 후 문항 소실·보기 수 변경 → 저장된 순열이 어긋난다. 채점하면 order 매핑이
        # 터지므로(skeptic CONFIRMED) 이 문항은 채점에서 제외한다. 응답을 기록하지 않아
        # 미정복으로 남고, 다음 회차에서 유효 순열로 재출제된다.
        if q is None or len(order) != len(q.options):
            stale += 1
            continue
        graded_ids.add(q.id)
        # 표시 인덱스(0..len-1) 기준 선택만 인정 — 음수·범위 밖은 버린다(order[-1] 같은
        # 파이썬 음수 인덱싱으로 표시-순서 계약을 우회하는 것 차단, skeptic 경미 지적).
        picks = [p for p in picks_by_q.get(q.id, []) if isinstance(p, int) and 0 <= p < len(order)]
        original = sorted({order[p] for p in picks})  # 표시 → 원본 인덱스 복원(서버 순열 정본)
        answer_set = sorted(int(i) for i in q.answer_indexes)
        is_correct = bool(original) and original == answer_set
        if is_correct:
            correct_n += 1
        db.add(
            CourseExamAttempt(
                student_id=principal.id,
                course_id=course_id,
                question_id=q.id,
                sitting_id=sitting.id,
                result="correct" if is_correct else "incorrect",
                answer=original,
                solve_time_ms=per_ms,
            )
        )
        results.append(
            {
                "question_id": q.id,
                "prompt": q.prompt,
                "options": [q.options[i] for i in order],  # 학생이 본 표시 순서 그대로
                "picked": picks,
                # 정답의 표시 위치 — 결과지가 학생이 본 화면 기준으로 정답을 보여준다
                "answer": sorted(order.index(i) for i in answer_set),
                "correct": is_correct,
                "explain": q.explain,
                "origin": q.origin,
                "source": q.source,
            }
        )
    sitting.submitted_at = datetime.now()
    sitting.total = len(results)
    sitting.correct = correct_n
    db.flush()

    active_ids = {q.id for q in _active_questions(db, course_id)}
    # 완벽 회차 = 이 한 회차가 현재 활성 전 문항을 담아 하나도 안 틀리고 다 맞힘(stale 없음).
    perfect_sitting = (
        stale == 0 and len(results) > 0
        and correct_n == len(results) and graded_ids == active_ids
    )
    completion = _grant_completion_if_mastered(
        db, principal.id, course_id, active_ids, perfect_sitting=perfect_sitting
    )
    mastered = _mastered_ids(db, principal.id, course_id) & active_ids
    db.commit()
    return {
        "total": len(results),
        "correct": correct_n,
        "results": results,
        # 발급 후 강사 편집으로 채점 못 한 문항 수 — 0보다 크면 화면이 "일부 문항이 바뀌어
        # 다음 회차에서 다시 나와요"를 안내한다(조용히 삼키지 않는다)
        "stale": stale,
        "progress": {"mastered": len(mastered), "total": len(active_ids)},
        "passed": completion is not None,
        "perfect": bool(completion.perfect) if completion else False,
    }
