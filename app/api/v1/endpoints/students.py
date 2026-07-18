"""학생 API — 본인 데이터만 (require_student)."""

import re
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel as _GBaseModel
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.core.permissions import Principal, require_student
from app.db.session import get_db
from app.models import (
    Badge,
    Chapter,
    ChapterProgress,
    ClassRoom,
    CoinTransaction,
    ConceptRead,
    Content,
    DailyQuizStatus,
    LearningAttempt,
    Recommendation,
    ShopItem,
    StudentBadge,
    StudentItem,
    StudentProfile,
    StudentProgress,
    WrongAnswer,
)
from app.schemas.student import (
    AttemptCreate,
    AvatarRequest,
    ConceptReadRequest,
    PurchaseRequest,
    StudentProfileUpdate,
)
from app.services import aggregate
from app.services.aggregate import fb
from app.services.stats import D  # DB(stat_blobs) 우선, design_data fallback
from app.utils.helpers import date_label

router = APIRouter(tags=["students"])

DAILY_LEARNING_COIN_CAP = 300  # 하루 학습 보상 코인 총량 상한(자기신고 파밍 방지)
ALL_SUBJECTS_STICKER_COINS = 30  # 6과목 완주 스티커와 함께 주는 보너스 코인(하루 1회, 자정 초기화)


def _me(principal: Principal) -> StudentProfile:
    assert principal.student is not None
    return principal.student


def _today_quiz_rows(db: Session, student_id: str) -> list[DailyQuizStatus]:
    """오늘의퀴즈 행 조회 — 오늘 날짜 행이 없으면 프리셋(과목/주제/보상)으로 생성."""
    today = date.today()
    rows = (
        db.query(DailyQuizStatus)
        .filter(DailyQuizStatus.student_id == student_id, DailyQuizStatus.quiz_date == today)
        .all()
    )
    # 프리셋 6과목 중 오늘 행이 없는 과목을 보충 생성.
    # (기존엔 '행이 하나도 없을 때만' 6과목 생성 → 한 과목만 풀어 그 행이 생기면 나머지 5과목이
    #  사라져 오늘의퀴즈에 한 과목만 보이던 버그 해소)
    have = {r.subject for r in rows}
    new_rows = []
    for preset in D.DAILY_QUIZ:
        if preset["subject"] not in have:
            r = DailyQuizStatus(
                student_id=student_id,
                quiz_date=today,
                subject=preset["subject"],
                topic=preset["topic"],
                status="todo",
                reward_coins=preset["reward"],
            )
            new_rows.append(r)
            rows.append(r)
    if new_rows:
        db.add_all(new_rows)
        db.commit()
    order = {s: i for i, s in enumerate(D.SUBJECT_ORDER)}
    rows.sort(key=lambda r: order.get(r.subject, len(order)))
    return rows


# 과목당 오늘의퀴즈 문항 수(= 인앱 EDU 세션 EDU_SESSION_TOTAL). '오늘 이 과목 풀었나' 판정 기준.
DAILY_QUIZ_TOTAL = 5


def _played_today(db: Session, student_id: str) -> dict[str, int]:
    """오늘 과목별 오늘의퀴즈 시도 수(chapter_no NULL = 습관 축만).

    화면상 '오늘의 퀴즈 현황'의 단일 기준(사용자 결정 2026-07-13): 한 과목의 오늘 시도가
    DAILY_QUIZ_TOTAL 이상이면 '오늘 완료'로 본다(마지막이 오답이어도 — 홈 진행바·과목카드·
    다음 과목·이어하기가 모두 이 기준을 공유해 서로 어긋나지 않게). 코인·랭킹·6과목 스티커의
    'done 승격'은 별개로 daily_quiz_status(정답 완주)만 인정한다(위조 방지 유지)."""
    start = datetime.combine(date.today(), time.min)
    return dict(
        db.query(LearningAttempt.subject, func.count(LearningAttempt.id))
        .filter(
            LearningAttempt.student_id == student_id,
            LearningAttempt.created_at >= start,
            LearningAttempt.chapter_no.is_(None),
            LearningAttempt.graded.is_(True),  # 서버 채점만 — 자기신고가 표시상 '완료'도 못 만들게
        )
        .group_by(LearningAttempt.subject)
        .all()
    )


def _played_set(played: dict[str, int]) -> set[str]:
    """오늘 (5문항) 푼 과목 집합 — '완료(현황)' 판정 공용."""
    return {s for s, n in played.items() if int(n) >= DAILY_QUIZ_TOTAL}


def _quiz_done_set(db: Session, student_id: str, played: dict[str, int] | None = None) -> set[str]:
    """화면 표시용 '오늘 완료' 과목 집합.

    현황 기준(시도 5문항) ∪ 정답완주(daily_quiz_status.status=='done'). 실제 흐름에선
    status=='done'이 시도≥5를 함의하므로 보통 시도 집합과 같지만, 정답완주 과목을 절대
    누락하지 않도록 합집합으로 둔다. 코인·랭킹 지급 판정과는 무관(그건 status 단독)."""
    if played is None:
        played = _played_today(db, student_id)
    done = _played_set(played)
    rows = (
        db.query(DailyQuizStatus.subject)
        .filter(
            DailyQuizStatus.student_id == student_id,
            DailyQuizStatus.quiz_date == date.today(),
            DailyQuizStatus.status == "done",
        )
        .all()
    )
    return done | {r.subject for r in rows}




# 랭킹 점수 산식(사용자 결정 2026-07-08): 정답률·풀이속도 + 6과목 완주 보너스 + 연속 완주 보너스.
# 학년별로만 합산 · 학기 누적(리셋 없음). 완료(daily_quiz_status done) 과목·일에 대해서만 점수 부여.
# - 정답률: 과목·일 정답률 0~100% → 0~10점
# - 풀이속도: 그 과목·일 '정답' 문항의 평균 풀이시간 — 빠를수록 0~5점(오답 빠른 찍기엔 점수 없음)
# - 6과목 완주: 하루 6과목 전부 완료 시 +30
# - 연속 완주: 6과목 완주가 전날에도 이어지면 그 날마다 +10 (꾸준함 보상)
RANK_ACC_MAX = 10  # 과목·일 정답률 만점(100%)
RANK_SPEED_MAX = 5  # 과목·일 속도 만점(빠른 정답)
RANK_SPEED_FAST_MS = 4000  # 평균 4초 이하 정답 → 속도 만점
RANK_SPEED_SLOW_MS = 20000  # 평균 20초 이상 → 속도 0점
RANK_FULLDAY_BONUS = 30  # 하루 6과목 완주 보너스
RANK_STREAK_STEP = 10  # 6과목 완주 연속 하루당 추가








# ---------------------------------------------------------------- 학습 홈
@router.get("/students/me/dashboard")
def dashboard(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
):
    me = _me(principal)
    quiz = _today_quiz_rows(db, me.id)
    today_total = len(quiz)
    # 과목 카드·진행바: '오늘의 퀴즈 현황' 단일 기준 = 오늘 시도 수(_played_today).
    # 5문항 다 풀면 완료로 본다(마지막 오답이어도) — 홈·오늘의퀴즈 페이지·결과화면 다음 과목이
    # 같은 기준을 써 서로 어긋나지 않게. 전체학습 주간 챕터(chapter_no 있음)는 제외해 습관 축과 분리.
    # 완료 = 시도≥5 ∪ 정답완주(status=='done'). status_done은 방금 조회한 quiz 행 재사용.
    played = _played_today(db, me.id)
    status_done = {q.subject for q in quiz if q.status == "done"}
    done_display = _played_set(played) | status_done
    today_done = sum(1 for card in D.HOME_SUBJECT_CARDS if card["subject"] in done_display)
    subjects = []
    for card in D.HOME_SUBJECT_CARDS:
        sub = card["subject"]
        total = card["total"]
        n = int(played.get(sub, 0))
        done = total if sub in done_display else min(total, n)
        state = "done" if sub in done_display else ("progress" if n > 0 else "todo")
        subjects.append(
            {**card, "done": done, "state": state, "meta": D.SUBJECT_META[sub]}
        )
    growth = aggregate.student_growth(db, me)  # 시도 없으면 None → 성장 그래프 데모
    # 배지·학년랭킹·AI코멘트 필드는 게임화 은퇴(0718)로 응답에서 제거 — 프론트도 안 읽는다.
    return {
        "nickname": me.nickname,
        "level": me.level,
        "coins": me.coins,
        "student_code": me.student_code,
        "today": {"done": today_done, "total": today_total},
        "subjects": subjects,
        # 성장 그래프: learning_attempts 실집계 (시도 없으면 D 데모값)
        "growth": fb(growth, D.HOME_GROWTH),
        # 성장 그래프가 데모값(시도 없음)이면 demo=True. 코인·레벨·오늘상태 등은 항상 실데이터.
        "demo": growth is None,
        "mascot_message": D.HOME_MASCOT_MESSAGE,
    }


# ---------------------------------------------------------------- 챕터지도/전체학습
@router.get("/students/me/progress")
def progress(
    subject: str | None = Query(default=None),
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    me = _me(principal)
    subjects = [subject] if subject in D.SUBJECT_ORDER else D.SUBJECT_ORDER
    prog_rows = {
        p.subject: p
        for p in db.query(StudentProgress).filter(StudentProgress.student_id == me.id).all()
    }
    chapter_rows = (
        db.query(Chapter)
        .filter(Chapter.subject.in_(subjects))
        .order_by(Chapter.subject, Chapter.order_no)
        .all()
    )
    by_subject: dict[str, list[Chapter]] = {}
    for ch in chapter_rows:
        by_subject.setdefault(ch.subject, []).append(ch)

    out = []
    for sub in subjects:
        chapters = by_subject.get(sub, [])
        p = prog_rows.get(sub)
        done = p.chapters_done if p else 0
        done = max(0, min(len(chapters), done))
        out.append(
            {
                "subject": sub,
                "meta": D.SUBJECT_META[sub],
                "done_chapters": done,
                "current_chapter": min(len(chapters), done + 1),
                "accuracy": p.accuracy if p else 0,
                "questions_done": p.questions_done if p else 0,
                "levels": D.RESULT_LEVELS,
                "chapters": [
                    {
                        "id": ch.id,
                        "no": ch.order_no,
                        "name": ch.name,
                        "count": ch.total_questions,
                        "state": "done" if i < done else ("current" if i == done else "locked"),
                    }
                    for i, ch in enumerate(chapters)
                ],
            }
        )
    if subject in D.SUBJECT_ORDER:
        return out[0]
    # 전체학습 헤더: 레벨(실컬럼) + 전체 진행률(완료 챕터/전체 챕터 실집계)
    total_ch = sum(len(x["chapters"]) for x in out)
    done_ch = sum(x["done_chapters"] for x in out)
    return {
        "subjects": out,
        "level": me.level,
        "overall_pct": round(done_ch / total_ch * 100) if total_ch else 0,
    }


# ---------------------------------------------------------------- 나의기록
@router.get("/students/me/records")
def records(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
):
    me = _me(principal)
    # learning_attempts 실집계 — 시도가 전혀 없으면 D(디자인 수치)로 화면 유지
    agg = aggregate.student_records(db, me) or {}
    weeks = fb(
        agg.get("weeks"),
        [
            {"label": w["label"], "minutes": round(w["v"] / 100 * 210), "pct": w["v"]}
            for w in D.RECORDS_WEEKS
        ],
    )
    mastery = fb(
        agg.get("mastery"),
        [{**m, "correct": round(m["pct"] / 100 * m["solved"])} for m in D.RECORDS_MASTERY],
    )
    series = fb(
        agg.get("accuracy_series"),
        {key: {"color": v["color"], "data": v["data"]} for key, v in D.RECORD_ACC_SERIES.items()},
    )
    return {
        "weeks": weeks,
        "calendar": fb(agg.get("calendar"), {**D.RECORDS_CAL, "learned": D.RECORDS_CAL_LEARNED}),
        "mastery": mastery,
        "accuracy_series": series,
        "accuracy_labels": ["6회 전", "5회 전", "4회 전", "3회 전", "2회 전", "최근"],
        "activities": fb(agg.get("activities"), D.RECORDS_ACTIVITIES),
        # 상단 통계 4종: 전체 기간 실집계 (시도 없으면 디자인 수치 유지)
        "stats": fb(agg.get("stats"), D.RECORDS_STATS),
        # 시도 기록이 없어 전부 디자인(데모)값이면 demo=True
        "demo": not agg,
    }


# ---------------------------------------------------------------- 오답노트
@router.get("/students/me/wrong-notes")
def wrong_notes(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
):
    me = _me(principal)
    rows = (
        db.query(WrongAnswer)
        .filter(WrongAnswer.student_id == me.id)
        .order_by(WrongAnswer.wrong_date.desc(), WrongAnswer.created_at.desc())
        .all()
    )
    items = []
    by_cat: dict[str, int] = {}
    for w in rows:
        tag = D.WRONG_TAGS.get(w.category, {})
        by_cat[w.category] = by_cat.get(w.category, 0) + 1
        items.append(
            {
                "id": w.id,
                "cat": w.category,
                "subject": w.subject,
                "question": w.question,
                "wrong": w.my_answer,
                "answer": w.correct_answer,
                "tip": w.tip,
                "date": date_label(w.wrong_date),
                "reviewed": w.reviewed,
                "chapter_no": w.chapter_no,  # 전체학습 챕터 오답이면 그 챕터, 오늘의 퀴즈면 null
                "tag": tag,
            }
        )
    reviewed_n = sum(1 for w in rows if w.reviewed)
    return {
        "items": items,
        "summary": {
            "total": len(items),
            "pending": len(items) - reviewed_n,
            "reviewed": reviewed_n,  # 복습 완료 수 (wrong_answers.reviewed 실데이터)
            "by_category": by_cat,
        },
        "tags": D.WRONG_TAGS,
    }


# ---------------------------------------------------------------- 연습장 필기 재생 (본인)




# ---------------------------------------------------------------- 배지




# ---------------------------------------------------------------- 추천
@router.get("/students/me/recommendations")
def recommendations(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
):
    me = _me(principal)
    title_map = {(r["subject"], r["chapter"]): r["title"] for r in D.RECOMMENDATIONS}
    rows = (
        db.query(Recommendation)
        .filter(Recommendation.student_id == me.id, Recommendation.status == "active")
        .order_by(Recommendation.created_at)
        .all()
    )
    return {
        "recommendations": [
            {
                "id": r.id,
                "title": title_map.get((r.subject, r.chapter_no), f"{r.subject} 챕터 {r.chapter_no}"),
                "subject": r.subject,
                "chapter": r.chapter_no,
                "priority": r.priority,
                "reason": r.reason,
                "meta": D.SUBJECT_META.get(r.subject, {}),
            }
            for r in rows
        ],
        "coins": me.coins,  # NAV 냥코인 칩
        "summary": D.RECO_SUMMARY,  # '이번 주 분석 요약' 문구 (stat_blobs 수정 가능)
    }


# ---------------------------------------------------------------- 오늘의퀴즈
@router.get("/students/me/bank-progress")
def bank_progress(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
):
    """전체학습(문제은행) 진도 — 과목별 안 푼/틀린/맞춘 문항 수 + 과목 정답률.

    챕터/주간 잠금/코인이 없는 은행 모드(0713)의 화면 카드용. 출제 우선순위와
    같은 분류(bank_mode.split_pool)를 쓰므로 화면 수치와 실제 출제가 항상 일치한다.
    """
    from app.services import bank_mode, subject_banks

    me = _me(principal)
    acc_by_subject = {
        p.subject: p.accuracy
        for p in db.query(StudentProgress).filter(StudentProgress.student_id == me.id).all()
    }
    out = []
    for subject in D.SUBJECT_ORDER:
        if subject not in subject_banks.LIVE_SUBJECTS:
            continue
        row = bank_mode.progress(db, me, subject)
        row["accuracy"] = acc_by_subject.get(subject)
        row["meta"] = D.SUBJECT_META.get(subject, {})
        out.append(row)
    return {"subjects": out}


@router.get("/students/me/q-today")
def q_today(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
):
    """오늘의 Q 현황 — 홈·문제은행의 'Q 카드' 원천(퀴즈 통합 1단계, 설계
    question-bank-scale-design.md 결정 ①②③).

    전 과목 통합 관점: 일일 목표(1세트=10문제) 진행·연속 학습일 + 과목별 큐(만기/틀린/새).
    프론트는 이걸로 '오늘의 Q 시작' 진입 과목(만기 많은 곳 우선)을 고른다 — 발급 자체는
    기존 과목 단위 챌린지를 그대로 쓴다(위젯·행동데이터 경로 불변)."""
    from app.services import bank_mode, subject_banks

    me = _me(principal)
    stats = bank_mode.q_daily_stats(db, me.id)
    subjects = []
    total_due = total_wrong = total_new = 0
    for subject in D.SUBJECT_ORDER:
        if subject not in subject_banks.LIVE_SUBJECTS:
            continue
        st = bank_mode.queue_status(db, me, subject)
        subjects.append({"subject": subject, **st, "meta": D.SUBJECT_META.get(subject, {})})
        total_due += st["due"]
        total_wrong += st["wrong"]
        total_new += st["new"]
    return {
        **stats,
        "total": {"due": total_due, "wrong": total_wrong, "new": total_new},
        "subjects": subjects,
    }


@router.get("/students/me/daily-quiz")
def daily_quiz(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
):
    me = _me(principal)
    rows = _today_quiz_rows(db, me.id)
    # 과목별 오늘 시도 수(5단계 진행 바용) — 하루 5문제(5단계) 기준
    _today_start = datetime.combine(date.today(), time.min)
    # 전체학습 주간 챕터 플레이(chapter_no 있음)는 오늘의퀴즈 진행바에서 제외(학습·습관 분리).
    _att_today = dict(
        db.query(LearningAttempt.subject, func.count(LearningAttempt.id))
        .filter(
            LearningAttempt.student_id == me.id,
            LearningAttempt.created_at >= _today_start,
            LearningAttempt.chapter_no.is_(None),
        )
        .group_by(LearningAttempt.subject)
        .all()
    )
    STAGES = 5

    def _card_status(r) -> str:
        # '오늘의 퀴즈 현황' 단일 기준(홈·결과화면과 동일): 오늘 5문항 다 풀면 완료로 표시
        # (마지막 오답이어도). 코인·랭킹의 정답완주 done은 별개(daily_quiz_status.status).
        if int(_att_today.get(r.subject, 0)) >= STAGES:
            return "done"
        return r.status

    quizzes = [
        {
            "id": r.id,
            "subject": r.subject,
            "topic": r.topic,
            "status": _card_status(r),
            "reward": r.reward_coins,
            "meta": D.SUBJECT_META.get(r.subject, {}),
            # 5단계 진행 바: 완료면 5/5, 아니면 오늘 시도 수(최대 5)
            "stages": STAGES,
            "stage_done": min(STAGES, int(_att_today.get(r.subject, 0))),
        }
        for r in rows
    ]
    done = sum(1 for q in quizzes if q["status"] == "done")

    # '이번 주 연속 도전' — learning_attempts 실집계 (이번 주 시도 없으면 D 유지)
    today = date.today()
    ws = today - timedelta(days=today.weekday())
    week_rows = (
        db.query(LearningAttempt)
        .filter(
            LearningAttempt.student_id == me.id,
            LearningAttempt.created_at >= datetime.combine(ws, time.min),
        )
        .all()
    )
    # 연속 일수는 학생 홈/기록과 같은 정의(60일 이력에서 오늘·어제부터 과거로 역산)를 쓴다 —
    # 주간 스트립 안에서만 역산하면 주 경계에서 연속이 끊겨(금~월 연속 4일이 1일)
    # 화면 간 수치가 갈렸다
    hist_rows = (
        db.query(LearningAttempt)
        .filter(
            LearningAttempt.student_id == me.id,
            LearningAttempt.created_at >= datetime.combine(today - timedelta(days=60), time.min),
        )
        .all()
    )
    hist_days = {r.created_at.date() for r in hist_rows if r.created_at}
    if week_rows or hist_days:
        # 실데이터가 있으면 주간 스트립도 실데이터로 — 디자인 폴백(월~목 done)을 섞으면
        # "이번 주 4일 했는데 연속 0일" 같은 표시 모순이 생긴다
        days_done = {r.created_at.date().weekday() for r in week_rows if r.created_at}
        week = []
        for i, label in enumerate(["월", "화", "수", "목", "금", "토", "일"]):
            day: dict = {"label": label, "done": i in days_done}
            if i == today.weekday():
                day["today"] = True
            week.append(day)
        streak = aggregate._streak_days(hist_days)
    else:
        week = D.DAILY_QUIZ_WEEK
        streak = sum(1 for day in week if day.get("done"))

    return {
        "quizzes": quizzes,
        "done": done,
        "total": len(quizzes),
        "remain": len(quizzes) - done,
        "week": week,
        "streak_days": streak,
        "coins": me.coins,  # NAV 냥코인 칩
    }


# ---------------------------------------------------------------- 지갑/상점


















# ---------------------------------------------------------------- 학년 랭킹
# 상위 3위 보너스 코인 (하루 1회, 랭킹 확인 시 지급 — 순위 유지 동기)
RANK_TOP3_COINS = {1: 30, 2: 20, 3: 10}




# ---------------------------------------------------------------- 상장 · 개근 뱃지
ATTENDANCE_BADGE_NAME = "개근왕"
ATTENDANCE_STREAK_DAYS = 30  # 30일 연속 학습 = 개근상






# ---------------------------------------------------------------- 학습 시도 저장
@router.post("/learning/attempts")
def save_attempt(
    req: AttemptCreate,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    # 공개 자기신고 경로 — graded=False. 서버 채점(정답 검증) 없이 온 값이므로 오늘의퀴즈
    # done 승격·랭킹·코인·스티커 등 점수 부수효과를 주지 않는다(기록·행동데이터만 남긴다).
    # 서버 채점 경로(위젯 verify·game-answer)는 _apply_attempt(graded=True)로 호출한다.
    # (적대적 검토 0713 #4/#5 — 무채점 자기신고로 랭킹/스티커/코인 위조 차단.)
    return _apply_attempt(req, _me(principal), db, graded=False)


def _apply_attempt(
    req: AttemptCreate, me: StudentProfile, db: Session, graded: bool = False
) -> dict:
    if req.subject not in D.SUBJECT_ORDER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="알 수 없는 과목입니다.")
    attempt = LearningAttempt(
        organization_id=me.organization_id,
        student_id=me.id,
        subject=req.subject,
        chapter_no=req.chapter_no,
        content_id=req.content_id,
        result=req.result,
        score=req.score,
        solve_time_ms=req.solve_time_ms,
        retry_count=req.retry_count,
        estimated_reason=req.estimated_reason,
        graded=graded,
    )
    db.add(attempt)

    # 문제은행 SRS 상태 갱신(설계: question-bank-scale-design.md) — 서버 채점(graded)된
    # 은행 문항(content_id 있음) 응답만. 자기신고(graded=False)는 정답률과 같은 이유로 제외.
    # 여기(단일 채점 싱크)에 두면 위젯 verify·game-answer 등 모든 채점 경로가 자동 포함된다.
    if graded and req.content_id:
        from app.services import bank_mode

        bank_mode.record_answer(db, me.id, req.subject, req.content_id, req.result == "correct")

    # 행동 데이터(포인터 궤적 포함) — 아동용 캡차 판정 모델의 학습 재료.
    # require_student 경로라 student_id는 인증된 본인 것만 기록된다.
    if req.behavior:
        from app.services.captcha_service import record_behavior_event

        record_behavior_event(
            db,
            organization_id=me.organization_id,
            student_id=me.id,
            source_type="game",
            behavior=req.behavior,
            correct=req.result == "correct",
        )

    coins_earned = 0
    # 복습(replay)·문제은행(no_coin)은 보상 없음 — 코인은 오늘의퀴즈(습관) 축 전용.
    # graded(서버 채점)만 코인 지급 — 무채점 자기신고(/learning/attempts)로는 코인 없음.
    if graded and req.result == "correct" and not req.replay and not req.no_coin:
        # 학생 행 잠금(SELECT ... FOR UPDATE)으로 동시 적립을 직렬화 — earned_today를
        # 읽고 지급하는 사이에 다른 요청이 끼어들면 상한 경계에서 수 코인 오버슛이 났다.
        db.query(StudentProfile).filter(StudentProfile.id == me.id).with_for_update().first()
        # 파밍 방지: 하루 학습 보상 코인 총량 상한(자기신고 반복으로 무한 적립 차단).
        # 정식 서버 채점(정답 검증)은 교육 API 단계에서 대체.
        earned_today = (
            db.query(func.coalesce(func.sum(CoinTransaction.amount), 0))
            .filter(
                CoinTransaction.student_id == me.id,
                CoinTransaction.amount > 0,
                CoinTransaction.reason.like("%학습 보상"),
                func.date(CoinTransaction.created_at) == date.today(),
            )
            .scalar()
            or 0
        )
        # 상한 경계 오버슛/레이스 방지: 남은 상한만큼만 지급(0~10으로 클램프).
        coins_earned = max(0, min(10, DAILY_LEARNING_COIN_CAP - int(earned_today)))
        if coins_earned > 0:
            # 원자적 증가: stale read-modify-write(me.coins += ...)는 동시요청 시 lost update.
            db.query(StudentProfile).filter(StudentProfile.id == me.id).update(
                {StudentProfile.coins: StudentProfile.coins + coins_earned},
                synchronize_session=False,
            )
            db.add(
                CoinTransaction(
                    student_id=me.id, amount=coins_earned, reason=f"{req.subject} 학습 보상"
                )
            )

    # 진도 테이블 보강: 문제 수 누적 + 과목 정답률 재계산 (전체학습/진도 화면 반영)
    prog = (
        db.query(StudentProgress)
        .filter(StudentProgress.student_id == me.id, StudentProgress.subject == req.subject)
        .first()
    )
    if prog is None:
        prog = StudentProgress(
            organization_id=me.organization_id, student_id=me.id, subject=req.subject
        )
        db.add(prog)
    prog.questions_done = (prog.questions_done or 0) + 1
    # 전체/정답 수를 COUNT 두 번 대신 한 번의 집계로 조회 — 모든 문제풀이마다 도는 핫패스
    prev_total, prev_correct = (
        db.query(
            func.count(LearningAttempt.id),
            func.coalesce(
                func.sum(case((LearningAttempt.result == "correct", 1), else_=0)), 0
            ),
        )
        .filter(
            LearningAttempt.student_id == me.id,
            LearningAttempt.subject == req.subject,
            LearningAttempt.graded.is_(True),  # 자기신고로 정답률 부풀리기 차단(서버 채점만)
        )
        .one()
    )
    prev_total = int(prev_total or 0)
    prev_correct = int(prev_correct or 0)
    total = prev_total + 1
    correct = prev_correct + (1 if req.result == "correct" else 0)
    prog.accuracy = round(correct / total * 100, 1)

    # 완료 챕터 실계산: 과목 챕터를 순서대로 누적, questions_done이 채운 챕터 수만큼 done.
    # (기존엔 chapters_done을 seed에서만 기록해 학습해도 진도·챕터 잠금이 영구 고정되던 실버그 해소)
    _chapters = (
        db.query(Chapter)
        .filter(Chapter.subject == req.subject)
        .order_by(Chapter.order_no)
        .all()
    )
    if _chapters:
        _cum = 0
        _done_ch = 0
        for _ch in _chapters:
            _cum += _ch.total_questions or 1
            if (prog.questions_done or 0) >= _cum:
                _done_ch += 1
            else:
                break
        prog.chapters_done = _done_ch
        prog.current_chapter = min(len(_chapters), _done_ch + 1)

    # 일일 잠금 규칙: 오늘의퀴즈 상태는 '오늘' 것만 갱신 가능(미래 날짜 미리 완료 불가 —
    # quiz_date는 항상 서버의 오늘). 복습(replay)은 상태를 건드리지 않는다(전날 다시풀기는 기록만).
    sticker_awarded = False
    sticker_coins = 0
    quiz_bonus = 0
    if graded and not req.replay and req.daily:
        quiz = (
            db.query(DailyQuizStatus)
            .filter(
                DailyQuizStatus.student_id == me.id,
                DailyQuizStatus.quiz_date == date.today(),
                DailyQuizStatus.subject == req.subject,
            )
            .first()
        )
        if quiz is None:
            quiz = DailyQuizStatus(
                student_id=me.id, quiz_date=date.today(), subject=req.subject, status="progress"
            )
            db.add(quiz)
        # 랭킹·상장 위조 차단(적대적검토 #4): done 승격은 클라이언트 completed 자기신고가
        # 아니라 '서버가 오늘 이 과목을 graded(서버 채점)로 DAILY_QUIZ_TOTAL 문항 이상 풀었고
        # 이번 제출이 정답'일 때만. 오답으로는 done 불가. 이미 done이면 유지. 방금 add한
        # 시도를 카운트에 반영하기 위해 flush 후 집계한다.
        db.flush()
        graded_today = (
            db.query(func.count(LearningAttempt.id))
            .filter(
                LearningAttempt.student_id == me.id,
                LearningAttempt.subject == req.subject,
                func.date(LearningAttempt.created_at) == date.today(),
                LearningAttempt.chapter_no.is_(None),
                LearningAttempt.graded.is_(True),
            )
            .scalar()
            or 0
        )
        was_done = quiz.status == "done"
        completed_ok = req.result == "correct" and int(graded_today) >= DAILY_QUIZ_TOTAL
        quiz.status = "done" if completed_ok else ("progress" if quiz.status != "done" else "done")

        # 오늘의퀴즈 완료 보상: 화면에 광고된 과목별 reward_coins를 done '승격 순간' 1회 지급.
        # (기존엔 표시 전용이라 완료해도 안 들어왔다 — 광고와 실지급 불일치 해소.)
        # 전이 가드(was_done)로 하루 1회. 동시 요청은 위 학생 행 잠금(정답 경로)으로 직렬화됨.
        if completed_ok and not was_done:
            reward = quiz.reward_coins or next(
                (p["reward"] for p in D.DAILY_QUIZ if p["subject"] == req.subject), 0
            )
            if reward > 0:
                quiz_bonus = int(reward)
                db.query(StudentProfile).filter(StudentProfile.id == me.id).update(
                    {StudentProfile.coins: StudentProfile.coins + quiz_bonus},
                    synchronize_session=False,
                )
                db.add(
                    CoinTransaction(
                        student_id=me.id, amount=quiz_bonus,
                        reason=f"{req.subject} 오늘의퀴즈 완료 보상",
                    )
                )

        # 6과목 완주 스티커: 오늘 전 과목이 done이 되는 순간 스티커 + 코인을 함께 지급.
        # daily_rewards(UNIQUE) 멱등 — 하루 1회. reward_date=오늘이라 자정에 자동 초기화.
        # SAVEPOINT(begin_nested)로 중복 지급 충돌만 되돌린다(rollback이 시도 기록을 날리지 않게).
        if completed_ok:
            db.flush()  # 방금 done 승격을 카운트에 반영
            done_today = (
                db.query(func.count(DailyQuizStatus.id))
                .filter(
                    DailyQuizStatus.student_id == me.id,
                    DailyQuizStatus.quiz_date == date.today(),
                    DailyQuizStatus.status == "done",
                    DailyQuizStatus.subject.in_(D.SUBJECT_ORDER),
                )
                .scalar()
                or 0
            )
            if done_today >= len(D.SUBJECT_ORDER):
                from sqlalchemy.exc import IntegrityError

                from app.models import DailyReward

                try:
                    with db.begin_nested():
                        db.add(
                            DailyReward(
                                student_id=me.id, kind="all_subjects_sticker",
                                reward_date=date.today(), amount=ALL_SUBJECTS_STICKER_COINS,
                            )
                        )
                        db.flush()
                    sticker_awarded = True
                    sticker_coins = ALL_SUBJECTS_STICKER_COINS
                    db.query(StudentProfile).filter(StudentProfile.id == me.id).update(
                        {StudentProfile.coins: StudentProfile.coins + sticker_coins},
                        synchronize_session=False,
                    )
                    db.add(
                        CoinTransaction(
                            student_id=me.id, amount=sticker_coins, reason="6과목 완주 스티커 보너스"
                        )
                    )
                except IntegrityError:
                    pass  # 오늘 이미 지급 — 스킵(시도 기록은 SAVEPOINT 밖이라 안전)
    db.commit()
    db.refresh(me)  # 원자적 코인 증가 후 최신 잔액으로 응답
    return {
        "ok": True, "attempt_id": attempt.id, "coins_earned": coins_earned, "coins": me.coins,
        "sticker_awarded": sticker_awarded, "sticker_coins": sticker_coins,
        "quiz_bonus": quiz_bonus,  # 오늘의퀴즈 완료 보상(광고된 reward_coins) — 승격 순간만 >0
    }


# ---------------------------------------------------------------- 실전 게임 세션 (과목별 문제은행 — subject_banks)
class _GameAnswerReq(_GBaseModel):
    question_id: str
    subject: str = "생활"  # 문항이 속한 과목 — 뱅크 스코프 조회(타 과목 id 교차 제출 차단)
    option_id: str = ""  # single 제출
    option_ids: list[str] | None = None  # multi(복수선택) 제출 — 집합 비교 채점
    last: bool = False  # 세션의 마지막 문항 → 오늘의퀴즈 완료(또는 챕터 단계 완료) 처리
    replay: bool = False  # 복습 모드 — 상태·코인 반영 없음
    behavior: dict | None = None  # 문항 풀이 중 포인터 궤적 등 (save_attempt로 전달)
    # 전체학습 주간 챕터 플레이 — 지정 시 오늘의퀴즈(습관) 미갱신, last면 단계 커서 전진.
    chapter_no: int | None = None
    stage: int | None = None


@router.get("/students/me/curriculum")
def curriculum(
    subject: str = Query(default="생활"),
    back: int = Query(default=7, ge=0, le=30),
    forward: int = Query(default=5, ge=0, le=14),
    principal: Principal = Depends(require_student),
):
    """일일 교육과정 — 오늘 기준 지난날(복습 가능)·오늘(과제)·다음날(잠금, 주제만).

    현재 '생활'만 실커리큘럼(ms 안전 주제 순환). 그 외 과목은 available=false.
    """
    _me(principal)
    from app.services import curriculum as _cur

    if subject != "생활":
        return {"available": False, "subject": subject, "days": []}
    return {"available": True, **_cur.curriculum_window(subject, back, forward)}


@router.get("/students/me/curriculum/day")
def curriculum_day(
    subject: str = Query(default="생활"),
    day: int = Query(ge=1),
    principal: Principal = Depends(require_student),
):
    """특정 일차 상세. 미래 일차는 주제만(잠금), 오늘/지난날은 단계별 문항(정답 제거)."""
    _me(principal)
    from app.services import curriculum as _cur

    if subject != "생활":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="지원하지 않는 과목입니다.")
    return _cur.day_detail(subject, day)


@router.get("/students/me/game-session")
def game_session(
    subject: str = Query(default="생활"),
    day: int | None = Query(default=None, ge=1),
    count: int = Query(default=5, ge=1, le=25),
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """실제 플레이 가능한 문항 세트 발급 (정답 미포함 — 채점은 서버).

    day 지정 시(생활 전용 — 일차 커리큘럼): 그 일차의 playable 문항 (미래 일차는 잠금 → available=false).
    day 미지정: 과목 뱅크 전체에서 무작위. 수학·과학·사회는 뱅크가 작아 커리큘럼 없이 무작위만 지원.
    (레거시 — 현재 프론트 미사용. 그래도 강의 완주 잠금은 걸어 강의 문항이 새지 않게 한다.)
    """
    me = _me(principal)
    from app.services import bank_mode, subject_banks

    if subject not in subject_banks.LIVE_SUBJECTS:
        return {"available": False, "subject": subject, "questions": []}

    if day is not None and subject == "생활":
        from app.services import curriculum as _cur

        detail = _cur.day_detail(subject, day)
        if detail.get("locked"):
            return {"available": False, "locked": True, "subject": subject, "topic": detail["topic"], "questions": []}
        playable = detail.get("playable", [])
        return {
            "available": len(playable) > 0,
            "subject": subject,
            "day": day,
            "topic": detail["topic"],
            "is_replay": detail.get("is_replay", False),
            "questions": playable,
        }
    import random as _random

    # 강의 완주 잠금 적용 — 미완주 강의 유래 문항은 풀에서 빠진다
    pool = bank_mode.unlocked_pool(db, me, subject)
    picked = _random.sample(pool, min(count, len(pool)))
    return {"available": True, "subject": subject, "questions": [subject_banks.public_question(q) for q in picked]}


# ---------------------------------------------------------------- 전체학습 주간 챕터 (오늘의퀴즈와 분리)
@router.get("/students/me/chapters")
def chapters(
    subject: str | None = Query(default=None),
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """전체학습 주간 챕터 — 과목별 챕터 목록 + 5단계 진행 + 달력 잠금(월요일 해제).

    오늘의 퀴즈(매일 습관·연속도전)와 분리된 '학습(숙련도)' 축이다. 챕터는 문제은행을
    10문제(5단계×2)씩 자른 것이고, 잠금은 전체 공통 달력(chapters.ANCHOR_MONDAY) 기준이라
    모든 학생이 같은 주에 같은 챕터를 본다. 문제은행이 작은 과목은 채울 수 있는 만큼만 챕터 생성.
    """
    from app.services import chapters as _ch

    me = _me(principal)
    subjects = [subject] if subject in D.SUBJECT_ORDER else D.SUBJECT_ORDER
    # 챕터 이름은 실제 문제은행 topic으로 생성(_ch.chapter_title) — 옛 Chapter 테이블 고정명 폐기
    # 과목 정답률(숙련도) — 있으면 패널에 표시
    acc_by = {
        p.subject: p.accuracy
        for p in db.query(StudentProgress).filter(StudentProgress.student_id == me.id).all()
    }
    # 학생 단계 진행(이어하기 커서)
    prog_rows = (
        db.query(ChapterProgress)
        .filter(ChapterProgress.student_id == me.id, ChapterProgress.subject.in_(subjects))
        .all()
    )
    stages_by = {
        (p.subject, p.chapter_no): max(0, min(_ch.STAGES, p.stages_done)) for p in prog_rows
    }

    out = []
    for sub in subjects:
        mx = _ch.max_chapters(sub)
        unlocked = _ch.unlocked_count(sub)
        chs = []
        current = None  # 이어할 챕터 = 열린 것 중 미완료 최저
        for no in range(1, mx + 1):
            sd = stages_by.get((sub, no), 0)
            is_unlocked = no <= unlocked
            if is_unlocked and sd < _ch.STAGES and current is None:
                current = no
            chs.append(
                {
                    "no": no,
                    "name": _ch.chapter_title(sub, no),
                    "stages": _ch.STAGES,
                    "stages_done": sd,
                    "questions": _ch.CHAPTER_SIZE,
                    "unlocked": is_unlocked,
                }
            )
        for c in chs:
            if not c["unlocked"]:
                c["state"] = "locked"
            elif c["stages_done"] >= _ch.STAGES:
                c["state"] = "done"
            elif c["no"] == current:
                c["state"] = "current"
            else:
                c["state"] = "available"
        out.append(
            {
                "subject": sub,
                "meta": D.SUBJECT_META[sub],
                "available": mx > 0,  # 문제은행 없는 과목(국어)은 false → 프론트 준비중
                "max_chapters": mx,
                "unlocked_chapters": unlocked,
                "current_chapter": current or (unlocked if mx else 0),
                "accuracy": acc_by.get(sub, 0),
                "chapters": chs,
            }
        )
    if subject in D.SUBJECT_ORDER:
        return out[0]
    return {"subjects": out, "anchor_monday": str(_ch.ANCHOR_MONDAY)}


@router.get("/students/me/chapter-session")
def chapter_session(
    subject: str = Query(...),
    chapter: int = Query(..., ge=1),
    stage: int | None = Query(default=None, ge=1, le=5),
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """전체학습 챕터의 한 단계(2문항) 발급 — 정답 미포함, 채점은 game-answer.

    stage 미지정 시 이어하기: 그 챕터의 다음 미완료 단계를 낸다. 완료한 단계를 다시 지정하면
    복습(is_replay=true, 코인·진행 갱신 없음). 달력상 잠긴 챕터는 막는다.
    """
    from app.services import chapters as _ch
    from app.services import subject_banks

    me = _me(principal)
    if subject not in D.SUBJECT_ORDER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="알 수 없는 과목입니다.")
    if subject not in subject_banks.LIVE_SUBJECTS:
        return {"available": False, "subject": subject, "questions": []}
    mx = _ch.max_chapters(subject)
    if chapter > mx:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="없는 챕터입니다.")
    if chapter > _ch.unlocked_count(subject):
        return {
            "available": False,
            "locked": True,
            "subject": subject,
            "chapter": chapter,
            "questions": [],
        }
    cp = (
        db.query(ChapterProgress)
        .filter(
            ChapterProgress.student_id == me.id,
            ChapterProgress.subject == subject,
            ChapterProgress.chapter_no == chapter,
        )
        .first()
    )
    done = max(0, min(_ch.STAGES, cp.stages_done if cp else 0))
    use_stage = stage if stage is not None else min(_ch.STAGES, done + 1)
    qs = _ch.stage_questions(subject, chapter, use_stage)
    # 강의 완주 잠금 — 이 단계에 섞인 미완주 강의 유래 문항은 뺀다(공개형엔 lecture_id가 없어
    # 원본 dict로 판정). 챕터는 기존 은행이 대부분이라 이 필터가 실제로 도는 일은 드물다.
    from app.services import bank_mode

    completed = bank_mode.completed_lecture_ids(db, me.id)
    qs = [q for q in qs if bank_mode.is_unlocked(subject_banks.get_question(subject, q["id"]), completed)]
    return {
        "available": len(qs) > 0,
        "subject": subject,
        "chapter": chapter,
        "stage": use_stage,
        "stages": _ch.STAGES,
        "stages_done": done,
        "is_replay": use_stage <= done,  # 완료한 단계 다시풀기 → 커서/코인 갱신 없음
        "questions": qs,
    }


def _bump_chapter_stage(
    db: Session, me: StudentProfile, subject: str, chapter_no: int, stage: int
) -> int | None:
    """챕터 단계 완료 커서 전진 — 다음 단계를 마쳤을 때만 stages_done +1 (순차·위조 방지).

    이미 지난 단계(복습)나 건너뛴 단계는 커서를 움직이지 않는다. UNIQUE(student,subject,chapter)로
    중복행 없음, sequential 가드로 이중 완료는 멱등.
    """
    from app.services import chapters as _ch

    if chapter_no < 1 or chapter_no > _ch.max_chapters(subject):
        return None
    if chapter_no > _ch.unlocked_count(subject):
        return None
    if stage < 1 or stage > _ch.STAGES:
        return None
    cp = (
        db.query(ChapterProgress)
        .filter(
            ChapterProgress.student_id == me.id,
            ChapterProgress.subject == subject,
            ChapterProgress.chapter_no == chapter_no,
        )
        .first()
    )
    if cp is None:
        cp = ChapterProgress(
            student_id=me.id, subject=subject, chapter_no=chapter_no, stages_done=0
        )
        db.add(cp)
    if stage == cp.stages_done + 1:
        cp.stages_done = stage
    db.commit()
    return cp.stages_done


class _ChapterStageDoneReq(_GBaseModel):
    subject: str
    chapter: int
    stage: int


@router.post("/students/me/chapter-stage-complete")
def chapter_stage_complete(
    req: _ChapterStageDoneReq,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """전체학습 위젯 세션(한 단계=2문항) 완료 시 단계 커서 전진 — 위젯 채점(game-answer 아님)
    경로라 별도 호출. 순차 가드로 건너뛰기·위조 방지(_bump_chapter_stage)."""
    me = _me(principal)
    if req.subject not in D.SUBJECT_ORDER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="알 수 없는 과목입니다.")
    sd = _bump_chapter_stage(db, me, req.subject, req.chapter, req.stage)
    return {"ok": True, "stages_done": sd}


def _opt_texts(q: dict, ids: list[str]) -> str:
    """옵션 id 목록 → 사람이 읽을 답 텍스트 (text 비면 emoji 슬롯의 숫자/기호 사용)."""
    by_id = {o["id"]: o for o in q.get("options", [])}
    parts = []
    for oid in ids:
        o = by_id.get(oid)
        if o:
            parts.append(o.get("text") or o.get("emoji") or "")
    return ", ".join(p for p in parts if p)


def _correct_answer_text(q: dict) -> str:
    """유형 불문 '정답'을 사람이 읽을 텍스트로 — 오답노트에 전 문제 유형을 담기 위함.

    선택형(single/multi/drag/order/listen…)은 옵션 텍스트, 타이핑형은 정답 문자열,
    십자말은 정답 낱말, 정답이 좌표·경로·매핑이라 텍스트화가 어려운 유형
    (route/trace/swipe/sort/connect/memory 등)은 explain(개념)으로 대체한다.
    """
    t = q.get("type")
    a = q.get("answer")
    # 단일 선택형(answer=옵션 id)
    if isinstance(a, str) and t not in ("dictation", "type_in"):
        txt = _opt_texts(q, [a])
        if txt:
            return txt
    # 복수/순서형(answer=id 리스트)
    if isinstance(a, list):
        txt = _opt_texts(q, [str(x) for x in a])
        if txt:
            return txt
    # 타이핑형
    if t in ("dictation", "type_in") and isinstance(a, str):
        return a
    if t == "input":
        ans = q.get("answers")
        if isinstance(ans, list) and ans:
            return str(ans[0])
    # 십자말: answer={슬롯: 낱말} — 값이 실제 정답 낱말
    if t == "crossword" and isinstance(a, dict):
        vals = [str(v) for v in a.values() if v]
        if vals:
            return ", ".join(vals)
    # 그 외(route/trace/swipe/sort/connect/memory/puzzle…) → 개념 설명으로 복습 포인트 제공
    return q.get("explain") or q.get("hint") or "그림과 활동을 다시 살펴봐요"


def _student_answer_text(q: dict, student_answer) -> str:
    """학생이 낸 답을 텍스트로 — 조작형은 정확 재현이 어려워 빈 문자열(문제+정답+개념만 남김)."""
    t = q.get("type")
    if student_answer is None:
        return ""
    if t in ("dictation", "type_in", "input"):
        return str(student_answer)[:200]
    if isinstance(student_answer, str):
        return _opt_texts(q, [student_answer])  # 옵션 없으면 "" (조작형)
    if isinstance(student_answer, list):
        return _opt_texts(q, [str(x) for x in student_answer])
    return ""  # dict(연결/분류 제출) 등 — 재현 생략


def _mark_reviewed(db: Session, me: StudentProfile, q: dict) -> None:
    """정답으로 '2회' 다시 맞힌 문항의 미복습 오답노트를 복습완료(reviewed=True)로 승격.

    사용자 결정(2026-07-14): 한 번 운 좋게 맞힌 걸로 지우지 않고 2회 정답부터 승격(간격
    반복 학습). 현재 정답 시도는 아직 LearningAttempt에 저장되기 전이므로, 이 문항의 '이전'
    graded 정답이 1회 이상이면(=지금이 2번째) 승격한다. 오답노트는 프롬프트로 매칭한다."""
    prompt = q.get("prompt")
    qid = q.get("id")
    if not prompt:
        return
    prior_correct = 0
    if qid:
        prior_correct = (
            db.query(func.count(LearningAttempt.id))
            .filter(
                LearningAttempt.student_id == me.id,
                LearningAttempt.content_id == str(qid)[:80],
                LearningAttempt.result == "correct",
                LearningAttempt.graded.is_(True),
            )
            .scalar()
            or 0
        )
    if prior_correct < 1:
        return  # 첫 정답 — 아직 승격하지 않는다(2회 필요)
    db.query(WrongAnswer).filter(
        WrongAnswer.student_id == me.id,
        WrongAnswer.question == prompt,
        WrongAnswer.reviewed.is_(False),
    ).update({WrongAnswer.reviewed: True}, synchronize_session=False)


def _record_wrong(
    db: Session, me: StudentProfile, subject: str, q: dict, student_answer,
    chapter_no: int | None = None,
) -> None:
    """게임 오답을 오답노트(WrongAnswer)·취약추천(Recommendation)에 실기록.

    전 문제 유형 지원 — 정답은 _correct_answer_text(유형별 렌더), 학생 답은
    _student_answer_text. 같은 문항이 '미복습' 상태로 있으면 중복 저장하지 않는다.
    복습(replay) 오답은 호출부에서 제외한다. chapter_no는 전체학습 주차 챕터 오답이면 그
    챕터(≥1), 오늘의 퀴즈 오답이면 None — '약한 챕터 미복습 오답' 진단에 쓴다.
    """
    from app.services import subject_banks

    dup = (
        db.query(WrongAnswer)
        .filter(
            WrongAnswer.student_id == me.id,
            WrongAnswer.question == q["prompt"],
            WrongAnswer.reviewed.is_(False),
        )
        .first()
    )
    if dup is None:
        db.add(
            WrongAnswer(
                student_id=me.id,
                organization_id=me.organization_id,
                subject=subject,
                category=subject_banks.WRONG_CATEGORY.get(subject, "safe"),  # D.WRONG_TAGS 키
                question=q["prompt"],
                # '잘 모르겠어요'(무응답)는 빈칸 대신 명시 — 오답노트에서 구분되게
                my_answer=("잘 모르겠어요" if student_answer is None else _student_answer_text(q, student_answer))[:200],
                correct_answer=_correct_answer_text(q)[:200],
                tip=q.get("explain") or q.get("hint"),
                wrong_date=date.today(),
                chapter_no=chapter_no if (isinstance(chapter_no, int) and chapter_no >= 1) else None,
            )
        )

    # 취약 주제 추천 — 같은 과목·챕터(stage)에 active 추천이 없으면 생성
    stage = int(q.get("stage") or 1)
    rec_dup = (
        db.query(Recommendation)
        .filter(
            Recommendation.student_id == me.id,
            Recommendation.subject == subject,
            Recommendation.chapter_no == stage,
            Recommendation.status == "active",
        )
        .first()
    )
    if rec_dup is None:
        db.add(
            Recommendation(
                student_id=me.id,
                subject=subject,
                chapter_no=stage,
                priority="보통",
                reason=f"{q.get('topic') or subject} 문제에서 틀린 적이 있어요. 다시 한 번 풀어볼까요?",
            )
        )


@router.post("/students/me/game-answer")
def game_answer(
    req: _GameAnswerReq,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """문항 1개 서버 채점 + 학습기록 저장 — 자기신고가 아닌 서버 판정 결과를 기록한다.

    single: option_id 등호 비교 / multi(복수선택): option_ids 집합 비교(부분 정답 없음).
    문항은 요청 과목의 뱅크에서만 찾는다 — 타 과목 문항 id 교차 제출은 404.
    """
    me = _me(principal)
    from app.services import subject_banks

    q = subject_banks.get_question(req.subject, req.question_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="문항을 찾을 수 없습니다.")
    if not q["playable"]:
        # 위젯 전용(SVG·미지원) 문항 — 현재 게임 UI 채점 대상이 아님
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="플레이할 수 없는 문항입니다.")
    if q["type"] not in ("single", "multi"):
        # 조작형(connect/sort/order/place)은 위젯(교육형 챌린지)에서만 서버 채점한다.
        # game-answer는 옵션 등호·집합 채점 전용 — 매핑/순서 채점은 captcha verify 경로.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="위젯에서 풀 수 있는 문항이에요.")

    if q["type"] == "multi":
        answer_ids = [str(a) for a in (q["answer"] or [])]
        picked_ids = [str(x) for x in (req.option_ids or ([req.option_id] if req.option_id else []))]
        correct = len(picked_ids) > 0 and set(picked_ids) == set(answer_ids)
    else:
        answer_ids = [str(q["answer"])]
        picked_ids = [str(req.option_id)] if req.option_id else []
        correct = picked_ids == answer_ids

    # 오답이면 오답노트·취약추천에 실기록 / 정답이면 그 문항 미복습 오답노트를 복습완료로 승격.
    # (복습은 제외 — 반복 파밍/중복 누적 방지)
    if not req.replay:
        if not correct:
            student_ans = req.option_ids if q["type"] == "multi" else req.option_id
            _record_wrong(db, me, req.subject, q, student_ans, chapter_no=req.chapter_no)
        else:
            _mark_reviewed(db, me, q)

    # 서버 판정 결과를 학습기록으로 저장 (기존 save_attempt와 동일 부수효과: 코인 상한·진도·퀴즈 상태)
    # 챕터 플레이(chapter_no 지정)는 daily=False — 코인·정답률(숙련도)은 반영하되 오늘의퀴즈(습관) 미갱신.
    is_chapter = req.chapter_no is not None
    attempt_req = AttemptCreate(
        subject=req.subject,
        chapter_no=req.chapter_no,
        # 문항 id — 오답노트 2회 승격 카운트·bank_mode(안 푼/틀린/맞춘) 분류의 원천.
        # (기존엔 game-answer가 content_id를 안 넣어 이 문항이 '안 푼'으로 오분류됐다.)
        content_id=str(req.question_id)[:80] if req.question_id else None,
        result="correct" if correct else "incorrect",
        score=20 if correct else 0,  # 5문 기준 100점 만점
        completed=req.last and not req.replay,
        replay=req.replay,
        daily=not is_chapter,
        behavior=req.behavior,
    )
    # game-answer는 서버가 문항 정답을 검증한 경로 → graded=True(점수 부수효과 대상).
    saved = _apply_attempt(attempt_req, me, db, graded=True)
    # 챕터 단계 완료: 단계 마지막 문항(last)까지 풀면 stages_done 커서 전진(이어하기 저장).
    stages_done = None
    if is_chapter and req.stage is not None and req.last and not req.replay:
        stages_done = _bump_chapter_stage(db, me, req.subject, req.chapter_no, req.stage)
    return {
        "correct": correct,
        "answer_id": answer_ids[0] if answer_ids else "",
        "answer_ids": answer_ids,
        "answer_text": _opt_texts(q, answer_ids),
        # 해설(explain)은 채점 후에만 공개 — 발급 응답(public_question)에는 포함되지 않는다
        "hint": q.get("explain") or q["hint"],
        "coins_earned": saved.get("coins_earned", 0),
        "stages_done": stages_done,
        # 6과목 완주 스티커 — 이 문항 적립으로 지급된 순간을 프론트가 놓치지 않게(위젯 경로와 동일)
        "sticker_awarded": saved.get("sticker_awarded", False),
        "sticker_coins": saved.get("sticker_coins", 0),
    }


# ---------------------------------------------------------------- 학습결과 / 게임화면
@router.get("/students/me/result")
def result(
    subject: str = Query(default="국어"),
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    me = _me(principal)
    key = subject if subject in D.RESULT_SUBJECTS else "국어"
    # 오늘 해당 과목 시도 실집계(정답/오답/점수/시간/연속) — 없으면 D 프리셋
    res_agg = aggregate.student_result_today(db, me, key)
    s = {**D.RESULT_SUBJECTS[key], **(res_agg or {})}
    # 오늘의 학습 지도·다음 과목: '오늘의 퀴즈 현황' 단일 기준 = 오늘 5문항 푼 과목 ∪ 정답완주.
    # (홈·오늘의퀴즈 페이지와 같은 기준 — 예전엔 여기만 status=="done"이라, 5문 다 풀고 마지막이
    #  오답인 과목을 '다음 과목'으로 다시 내밀어 방금 푼 과목으로 역주행하던 버그가 있었다.)
    done_set = _quiz_done_set(db, me.id) | {key}
    # 오늘의 스티커(6과목 완주) — daily_rewards 장부 기준. 자정이 지나면 자동으로 미획득.
    from app.models import DailyReward

    sticker_today = (
        db.query(DailyReward)
        .filter(
            DailyReward.student_id == me.id,
            DailyReward.kind == "all_subjects_sticker",
            DailyReward.reward_date == date.today(),
        )
        .first()
        is not None
    )
    return {
        "subject": key,
        "nickname": me.nickname,
        "meta": D.SUBJECT_META[key],
        **s,
        # 세션 문항 수(마지막 세션 실집계). 시도 없으면 프리셋과 맞춰 5.
        "total": s.get("total", 5),
        "levels": D.RESULT_LEVELS,
        "today_done": sorted(done_set, key=D.SUBJECT_ORDER.index),
        "subject_order": D.SUBJECT_ORDER,
        "all_done_today": done_set >= set(D.SUBJECT_ORDER),
        "sticker_today": sticker_today,
        # 오늘 이 과목 시도가 없어 점수·정답 수치가 디자인(데모)값이면 demo=True
        "demo": not res_agg,
    }


@router.get("/students/me/chapter-history")
def chapter_history(
    subject: str = Query(...),
    chapter: int = Query(ge=1),
    before: str | None = Query(default=None),  # ISO 시각 — 이번 세션 시작 이전 기록만(비교용)
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """이 챕터의 지난 기록(정답률) — 결과 화면 '지난 기록 vs 이번' 비교용.

    before(이번 세션 시작 시각)를 주면 그 이전 시도만 집계해, 방금 푼 세션이
    '지난 기록'에 섞이지 않는다. 기록이 없으면 accuracy=null.
    """
    me = _me(principal)
    if subject not in D.SUBJECT_ORDER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="알 수 없는 과목입니다.")
    q = db.query(
        func.count(LearningAttempt.id),
        func.coalesce(func.sum(case((LearningAttempt.result == "correct", 1), else_=0)), 0),
    ).filter(
        LearningAttempt.student_id == me.id,
        LearningAttempt.subject == subject,
        LearningAttempt.chapter_no == chapter,
    )
    if before:
        try:
            dt = datetime.fromisoformat(before.replace("Z", "+00:00"))
            # created_at은 서버 로컬(naive)로 저장 — tz 포함 입력(UTC 등)은 로컬로 변환해 비교.
            # naive 비교로 두면 KST 오전 기록이 UTC 컷보다 '미래'가 돼 오늘 기록이 통째로 잘린다.
            cut = dt.astimezone().replace(tzinfo=None) if dt.tzinfo is not None else dt
            q = q.filter(LearningAttempt.created_at < cut)
        except ValueError:
            pass  # 형식 오류는 무시하고 전체 기록으로
    total, correct = q.one()
    total = int(total or 0)
    return {
        "subject": subject,
        "chapter": chapter,
        "total": total,
        "accuracy": round(int(correct or 0) / total * 100) if total else None,
    }


@router.get("/students/me/chapter-stats")
def chapter_stats(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
):
    """전체학습(숙련 축) 과목×챕터별 정답률 — 대시보드 그래프용.

    챕터 축(chapter_no≥1)만. 오늘의 퀴즈 정답률은 daily_quiz_accuracy로 분리 노출.
    실데이터 없어도 챕터 골격은 내려간다(정답률 null=미학습) — 가짜 진행 없음.
    """
    return {"subjects": aggregate.chapter_stats(db, _me(principal))}


@router.get("/students/me/habit-stats")
def habit_stats(
    weeks: int = Query(default=4, ge=1, le=12),
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """오늘의 퀴즈(습관 축) 일별 완료·정답률 + 연속일 — 대시보드 습관 추세용."""
    return aggregate.habit_series(db, _me(principal), weeks=weeks)


@router.get("/students/me/game-state")
def game_state(
    subject: str = Query(default="국어"),
    principal: Principal = Depends(require_student),
):
    key = subject if subject in D.GAME_SUBJECTS else "국어"
    return {
        "subject": key,
        "meta": D.SUBJECT_META[key],
        **D.GAME_SUBJECTS[key],
        "question": D.GAME_QUESTIONS[key],
        "reward": {"have": D.GAME_REWARDS[key], "goal": 5},
    }


# ---------------------------------------------------------------- 개념 읽음
def _find_chapter(db: Session, concept_id: str) -> Chapter | None:
    ch = db.get(Chapter, concept_id)
    if ch:
        return ch
    if "-" in concept_id:
        sub, _, num = concept_id.rpartition("-")
        if sub in D.SUBJECT_ORDER and num.isdigit():
            return (
                db.query(Chapter)
                .filter(Chapter.subject == sub, Chapter.order_no == int(num))
                .first()
            )
    return None


@router.post("/students/me/concepts/read")
def mark_concept_read(
    req: ConceptReadRequest,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    me = _me(principal)
    ch = _find_chapter(db, req.concept_id)
    if ch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="개념을 찾을 수 없습니다.")
    exists = (
        db.query(ConceptRead)
        .filter(ConceptRead.student_id == me.id, ConceptRead.chapter_id == ch.id)
        .first()
    )
    if exists is None:
        db.add(ConceptRead(student_id=me.id, chapter_id=ch.id))
        db.commit()
    return {"ok": True, "concept_id": f"{ch.subject}-{ch.order_no}"}


@router.get("/students/me/concepts/read")
def concept_reads(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
):
    me = _me(principal)
    rows = (
        db.query(ConceptRead, Chapter)
        .join(Chapter, Chapter.id == ConceptRead.chapter_id)
        .filter(ConceptRead.student_id == me.id)
        .all()
    )
    return [f"{ch.subject}-{ch.order_no}" for _, ch in rows]


# ---------------------------------------------------------------- 검색
@router.get("/contents/search")
def search_contents(
    q: str = Query(default=""),
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    qq = q.strip().lower()
    rows = db.query(Content).filter(Content.status == "active").order_by(Content.created_at).all()
    results = []
    for c in rows:
        desc, _, kw = (c.description or "").partition("\n")
        haystack = f"{c.title} {desc} {kw} {c.subject or ''}".lower()
        if qq and qq not in haystack:
            continue
        results.append(
            {
                "id": c.id,
                "title": c.title,
                "tag": c.category,
                "desc": desc,
                "icon": c.icon,
                "subject": c.subject,
                "href": c.route_hint,
                "meta": D.SUBJECT_META.get(c.subject or "", {}),
            }
        )
    return {"query": q, "count": len(results), "results": results}


# ---------------------------------------------------------------- 학생 비밀번호 변경 (강제 변경 포함)
from pydantic import BaseModel as _BaseModel  # noqa: E402


class _ChangePwReq(_BaseModel):
    new_password: str


@router.patch("/students/me/password")
def change_my_password(
    req: _ChangePwReq,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """학생 본인 비밀번호 변경 — 초기화(must_change_password) 후 첫 로그인 강제 변경에도 사용."""
    from app.core.security import hash_password

    if not req.new_password or len(req.new_password) < 4:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="비밀번호는 4자 이상이어야 해요.")
    me = _me(principal)
    me.password_hash = hash_password(req.new_password)
    me.must_change_password = False
    db.commit()
    return {"ok": True}
