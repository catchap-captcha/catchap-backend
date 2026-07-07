"""학생 API — 본인 데이터만 (require_student)."""

import re
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel as _GBaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.permissions import Principal, require_student
from app.db.session import get_db
from app.models import (
    Badge,
    Chapter,
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
    if not rows:
        for preset in D.DAILY_QUIZ:
            rows.append(
                DailyQuizStatus(
                    student_id=student_id,
                    quiz_date=today,
                    subject=preset["subject"],
                    topic=preset["topic"],
                    status="todo",
                    reward_coins=preset["reward"],
                )
            )
        db.add_all(rows)
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


# 랭킹 점수 산식(사용자 결정 2026-07-07): "일일 과제 완료" 기반 · 학년별로만 합산 · 학기 누적(리셋 없음).
# - 과목 완료 1개당 10점, 하루 전 과목(6과목) 완료 보너스 +40 → 하루 최대 100점
# - 문제 수·속도 경쟁이 아니라 꾸준함이 점수가 된다 (개근과 자연 연동)
RANK_SUBJECT_POINT = 10
RANK_FULLDAY_BONUS = 40


def _grade_scores(db: Session, student_ids: list[str]) -> dict[str, int]:
    """학생별 랭킹 점수 = 완료 과목 수 × 10 + 전과목 완료일 수 × 40 (daily_quiz_status 실집계)."""
    rows = (
        db.query(
            DailyQuizStatus.student_id,
            DailyQuizStatus.quiz_date,
            func.count(DailyQuizStatus.id),
        )
        .filter(
            DailyQuizStatus.student_id.in_(student_ids),
            DailyQuizStatus.status == "done",
        )
        .group_by(DailyQuizStatus.student_id, DailyQuizStatus.quiz_date)
        .all()
    )
    full = len(D.SUBJECT_ORDER)  # 전 과목 수 (6)
    scores: dict[str, int] = {}
    for sid, _day, done_cnt in rows:
        pts = int(done_cnt) * RANK_SUBJECT_POINT + (RANK_FULLDAY_BONUS if int(done_cnt) >= full else 0)
        scores[sid] = scores.get(sid, 0) + pts
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
    attempts_today = dict(
        db.query(LearningAttempt.subject, func.count(LearningAttempt.id))
        .filter(LearningAttempt.student_id == me.id, LearningAttempt.created_at >= today_start)
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
    return {
        "nickname": me.nickname,
        "level": me.level,
        "coins": me.coins,
        "student_code": me.student_code,
        "today": {"done": today_done, "total": today_total},
        "subjects": subjects,
        # 성장 그래프: learning_attempts 실집계 (시도 없으면 D)
        "growth": fb(aggregate.student_growth(db, me), D.HOME_GROWTH),
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
    out = []
    for b in all_badges:
        sb = mine.get(b.id)
        earned = bool(sb and sb.earned_at)
        if earned:
            foot = _earned_foot(sb.earned_at)
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
                "progress": (sb.progress if sb else 0.0),
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
    quizzes = [
        {
            "id": r.id,
            "subject": r.subject,
            "topic": r.topic,
            "status": r.status,
            "reward": r.reward_coins,
            "meta": D.SUBJECT_META.get(r.subject, {}),
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
    if week_rows:
        days_done = {r.created_at.date().weekday() for r in week_rows if r.created_at}
        week = []
        for i, label in enumerate(["월", "화", "수", "목", "금", "토", "일"]):
            day: dict = {"label": label, "done": i in days_done}
            if i == today.weekday():
                day["today"] = True
            week.append(day)
    else:
        week = D.DAILY_QUIZ_WEEK
    streak = 0
    for day in week:
        if day.get("done"):
            streak += 1
        else:
            break

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
            db.rollback()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="냥코인이 부족해요.")
        db.add(CoinTransaction(student_id=me.id, amount=-item.price, reason=f"{item.name} 구매"))
    db.add(StudentItem(student_id=me.id, item_id=item.id))
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
    return {"ok": True, "nickname": me.nickname, "age": me.age}


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
    bonus = 0
    if mine["rank"] in RANK_TOP3_COINS and mine["score"] > 0:
        already = (
            db.query(CoinTransaction)
            .filter(
                CoinTransaction.student_id == me.id,
                CoinTransaction.reason.like("%랭킹 보상"),
                func.date(CoinTransaction.created_at) == date.today(),
            )
            .first()
        )
        if already is None:
            bonus = RANK_TOP3_COINS[mine["rank"]]
            me.coins += bonus
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
            db.flush()
        earned = (
            db.query(StudentBadge)
            .filter(StudentBadge.student_id == me.id, StudentBadge.badge_id == badge.id)
            .first()
        )
        if earned is None:
            db.add(StudentBadge(student_id=me.id, badge_id=badge.id, earned_at=datetime.now(), progress=1))
            db.commit()
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

    coins_earned = 0
    # 복습(replay: 전날 다시풀기·오늘 재도전)은 보상 없음 — 반복 파밍 차단
    if req.result == "correct" and not req.replay:
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
        if earned_today < DAILY_LEARNING_COIN_CAP:
            coins_earned = 10
            me.coins += coins_earned
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
    prev_total = (
        db.query(func.count(LearningAttempt.id))
        .filter(LearningAttempt.student_id == me.id, LearningAttempt.subject == req.subject)
        .scalar()
        or 0
    )
    prev_correct = (
        db.query(func.count(LearningAttempt.id))
        .filter(
            LearningAttempt.student_id == me.id,
            LearningAttempt.subject == req.subject,
            LearningAttempt.result == "correct",
        )
        .scalar()
        or 0
    )
    total = prev_total + 1
    correct = prev_correct + (1 if req.result == "correct" else 0)
    prog.accuracy = round(correct / total * 100, 1)

    # 일일 잠금 규칙: 오늘의퀴즈 상태는 '오늘' 것만 갱신 가능(미래 날짜 미리 완료 불가 —
    # quiz_date는 항상 서버의 오늘). 복습(replay)은 상태를 건드리지 않는다(전날 다시풀기는 기록만).
    if not req.replay:
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
        quiz.status = "done" if req.completed else ("progress" if quiz.status != "done" else "done")
    db.commit()
    return {"ok": True, "attempt_id": attempt.id, "coins_earned": coins_earned, "coins": me.coins}


# ---------------------------------------------------------------- 실전 게임 세션 (생활 — ms 문제은행)
class _GameAnswerReq(_GBaseModel):
    question_id: str
    option_id: str
    last: bool = False  # 세션의 마지막 문항 → 오늘의퀴즈 완료 처리
    replay: bool = False  # 복습 모드 — 상태·코인 반영 없음


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

    day 지정 시: 그 일차 커리큘럼의 playable 문항 (미래 일차는 잠금 → available=false).
    day 미지정: 생활 전체에서 무작위(빠른 연습용).
    """
    _me(principal)
    if subject != "생활":
        return {"available": False, "subject": subject, "questions": []}
    from app.services import curriculum as _cur
    from app.services import life_bank

    if day is not None:
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

    pool = [q for q in life_bank.LIFE_FULL if q["playable"]]
    picked = _random.sample(pool, min(count, len(pool)))
    return {"available": True, "subject": subject, "questions": [life_bank.public_question(q) for q in picked]}


@router.post("/students/me/game-answer")
def game_answer(
    req: _GameAnswerReq,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """문항 1개 서버 채점 + 학습기록 저장 — 자기신고가 아닌 서버 판정 결과를 기록한다."""
    me = _me(principal)
    from app.services import life_bank

    q = life_bank.get_question(req.question_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="문항을 찾을 수 없습니다.")
    correct = str(req.option_id) == str(q["answer"])
    answer_opt = next((o for o in q["options"] if o["id"] == q["answer"]), None)

    # 서버 판정 결과를 학습기록으로 저장 (기존 save_attempt와 동일 부수효과: 코인 상한·진도·퀴즈 상태)
    attempt_req = AttemptCreate(
        subject="생활",
        result="correct" if correct else "incorrect",
        score=20 if correct else 0,  # 5문 기준 100점 만점
        completed=req.last and not req.replay,
        replay=req.replay,
    )
    saved = save_attempt(attempt_req, principal, db)
    return {
        "correct": correct,
        "answer_id": q["answer"],
        "answer_text": answer_opt["text"] if answer_opt else "",
        "hint": q["hint"],
        "coins_earned": saved.get("coins_earned", 0),
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
    s = {**D.RESULT_SUBJECTS[key], **(aggregate.student_result_today(db, me, key) or {})}
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
    return {
        "subject": key,
        "nickname": me.nickname,
        "meta": D.SUBJECT_META[key],
        **s,
        "total": 5,
        "levels": D.RESULT_LEVELS,
        "today_done": sorted(done_set, key=D.SUBJECT_ORDER.index),
        "subject_order": D.SUBJECT_ORDER,
        "all_done_today": done_set >= set(D.SUBJECT_ORDER),
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
