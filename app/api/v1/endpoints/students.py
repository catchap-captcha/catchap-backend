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


def _my_grade(db: Session, me: StudentProfile) -> int | None:
    """학생의 학년 — 소속 반(classes.grade) 기준. 무반이면 None."""
    if not me.class_id:
        return None
    cls = db.get(ClassRoom, me.class_id)
    return cls.grade if cls else None


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


def _speed_points(avg_ms: float) -> int:
    """정답 평균 풀이시간 → 속도 점수(0~RANK_SPEED_MAX). 빠를수록 높음."""
    if avg_ms <= 0:
        return 0
    if avg_ms <= RANK_SPEED_FAST_MS:
        return RANK_SPEED_MAX
    if avg_ms >= RANK_SPEED_SLOW_MS:
        return 0
    span = RANK_SPEED_SLOW_MS - RANK_SPEED_FAST_MS
    return round(RANK_SPEED_MAX * (RANK_SPEED_SLOW_MS - avg_ms) / span)


def _grade_scores(db: Session, student_ids: list[str]) -> dict[str, int]:
    """학생별 랭킹 점수 = 정답률·풀이속도(완료 과목·일) + 6과목 완주 보너스 + 연속 완주 보너스."""
    if not student_ids:
        return {}
    full = len(D.SUBJECT_ORDER)  # 전 과목 수 (6)

    # 1) 완료(done) 과목 집합 — 일자별. 점수 부여 대상 + 완주/연속 보너스 판정 근거.
    done_rows = (
        db.query(DailyQuizStatus.student_id, DailyQuizStatus.quiz_date, DailyQuizStatus.subject)
        .filter(DailyQuizStatus.student_id.in_(student_ids), DailyQuizStatus.status == "done")
        .all()
    )
    done: dict[str, dict[date, set[str]]] = {}
    for sid, day, subj in done_rows:
        done.setdefault(sid, {}).setdefault(day, set()).add(subj)

    # 2) 과목·일별 정답률/정답 평균 풀이시간 (learning_attempts 실집계).
    #    (sid, day, subject) → [시도수, 정답수, 정답 풀이시간 합].
    att_rows = (
        db.query(
            LearningAttempt.student_id,
            func.date(LearningAttempt.created_at),
            LearningAttempt.subject,
            LearningAttempt.result,
            LearningAttempt.solve_time_ms,
        )
        .filter(LearningAttempt.student_id.in_(student_ids))
        .all()
    )
    stat: dict[tuple[str, date, str], list[int]] = {}
    for sid, day, subj, result, solve_ms in att_rows:
        d = day if isinstance(day, date) else date.fromisoformat(str(day)[:10])
        s = stat.setdefault((sid, d, subj), [0, 0, 0])
        s[0] += 1
        if result == "correct":
            s[1] += 1
            s[2] += int(solve_ms or 0)

    scores: dict[str, int] = {}
    for sid, day_map in done.items():
        total = 0
        all6: set[date] = set()
        for day, subs in day_map.items():
            for subj in subs:
                s = stat.get((sid, day, subj))
                if s and s[0] > 0:
                    acc_pts = round(s[1] / s[0] * RANK_ACC_MAX)
                    avg_ms = (s[2] / s[1]) if s[1] else 0
                    total += acc_pts + _speed_points(avg_ms)
                # 시도 기록 없이 완료만 있으면 정답률·속도 0점 — 완주/연속 보너스로만 반영.
            if len(subs) >= full:
                total += RANK_FULLDAY_BONUS
                all6.add(day)
        # 연속 완주 보너스: 6과목 완주일의 '전날'도 6과목 완주면 그 날마다 +STEP.
        total += RANK_STREAK_STEP * sum(1 for d in all6 if (d - timedelta(days=1)) in all6)
        scores[sid] = total
    return scores


def _class_board(db: Session, me: StudentProfile) -> list[dict]:
    """같은 학년 학생들의 랭킹 (학년별로만 합산 — 반이 달라도 같은 학년이면 함께 경쟁).

    개인정보 보호: 타 학생은 닉네임만 노출한다 (실명 절대 금지).
    """
    grade = _my_grade(db, me)
    if grade is not None:
        peers = (
            db.query(StudentProfile)
            .join(ClassRoom, StudentProfile.class_id == ClassRoom.id)
            .filter(
                StudentProfile.organization_id == me.organization_id,
                StudentProfile.status != "disabled",
                ClassRoom.grade == grade,
            )
            .all()
        )
    else:
        peers = [me]  # 무반 학생은 학년 풀 없음 — 본인만
    if all(s.id != me.id for s in peers):
        peers.append(me)
    scores = _grade_scores(db, [s.id for s in peers])
    ranked = sorted(
        (
            {"name": s.nickname, "score": scores.get(s.id, 0), "me": s.id == me.id}
            for s in peers
        ),
        key=lambda r: (-r["score"], r["name"]),
    )
    return [
        {"rank": i + 1, "name": r["name"], "score": r["score"], "me": r["me"]}
        for i, r in enumerate(ranked)
    ]


# ---------------------------------------------------------------- 학습 홈
@router.get("/students/me/dashboard")
def dashboard(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
):
    me = _me(principal)
    quiz = _today_quiz_rows(db, me.id)
    today_total = len(quiz)
    today_done = sum(1 for q in quiz if q.status == "done")
    earned = (
        db.query(StudentBadge)
        .filter(StudentBadge.student_id == me.id, StudentBadge.earned_at.isnot(None))
        .count()
    )
    # 과목 카드: 오늘 상태(daily_quiz_status) + 오늘 학습 시도 수 (실데이터)
    quiz_by_subject = {q.subject: q for q in quiz}
    today_start = datetime.combine(date.today(), time.min)
    # 오늘의퀴즈(습관) 진행바 — 전체학습 주간 챕터 플레이(chapter_no 있음)는 제외해 분리 유지.
    attempts_today = dict(
        db.query(LearningAttempt.subject, func.count(LearningAttempt.id))
        .filter(
            LearningAttempt.student_id == me.id,
            LearningAttempt.created_at >= today_start,
            LearningAttempt.chapter_no.is_(None),
        )
        .group_by(LearningAttempt.subject)
        .all()
    )
    subjects = []
    for card in D.HOME_SUBJECT_CARDS:
        sub = card["subject"]
        q = quiz_by_subject.get(sub)
        state = {"done": "done", "progress": "progress", "doing": "progress"}.get(
            q.status if q else "todo", "todo"
        )
        total = card["total"]
        done = total if state == "done" else min(total, int(attempts_today.get(sub, 0)))
        if state == "todo" and done > 0:
            state = "progress"
        subjects.append(
            {**card, "done": done, "state": state, "meta": D.SUBJECT_META[sub]}
        )
    # 학년 랭킹 밴드: 같은 학년 실데이터 기준 (일일 과제 완료 점수, 학기 누적)
    board = _class_board(db, me)
    my_rank = next(r["rank"] for r in board if r["me"])
    band = f"상위 {max(1, round(my_rank / len(board) * 100))}%"
    growth = aggregate.student_growth(db, me)  # 시도 없으면 None → 성장 그래프 데모
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
        "badges": {"earned": earned, "total": db.query(Badge).count()},
        "class_rank": {"band": band, "note": D.HOME_CLASS_RANK_NOTE},
        "ai_comment": D.HOME_AI_COMMENT,
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


# ---------------------------------------------------------------- 배지
def _earned_foot(earned_at: datetime | date) -> str:
    """획득일 → 화면 하단 라벨 (student_badges.earned_at 실데이터 기준)."""
    d = earned_at.date() if isinstance(earned_at, datetime) else earned_at
    days = (date.today() - d).days
    if days <= 0:
        return "오늘 획득"
    if days == 1:
        return "어제 획득"
    return f"{d.month}월 {d.day}일 획득"


@router.get("/students/me/badges")
def badges(principal: Principal = Depends(require_student), db: Session = Depends(get_db)):
    me = _me(principal)
    all_badges = db.query(Badge).order_by(Badge.order_no).all()
    mine = {
        sb.badge_id: sb
        for sb in db.query(StudentBadge).filter(StudentBadge.student_id == me.id).all()
    }
    # 연속 학습 배지(불꽃 학습왕)는 홈/기록과 같은 실 streak으로 —
    # 디자인 상수 "12/14일"이 남아 화면 간 연속 일수가 갈리던 원인
    streak_rows = (
        db.query(LearningAttempt)
        .filter(
            LearningAttempt.student_id == me.id,
            LearningAttempt.created_at
            >= datetime.combine(date.today() - timedelta(days=60), time.min),
        )
        .all()
    )
    live_streak = aggregate._streak_days({r.created_at.date() for r in streak_rows if r.created_at})
    out = []
    for b in all_badges:
        sb = mine.get(b.id)
        earned = bool(sb and sb.earned_at)
        progress = sb.progress if sb else 0.0
        if earned:
            foot = _earned_foot(sb.earned_at)
        elif b.name == "불꽃 학습왕":
            foot = f"{min(live_streak, 14)}/14일"
            progress = round(min(live_streak / 14, 1.0), 3)
        else:
            # 도전 중 문구는 디자인 카피 유지 (단, 디자인이 '획득'으로 표기한 항목은 제외)
            state = D.BADGE_STATE.get(b.name, {})
            foot = state.get("foot", "도전 중") if not state.get("earned") else "도전 중"
        out.append(
            {
                "id": b.id,
                "name": b.name,
                "desc": b.description,
                "icon": b.icon,
                "color": b.color,
                "earned": earned,
                "locked": not earned,
                "progress": progress,
                "foot": foot,
            }
        )
    earned_count = sum(1 for b in out if b["earned"])

    # 히어로 쇼케이스: 가장 최근 획득 배지 (student_badges.earned_at 실데이터, 문구는 D)
    recent = None
    latest: tuple | None = None
    for b in all_badges:
        sb = mine.get(b.id)
        if sb and sb.earned_at and (latest is None or sb.earned_at > latest[0].earned_at):
            latest = (sb, b)
    if latest:
        sb, b = latest
        hero = D.BADGE_HERO.get(b.name, {})
        recent = {
            "name": b.name,
            "icon": b.icon,
            "color": b.color,
            "title": hero.get("title", b.name),
            "desc": hero.get("desc", b.description),
            "foot": _earned_foot(sb.earned_at),
        }

    # '다음 배지' 진행 카드: 미획득 중 progress 최고 배지
    next_badge = None
    best: tuple | None = None
    for item, b in zip(out, all_badges):
        if item["earned"]:
            continue
        prog = float(item["progress"] or 0.0)
        if best is None or prog > best[0]:
            best = (prog, item, b)
    if best:
        prog, item, b = best
        cur = total = None
        unit = ""
        m = re.match(r"\s*(\d+)\s*/\s*(\d+)\s*(\S*)", str(item["foot"] or ""))
        if m:
            cur, total, unit = int(m.group(1)), int(m.group(2)), m.group(3)
            if total:
                prog = cur / total
        next_badge = {
            "name": b.name,
            "desc": b.description,
            "icon": b.icon,
            "color": b.color,
            "progress": round(prog, 3),
            "foot": item["foot"],
            "chip": D.BADGE_NEXT_CHIP,
            "current": cur,
            "total": total,
            "unit": unit,
            "remain": f"{total - cur}{unit}" if total is not None and cur is not None else None,
        }

    return {
        "badges": out,
        "earned": earned_count,
        "locked": len(out) - earned_count,
        "level": me.level,
        "recent": recent,
        "next": next_badge,
    }


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
    quizzes = [
        {
            "id": r.id,
            "subject": r.subject,
            "topic": r.topic,
            "status": r.status,
            "reward": r.reward_coins,
            "meta": D.SUBJECT_META.get(r.subject, {}),
            # 5단계 진행 바: 완료면 5/5, 아니면 오늘 시도 수(최대 5)
            "stages": STAGES,
            "stage_done": STAGES if r.status == "done" else min(STAGES, int(_att_today.get(r.subject, 0))),
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
def _catalog_rows(db: Session) -> list[ShopItem]:
    return db.query(ShopItem).order_by(ShopItem.category, ShopItem.order_no).all()


def _design_meta(item: ShopItem) -> dict:
    for entry in D.SHOP_CATALOG.get(item.category, []):
        if entry["name"] == item.name:
            return entry
    return {}


@router.get("/students/me/wallet")
def wallet(principal: Principal = Depends(require_student), db: Session = Depends(get_db)):
    me = _me(principal)
    owned_rows = db.query(StudentItem).filter(StudentItem.student_id == me.id).all()
    owned_ids = [r.item_id for r in owned_rows]
    items = {i.id: i for i in _catalog_rows(db)}
    owned_keys: dict[str, list[str]] = {"hat": [], "bg": [], "sticker": []}
    for item_id in owned_ids:
        item = items.get(item_id)
        if item is None:
            continue
        meta = _design_meta(item)
        if meta:
            owned_keys.setdefault(item.category, []).append(meta["key"])
    return {
        "coins": me.coins,
        "items": owned_ids,
        "owned": owned_keys,
        "avatar": me.avatar or {},
        "nickname": me.nickname,
        "age": me.age,
        "student_code": me.student_code,
        "level": me.level,
        # 마이페이지 '주간 활동 요약' — 실집계 (데이터 없으면 null → 프론트 fallback)
        "week_summary": _week_summary(db, me),
        # '함께한 지 N일' — student_profiles.created_at 실데이터
        "days_together": (
            max(1, (date.today() - me.created_at.date()).days + 1) if me.created_at else None
        ),
        # 주간 목표 — 이번 주 학습일 실집계 (없으면 D)
        "week_goal": _week_goal(db, me),
    }


def _week_goal(db: Session, me) -> dict:
    g = dict(D.PROFILE_WEEK_GOAL)
    total = int(g.get("total", 5))
    ws = date.today() - timedelta(days=date.today().weekday())
    rows = (
        db.query(LearningAttempt)
        .filter(
            LearningAttempt.student_id == me.id,
            LearningAttempt.created_at >= datetime.combine(ws, time.min),
        )
        .all()
    )
    days = {r.created_at.date() for r in rows if r.created_at}
    done = len(days) if rows else int(g.get("done", 0))
    remain = max(0, total - done)
    if remain == 0:
        hint = g.get("hint_done", "")
    elif remain == 1:
        hint = g.get("hint_one", "")
    else:
        hint = str(g.get("hint_many", "")).replace("{n}", str(remain))
    return {"done": min(done, total), "total": total, "hint": hint}


def _week_summary(db: Session, me) -> dict | None:
    """이번 주: 연속 학습일 / 푼 문제 / 모은 냥코인 / 완료한 놀이(과목×날짜 세션 수)"""
    from datetime import date, datetime, time, timedelta

    from app.models import CoinTransaction, LearningAttempt
    from app.services import aggregate as agg

    growth = agg.student_growth(db, me)
    if growth is None:
        return None

    week_start = datetime.combine(
        date.today() - timedelta(days=date.today().weekday()), time.min
    )
    coins_earned = sum(
        t.amount
        for t in db.query(CoinTransaction)
        .filter(
            CoinTransaction.student_id == me.id,
            CoinTransaction.amount > 0,
            CoinTransaction.created_at >= week_start,
        )
        .all()
    )
    week_rows = (
        db.query(LearningAttempt)
        .filter(LearningAttempt.student_id == me.id, LearningAttempt.created_at >= week_start)
        .all()
    )
    games_done = len(
        {(r.created_at.date(), r.subject) for r in week_rows if r.created_at and r.subject}
    )
    return {
        "streak_days": growth.get("streak_days", 0),
        "solved": growth.get("week_solved", 0),
        "coins_earned": coins_earned,
        "games_done": games_done,
    }


@router.get("/shop/catalog")
def shop_catalog(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
):
    out: dict[str, list[dict]] = {"hat": [], "bg": [], "sticker": []}
    for item in _catalog_rows(db):
        meta = _design_meta(item)
        out.setdefault(item.category, []).append(
            {
                "id": item.id,
                "key": meta.get("key", item.id),
                "category": item.category,
                "name": item.name,
                "icon": item.icon,
                "price": item.price,
                "color": meta.get("color"),
                "css": meta.get("css"),
            }
        )
    return out


@router.post("/students/me/shop/purchase")
def purchase(
    req: PurchaseRequest,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    me = _me(principal)
    item = db.get(ShopItem, req.item_id)
    if item is None:
        # 디자인 키('crown' 등)로도 조회 허용
        for row in _catalog_rows(db):
            if _design_meta(row).get("key") == req.item_id:
                item = row
                break
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="아이템을 찾을 수 없습니다.")
    exists = (
        db.query(StudentItem)
        .filter(StudentItem.student_id == me.id, StudentItem.item_id == item.id)
        .first()
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 보유한 아이템입니다.")
    from sqlalchemy.exc import IntegrityError

    # 소유 레코드를 UNIQUE(student_id,item_id)로 먼저 확보 → 동시 구매 race를 원자적으로 차단
    # (차감 뒤 중복 삽입이 실패하면 코인만 빠지는 사태 방지 — 확보 성공 후에만 차감).
    db.add(StudentItem(student_id=me.id, item_id=item.id))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 보유한 아이템입니다.")
    # 원자적 차감: 동시 구매 요청이 잔액 검사를 함께 통과해 코인이 음수가 되는 것을 방지
    if item.price > 0:
        updated = (
            db.query(StudentProfile)
            .filter(StudentProfile.id == me.id, StudentProfile.coins >= item.price)
            .update(
                {StudentProfile.coins: StudentProfile.coins - item.price},
                synchronize_session=False,
            )
        )
        if not updated:
            db.rollback()  # 잔액 부족 → 방금 확보한 소유 레코드도 함께 되돌림
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="냥코인이 부족해요.")
        db.add(CoinTransaction(student_id=me.id, amount=-item.price, reason=f"{item.name} 구매"))
    db.commit()
    db.refresh(me)
    return {"ok": True, "coins": me.coins, "item_id": item.id}


@router.put("/students/me/avatar")
def save_avatar(
    req: AvatarRequest,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    me = _me(principal)
    # 허용 키/문자열 값만 저장 — 임의·거대 JSON 저장(스토리지 남용) 차단
    allowed = ("hat", "background", "sticker", "face", "outfit")
    clean: dict[str, str] = {}
    for k, v in (req.avatar or {}).items():
        if k in allowed and isinstance(v, str) and len(v) <= 50:
            clean[k] = v
    me.avatar = clean
    db.commit()
    return {"ok": True, "avatar": me.avatar}


@router.patch("/students/me/profile")
def update_profile(
    req: StudentProfileUpdate,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    me = _me(principal)
    if req.nickname is not None and req.nickname.strip():
        me.nickname = req.nickname.strip()[:8]
    if req.age is not None:
        me.age = req.age
    db.commit()
    return {"ok": True, "nickname": me.nickname, "age": me.age, "gender": me.gender}


# ---------------------------------------------------------------- 학년 랭킹
# 상위 3위 보너스 코인 (하루 1회, 랭킹 확인 시 지급 — 순위 유지 동기)
RANK_TOP3_COINS = {1: 30, 2: 20, 3: 10}


@router.get("/students/me/class-ranking")
def class_ranking(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
):
    me = _me(principal)
    rows = _class_board(db, me)
    mine = next(r for r in rows if r["me"])
    top_score = rows[0]["score"] or 1
    grade = _my_grade(db, me)

    # 상위 3위 추가 코인: 오늘 아직 안 받았으면 지급 (학기 누적 랭킹이라 '매일 유지' 보상)
    # 멱등 지급: daily_rewards(UNIQUE student_id,kind,reward_date)에 INSERT 성공 시에만 지급.
    # SELECT-then-INSERT 였던 과거엔 동시요청이 둘 다 통과해 이중 지급됐다.
    bonus = 0
    if mine["rank"] in RANK_TOP3_COINS and mine["score"] > 0:
        from sqlalchemy.exc import IntegrityError

        from app.models import DailyReward

        amount = RANK_TOP3_COINS[mine["rank"]]
        db.add(DailyReward(student_id=me.id, kind="rank_bonus", reward_date=date.today(), amount=amount))
        try:
            db.flush()  # 오늘치 최초 지급이면 통과, 중복이면 IntegrityError
            granted = True
        except IntegrityError:
            db.rollback()
            granted = False
        if granted:
            bonus = amount
            db.query(StudentProfile).filter(StudentProfile.id == me.id).update(
                {StudentProfile.coins: StudentProfile.coins + bonus},
                synchronize_session=False,
            )
            db.add(
                CoinTransaction(
                    student_id=me.id, amount=bonus, reason=f"{mine['rank']}위 랭킹 보상"
                )
            )
            db.commit()

    return {
        "rank": mine["rank"],
        "score": mine["score"],
        "grade": grade,
        "class_size": len(rows),  # (호환) 랭킹 풀 크기 = 같은 학년 인원
        "board": rows[:20],  # 상위 20명까지만 노출
        "top_pct": round(mine["score"] / top_score * 100),
        "bonus_coins": bonus,  # 방금 지급된 상위 3위 보너스 (0이면 없음)
    }


# ---------------------------------------------------------------- 상장 · 개근 뱃지
ATTENDANCE_BADGE_NAME = "개근왕"
ATTENDANCE_STREAK_DAYS = 30  # 30일 연속 학습 = 개근상


def _semester_label(d: date) -> str:
    # 한국 학기: 3~8월 = 1학기, 9~2월 = 2학기(연도는 학기 시작 연도)
    if 3 <= d.month <= 8:
        return f"{d.year}년 1학기"
    year = d.year if d.month >= 9 else d.year - 1
    return f"{year}년 2학기"


@router.get("/students/me/awards")
def my_awards(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
):
    """상장(다운로드용) 목록 — 학년 랭킹 상위 3위 + 개근상. 개근 뱃지는 여기서 자동 지급."""
    me = _me(principal)
    awards: list[dict] = []
    today = date.today()
    semester = _semester_label(today)

    # 학년 랭킹 상장 (학기 누적 상위 3위)
    board = _class_board(db, me)
    mine = next(r for r in board if r["me"])
    grade = _my_grade(db, me)
    if grade is not None and mine["rank"] in (1, 2, 3) and mine["score"] > 0:
        awards.append(
            {
                "type": "rank",
                "title": f"{grade}학년 랭킹 {mine['rank']}위",
                "detail": f"{semester} · {grade}학년 {len(board)}명 중 {mine['rank']}위 · {mine['score']}점",
                "rank": mine["rank"],
                "grade": grade,
                "semester": semester,
            }
        )

    # 개근상 — 연속 학습 30일 이상이면 상장 + '개근왕' 뱃지 자동 지급
    growth = aggregate.student_growth(db, me) or {}
    streak = int(growth.get("streak_days") or 0)
    if streak >= ATTENDANCE_STREAK_DAYS:
        awards.append(
            {
                "type": "attendance",
                "title": "개근상",
                "detail": f"{semester} · {streak}일 연속으로 하루도 빠짐없이 학습했어요",
                "streak_days": streak,
                "semester": semester,
            }
        )
        from sqlalchemy.exc import IntegrityError

        # 배지 find-or-create — badges.name UNIQUE로 동시요청 중복 배지 생성 차단
        badge = db.query(Badge).filter(Badge.name == ATTENDANCE_BADGE_NAME).first()
        if badge is None:
            badge = Badge(
                name=ATTENDANCE_BADGE_NAME,
                description=f"{ATTENDANCE_STREAK_DAYS}일 연속 학습 개근",
                icon="ph-fill ph-calendar-check",
                color="#17B08C",
                condition_text=f"{ATTENDANCE_STREAK_DAYS}일 연속 학습하기",
                order_no=99,
            )
            db.add(badge)
            try:
                db.flush()
            except IntegrityError:  # 경쟁 요청이 먼저 만듦 → 재조회
                db.rollback()
                badge = db.query(Badge).filter(Badge.name == ATTENDANCE_BADGE_NAME).first()
        earned = (
            db.query(StudentBadge)
            .filter(StudentBadge.student_id == me.id, StudentBadge.badge_id == badge.id)
            .first()
        )
        if earned is None:
            # student_badges(student_id,badge_id) UNIQUE로 동시요청 이중지급 차단
            db.add(
                StudentBadge(
                    student_id=me.id, badge_id=badge.id, earned_at=datetime.now(), progress=1
                )
            )
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
        elif earned.earned_at is None:
            earned.earned_at = datetime.now()
            earned.progress = 1
            db.commit()

    return {
        "nickname": me.nickname,
        "grade": grade,
        "semester": semester,
        "streak_days": streak,
        "attendance_target": ATTENDANCE_STREAK_DAYS,
        "awards": awards,
    }


# ---------------------------------------------------------------- 학습 시도 저장
@router.post("/learning/attempts")
def save_attempt(
    req: AttemptCreate,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    me = _me(principal)
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
    )
    db.add(attempt)

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
    # 복습(replay: 전날 다시풀기·오늘 재도전)은 보상 없음 — 반복 파밍 차단
    if req.result == "correct" and not req.replay:
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
        .filter(LearningAttempt.student_id == me.id, LearningAttempt.subject == req.subject)
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
    if not req.replay and req.daily:
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
        # 랭킹·상장 위조 차단: done 승격은 '완료 신고 + 서버/제출이 정답'일 때만.
        # 오답으로는 done이 될 수 없다(오답 반복으로 랭킹 만점 방지). 이미 done이면 유지.
        completed_ok = req.completed and req.result == "correct"
        quiz.status = "done" if completed_ok else ("progress" if quiz.status != "done" else "done")

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
):
    """실제 플레이 가능한 문항 세트 발급 (정답 미포함 — 채점은 서버).

    day 지정 시(생활 전용 — 일차 커리큘럼): 그 일차의 playable 문항 (미래 일차는 잠금 → available=false).
    day 미지정: 과목 뱅크 전체에서 무작위. 수학·과학·사회는 뱅크가 작아 커리큘럼 없이 무작위만 지원.
    """
    _me(principal)
    from app.services import subject_banks

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

    pool = subject_banks.playable_pool(subject)
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


def _record_wrong(db: Session, me: StudentProfile, subject: str, q: dict, picked_ids: list[str], answer_ids: list[str]) -> None:
    """게임 오답을 오답노트(WrongAnswer)·취약추천(Recommendation)에 실기록.

    같은 문항이 이미 '미복습' 상태로 있으면 중복 저장하지 않는다(반복 오답 누적 방지).
    복습(replay) 오답은 호출부에서 제외한다.
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
                my_answer=_opt_texts(q, picked_ids)[:200],
                correct_answer=_opt_texts(q, answer_ids)[:200],
                tip=q.get("explain") or q.get("hint"),
                wrong_date=date.today(),
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

    # 오답이면 오답노트·취약추천에 실기록 (복습은 제외 — 반복 파밍/중복 누적 방지)
    if not correct and not req.replay:
        _record_wrong(db, me, req.subject, q, picked_ids, answer_ids)

    # 서버 판정 결과를 학습기록으로 저장 (기존 save_attempt와 동일 부수효과: 코인 상한·진도·퀴즈 상태)
    # 챕터 플레이(chapter_no 지정)는 daily=False — 코인·정답률(숙련도)은 반영하되 오늘의퀴즈(습관) 미갱신.
    is_chapter = req.chapter_no is not None
    attempt_req = AttemptCreate(
        subject=req.subject,
        chapter_no=req.chapter_no,
        result="correct" if correct else "incorrect",
        score=20 if correct else 0,  # 5문 기준 100점 만점
        completed=req.last and not req.replay,
        replay=req.replay,
        daily=not is_chapter,
        behavior=req.behavior,
    )
    saved = save_attempt(attempt_req, principal, db)
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
    # 오늘 완료 과목: daily_quiz_status 실데이터 기준
    done_today = {
        r.subject
        for r in db.query(DailyQuizStatus).filter(
            DailyQuizStatus.student_id == me.id,
            DailyQuizStatus.quiz_date == date.today(),
            DailyQuizStatus.status == "done",
        ).all()
    }
    done_set = (done_today or set()) | {key}
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
