"""원천 테이블 실시간 집계 — 차트/대시보드 KPI용.

집계 소스: learning_attempts, behavior_summaries, api_usage_logs,
student_badges, student_profiles/classes.

패턴: 각 집계 함수는 스코프에 원천 데이터가 없으면 None(또는 빈 값)을 반환하고,
엔드포인트에서 `fb(집계값, D.디자인값)` 으로 stat_blobs(D) fallback 한다.
→ 화면은 절대 비지 않고, 시도가 쌓이는 즉시 실데이터로 바뀐다.

텍스트성 값(AI 코멘트/추천 문구/축 라벨 등)은 D를 유지한다.
"""

from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Any, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    ApiUsageLog,
    BehaviorSummary,
    LearningAttempt,
    StudentBadge,
    StudentProfile,
)
from app.services.stats import D
from app.utils.helpers import student_display_name


def fb(value: Any, default: Any) -> Any:
    """집계 결과가 비어 있으면 디자인(D) 값 유지 — 화면이 절대 비지 않게."""
    if value is None:
        return default
    if isinstance(value, (list, dict, tuple, str)) and len(value) == 0:
        return default
    return value


# ---------------------------------------------------------------- 공통 유틸
def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _dt(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _fmt_n(n: int) -> str:
    return f"{n:,}"


def _fmt_dur(ms: int) -> str:
    """solve_time_ms 합계 → '5h 43m' / '40m' 표기 (홈 주간 차트 디자인 포맷)."""
    total_min = round(ms / 60000)
    h, m = divmod(total_min, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def _acc(rows: Sequence[LearningAttempt]) -> int | None:
    if not rows:
        return None
    correct = sum(1 for r in rows if r.result == "correct")
    return round(correct / len(rows) * 100)


def _delta_str(cur: int | None, prev: int | None) -> str:
    if cur is None or prev is None:
        return "+0%p"
    d = cur - prev
    return f"{'+' if d >= 0 else ''}{d}%p"


def week_label(today: date | None = None) -> str:
    """현재 주(월~일)의 실제 날짜 라벨 — 고정 문자열 대신 오늘 기준 동적 생성."""
    today = today or date.today()
    ws = _week_start(today)
    we = ws + timedelta(days=6)
    week_of_month = (ws.day - 1) // 7 + 1
    return f"{ws.month}월 {week_of_month}째 주 ({ws.month}.{ws.day}~{we.month}.{we.day})"


def period_label(period: str, today: date | None = None) -> str:
    today = today or date.today()
    if period == "month":
        return f"{today.year}년 {today.month}월"
    if period in ("year", "term", "semester"):
        return f"{today.year}년"
    return week_label(today)


def attempts(
    db: Session,
    *,
    student_ids: Sequence[str] | None = None,
    org_id: str | None = None,
    since: date | None = None,
    until: date | None = None,
    subject: str | None = None,
) -> list[LearningAttempt]:
    q = db.query(LearningAttempt)
    if student_ids is not None:
        if not student_ids:
            return []
        q = q.filter(LearningAttempt.student_id.in_(list(student_ids)))
    if org_id is not None:
        q = q.filter(LearningAttempt.organization_id == org_id)
    if since is not None:
        q = q.filter(LearningAttempt.created_at >= _dt(since))
    if until is not None:
        q = q.filter(LearningAttempt.created_at < _dt(until))
    if subject:
        q = q.filter(LearningAttempt.subject == subject)
    return q.order_by(LearningAttempt.created_at).all()


def _bucketize(
    rows: Sequence[LearningAttempt], buckets: list[tuple[date, date]]
) -> list[list[LearningAttempt]]:
    out: list[list[LearningAttempt]] = [[] for _ in buckets]
    for r in rows:
        d = r.created_at.date() if r.created_at else date.today()
        for i, (s, e) in enumerate(buckets):
            if s <= d < e:
                out[i].append(r)
                break
    return out


def _acc_series(rows: Sequence[LearningAttempt], buckets: list[tuple[date, date]]) -> list[int]:
    """버킷별 정답률 — 빈 버킷은 직전 값(없으면 전체 평균)으로 채움."""
    grouped = _bucketize(rows, buckets)
    overall = _acc(rows) or 0
    raw = [_acc(g) for g in grouped]
    filled: list[int] = []
    prev: int | None = None
    for v in raw:
        if v is None:
            v = prev if prev is not None else overall
        filled.append(v)
        prev = v
    return filled


def _streak_days(days: set[date], today: date | None = None) -> int:
    today = today or date.today()
    cur = today if today in days else today - timedelta(days=1)
    n = 0
    while cur in days:
        n += 1
        cur -= timedelta(days=1)
    return n


def student_roster_metrics(
    db: Session, student_ids: Sequence[str], today: date | None = None
) -> dict[str, dict]:
    """학생별 로스터 지표를 learning_attempts에서 실집계 — 교사/기관 명단용.

    반환: {student_id: {acc, streak, solved, today}} — **시도가 1건이라도 있는 학생만** 포함한다.
    시도가 없는(데모/미플레이) 학생은 키가 없어, 호출부가 기존 seed(LearningSummary) 값으로
    폴백한다(코드베이스의 fb 철학과 동일). 즉 실제로 푸는 학생은 실데이터, 데모는 그대로.
    정의: acc=28일 정답률(canonical), streak=오늘 기준 역산 출석 연속, solved=누적 시도,
    today=오늘 DailyQuizStatus done 여부(권위 완료 정의).
    """
    from collections import defaultdict

    from app.models import DailyQuizStatus

    today = today or date.today()
    ids = [i for i in student_ids]
    if not ids:
        return {}
    since28 = today - timedelta(days=27)  # 오늘 포함 28일 창
    week_start = _week_start(today)

    solved: dict[str, int] = defaultdict(int)
    dates: dict[str, set[date]] = defaultdict(set)
    acc_n: dict[str, int] = defaultdict(int)
    acc_d: dict[str, int] = defaultdict(int)
    week_ms: dict[str, int] = defaultdict(int)
    rows = (
        db.query(
            LearningAttempt.student_id,
            LearningAttempt.result,
            func.date(LearningAttempt.created_at),
            LearningAttempt.solve_time_ms,
        )
        .filter(LearningAttempt.student_id.in_(ids))
        .all()
    )
    for sid, result, d, ms in rows:
        dd = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
        solved[sid] += 1
        dates[sid].add(dd)
        if dd >= since28:
            acc_d[sid] += 1
            if result == "correct":
                acc_n[sid] += 1
        if dd >= week_start:
            week_ms[sid] += ms or 0

    done_today = {
        r[0]
        for r in db.query(DailyQuizStatus.student_id)
        .filter(
            DailyQuizStatus.student_id.in_(ids),
            DailyQuizStatus.quiz_date == today,
            DailyQuizStatus.status == "done",
        )
        .distinct()
        .all()
    }

    out: dict[str, dict] = {}
    for sid in solved:  # 시도가 있는 학생만 (없으면 호출부가 seed 폴백)
        out[sid] = {
            "acc": round(acc_n[sid] / acc_d[sid] * 100) if acc_d[sid] else 0,
            "streak": _streak_days(dates[sid], today),
            "solved": solved[sid],
            "today": "done" if sid in done_today else "none",
            "week_min": round(week_ms[sid] / 60000),  # 이번 주 학습 시간(분)
        }
    return out


def _rel_time(dt: datetime | None) -> str:
    if dt is None:
        return ""
    # created_at은 로컬 naive(datetime.now) 저장 규약 — utcnow와 비교하면 KST에서
    # 항상 '방금 전'이 되고, 아침 시간대엔 '-1일 전'까지 나온다(자정 경계 밀림).
    now = datetime.now()
    if dt.date() == now.date():
        mins = max(0, int((now - dt).total_seconds() // 60))
        if mins < 30:
            return "방금 전"
        return "오늘"
    days = (now.date() - dt.date()).days
    if days == 1:
        return "어제"
    return f"{days}일 전"


def _display_name(s: StudentProfile) -> str:
    # 공용 로직(helpers.student_display_name) — 실명 최우선. 예전 복사본엔 실명 분기가
    # 빠져 있어 분석 화면만 명단과 다른 이름(닉네임)이 보였다.
    return student_display_name(s, D.CODE_FULL_NAME)


# estimated_reason → 화면 라벨/색/태그 (텍스트는 디자인 어휘 유지)
_REASON_META = {
    "개념 혼동": {"label": "개념 오답 추정", "color": "#FF5A6E", "tag": "개념 보강"},
    "조작 실수": {"label": "조작 실수 추정", "color": "#2E7BFF", "tag": "조작 연습"},
    "선택지 혼동": {"label": "선택지 혼동 추정", "color": "#8B6BFF", "tag": "선택지 혼동"},
    "UI 문제": {"label": "UI 문제 후보", "color": "#FF922E", "tag": "조작 도움"},
}


def _reasons_dist(rows: Sequence[LearningAttempt]) -> list[dict]:
    wrong = [r.estimated_reason for r in rows if r.result != "correct" and r.estimated_reason]
    if not wrong:
        return []
    counts = Counter(wrong)
    total = sum(counts.values())
    out = []
    for reason, n in counts.most_common():
        meta = _REASON_META.get(reason, {"label": f"{reason} 추정", "color": "#B0A79B"})
        out.append({"label": meta["label"], "pct": f"{round(n / total * 100)}%", "color": meta["color"]})
    return out


# ---------------------------------------------------------------- 학생: 나의기록
def student_records(db: Session, me: StudentProfile) -> dict | None:
    """weeks/calendar/mastery/accuracy_series/activities/stats — learning_attempts 실집계."""
    today = date.today()
    all_rows = attempts(db, student_ids=[me.id])
    rows = [
        r
        for r in all_rows
        if r.created_at and r.created_at.date() >= today - timedelta(weeks=8)
    ]
    if not rows:
        return None

    # 상단 통계 4종 — 전체 기간 실집계
    total_min = round(sum(r.solve_time_ms or 0 for r in all_rows) / 60000)
    stats = {
        "streak_days": _streak_days({r.created_at.date() for r in all_rows if r.created_at}),
        "total_hours": total_min // 60,
        "total_minutes": total_min % 60,
        "total_solved": len(all_rows),
        "avg_accuracy": _acc(all_rows) or 0,
    }

    # 최근 4주 주별 학습량
    this_ws = _week_start(today)
    week_buckets = [
        (this_ws - timedelta(weeks=i), this_ws - timedelta(weeks=i - 1)) for i in range(3, -1, -1)
    ]
    labels = ["3주 전", "2주 전", "지난주", "이번주"]
    grouped = _bucketize(rows, week_buckets)
    max_n = max((len(g) for g in grouped), default=0) or 1
    weeks = [
        {
            "label": labels[i],
            "minutes": round(sum(r.solve_time_ms for r in g) / 60000),
            "pct": round(len(g) / max_n * 100),
        }
        for i, g in enumerate(grouped)
    ]

    # 이번 달 학습 달력
    month_first = today.replace(day=1)
    next_month = (month_first + timedelta(days=32)).replace(day=1)
    learned = sorted(
        {
            r.created_at.date().day
            for r in rows
            if r.created_at and month_first <= r.created_at.date() < next_month
        }
    )
    calendar = {
        "month": today.month,
        "year": today.year,
        "today": today.day,
        "blanks": (month_first.weekday() + 1) % 7,  # 일요일 시작 그리드
        "days": (next_month - month_first).days,
        "learned": fb(learned, D.RECORDS_CAL_LEARNED),
    }

    # 과목별 실력 (최근 28일 vs 이전 28일)
    recent = [r for r in rows if r.created_at and r.created_at.date() >= today - timedelta(days=28)]
    prior = [r for r in rows if r.created_at and r.created_at.date() < today - timedelta(days=28)]
    mastery = []
    for sub in D.SUBJECT_ORDER:
        sub_rows = [r for r in recent if r.subject == sub]
        if not sub_rows:
            continue
        pct = _acc(sub_rows) or 0
        prev_pct = _acc([r for r in prior if r.subject == sub])
        meta = D.SUBJECT_META[sub]
        mastery.append(
            {
                "name": D.GAME_SUBJECTS.get(sub, {}).get("gameTitle", sub),
                "icon": meta["icon"],
                "color": meta["color"],
                "bg": meta["soft"],
                "pct": pct,
                "solved": len(sub_rows),
                "delta": pct - prev_pct if prev_pct is not None else 0,
                "correct": sum(1 for r in sub_rows if r.result == "correct"),
            }
        )
    mastery.sort(key=lambda m: -m["pct"])

    # 정답률 흐름 (최근 6주 주별) — 배열 길이 6 유지
    six_buckets = [
        (this_ws - timedelta(weeks=i), this_ws - timedelta(weeks=i - 1)) for i in range(5, -1, -1)
    ]
    series: dict[str, dict] = {}
    for key, design in D.RECORD_ACC_SERIES.items():
        sub_rows = rows if key == "전체" else [r for r in rows if r.subject == key]
        if sub_rows:
            data = _acc_series(sub_rows, six_buckets)
        else:
            data = design["data"]  # 시도 없는 과목은 D 유지 (빈 차트 방지)
        series[key] = {"color": design["color"], "data": data}

    # 최근 학습 기록 4건 — (날짜, 과목) 세션 단위
    sessions: dict[tuple[date, str], list[LearningAttempt]] = {}
    for r in rows:
        d = r.created_at.date() if r.created_at else today
        sessions.setdefault((d, r.subject), []).append(r)
    ordered = sorted(sessions.items(), key=lambda kv: max(r.created_at for r in kv[1]), reverse=True)
    activities, seen_subjects = [], set()
    for (d, sub), group in ordered:
        if sub in seen_subjects:
            continue
        seen_subjects.add(sub)
        meta = D.SUBJECT_META[sub]
        acc = _acc(group) or 0
        activities.append(
            {
                "title": D.GAME_SUBJECTS.get(sub, {}).get("gameTitle", f"{sub} 학습"),
                "sub": f"{sub} · {len(group)}문제",
                "icon": meta["icon"],
                "color": meta["color"],
                "bg": meta["soft"],
                "result": f"정답률 {acc}%",
                "time": _rel_time(max(r.created_at for r in group)),
            }
        )
        if len(activities) >= 4:
            break

    return {
        "weeks": weeks,
        "calendar": calendar,
        "mastery": mastery,
        "accuracy_series": series,
        "activities": activities,
        "stats": stats,
    }


# ---------------------------------------------------------------- 학생: 홈 growth
def student_growth(db: Session, me: StudentProfile) -> dict | None:
    today = date.today()
    ws = _week_start(today)
    rows = attempts(db, student_ids=[me.id], since=today - timedelta(days=60))
    if not rows:
        return None
    week_rows = [r for r in rows if r.created_at and r.created_at.date() >= ws]
    prev_week_rows = [
        r
        for r in rows
        if r.created_at and ws - timedelta(weeks=1) <= r.created_at.date() < ws
    ]
    days = {r.created_at.date() for r in rows if r.created_at}
    counts = [0] * 7
    time_ms = [0] * 7
    for r in week_rows:
        wd = r.created_at.date().weekday()
        counts[wd] += 1
        time_ms[wd] += r.solve_time_ms or 0
    max_n = max(counts) or 1
    day_names = ["월", "화", "수", "목", "금", "토", "일"]
    week_bars = []
    for i, n in enumerate(counts):
        bar = {"day": day_names[i], "pct": round(n / max_n * 100), "time": _fmt_dur(time_ms[i])}
        if i == today.weekday():
            bar["today"] = True
        week_bars.append(bar)
    growth = dict(D.HOME_GROWTH)  # 데이터 없을 때의 텍스트성 값은 D 유지
    week_total_ms = sum(time_ms)
    prev_total_ms = sum(r.solve_time_ms or 0 for r in prev_week_rows)
    if prev_total_ms > 0:
        pct = round((week_total_ms - prev_total_ms) / prev_total_ms * 100)
        growth["time_delta"] = f"{'+' if pct >= 0 else ''}{pct}%"
    growth.update(
        {
            "streak_days": _streak_days(days),
            "week_solved": len(week_rows),
            "accuracy": fb(_acc(week_rows), _acc(rows) or growth.get("accuracy", 0)),
            "week_bars": week_bars,
            "week_total": _fmt_dur(week_total_ms),
        }
    )
    return growth


# ---------------------------------------------------------------- 학생: 학습결과
def student_result_today(db: Session, me: StudentProfile, subject: str) -> dict | None:
    rows = attempts(db, student_ids=[me.id], since=date.today(), subject=subject)
    if not rows:
        return None
    # 결과 화면은 '방금 끝낸 세션'을 보여준다 — 오늘 누적(전 단계·재시도 합산)이 아니라
    # 마지막 세션만 집계한다. 세션 경계는 (a) 한 세션 문항 수(EDU_SESSION_TOTAL, 5)와
    # (b) 시도 사이 시간 간격(SESSION_GAP 초과면 다른 세션)으로 판정한다. 이렇게 안 하면
    # correct/total이 452/5처럼 어긋난다(오늘 616회 시도가 통째로 잡히던 버그).
    session_gap = timedelta(minutes=15)
    session_size = 5  # captcha_api.EDU_SESSION_TOTAL과 동일(순환 import 회피 위해 로컬 상수)
    session = [rows[-1]]
    for r in reversed(rows[:-1]):
        if len(session) >= session_size:
            break
        prev = session[-1]
        if prev.created_at and r.created_at and (prev.created_at - r.created_at) <= session_gap:
            session.append(r)
        else:
            break
    session.reverse()  # 시간 오름차순
    correct = sum(1 for r in session if r.result == "correct")
    score = sum(r.score for r in session)
    # 걸린 시간: 문항별 solve_time_ms 합(폴백 저장 경로)과 세션 첫~끝 시각 간격(위젯
    # 경로는 solve_time_ms=0이라 이쪽이 실측) 중 큰 값. 둘 다 없으면 0:00.
    total_ms = sum(r.solve_time_ms for r in session)
    if len(session) >= 2 and session[0].created_at and session[-1].created_at:
        span_ms = (session[-1].created_at - session[0].created_at).total_seconds() * 1000
        total_ms = max(total_ms, span_ms)
    m, s = divmod(round(total_ms / 1000), 60)
    streak = 0
    for r in reversed(session):
        if r.result == "correct":
            streak += 1
        else:
            break
    return {
        "correct": correct,
        "wrong": len(session) - correct,
        "total": len(session),
        "score": f"+{score}",
        "time": f"{m}:{s:02d}",
        "streak": streak,
    }


# ---------------------------------------------------------------- 교사: 대시보드
def teacher_dashboard(db: Session, students: Sequence[StudentProfile]) -> dict | None:
    """kpis/bar_data/game_bars/attention 실집계 — 시도 없으면 None."""
    today = date.today()
    ws = _week_start(today)
    ids = [s.id for s in students]
    rows = attempts(db, student_ids=ids, since=today - timedelta(days=21))
    if not rows:
        return None
    week_rows = [r for r in rows if r.created_at.date() >= ws]
    prev_week_rows = [r for r in rows if ws - timedelta(weeks=1) <= r.created_at.date() < ws]
    recent_rows = [r for r in rows if r.created_at.date() >= today - timedelta(days=14)]

    today_done = len({r.student_id for r in rows if r.created_at.date() == today})
    total = len(students) or 1
    acc = _acc(week_rows)
    kpis = {
        "today_done": today_done,
        "today_done_pct": f"{round(today_done / total * 100)}%",
        "avg_accuracy": fb(acc, _acc(rows) or 0),
        "avg_accuracy_delta": _delta_str(acc, _acc(prev_week_rows)),
        # 분석(kHelp)·attention과 같은 단일 정의(_need_help)
        "need_help": _need_help(rows) or 0,
    }

    # 요일별 참여 학생 수 (이번 주)
    day_names = ["월", "화", "수", "목", "금", "토", "일"]
    per_day: list[set] = [set() for _ in range(7)]
    for r in week_rows:
        per_day[r.created_at.date().weekday()].add(r.student_id)
    bar_data = []
    for i in range(7):
        bar = {"day": day_names[i], "n": len(per_day[i])}
        if i == today.weekday():
            bar["today"] = True
        bar_data.append(bar)

    # 카테고리(과목 게임)별 정답률
    game_bars = []
    for sub in D.SUBJECT_ORDER:
        sub_rows = [r for r in recent_rows if r.subject == sub]
        if not sub_rows:
            continue
        game_bars.append(
            {
                "label": D.GAME_SUBJECTS.get(sub, {}).get("gameTitle", sub),
                "pct": _acc(sub_rows) or 0,
                "color": D.SUBJECT_META[sub]["color"],
            }
        )
    game_bars.sort(key=lambda g: -g["pct"])
    game_bars = game_bars[:5]

    attention = _attention(students, recent_rows)
    return {"kpis": kpis, "bar_data": bar_data, "game_bars": game_bars, "attention": attention}


def _need_help(rows: Sequence[LearningAttempt]) -> int | None:
    """도움 필요 학생 수 — 단일 정의: 최근 14일 · 시도≥5 · 정답률<70.

    14일 창에 시도가 하나도 없으면 None (호출부가 디자인값 유지).
    대시보드 need_help / 분석 kHelp / attention 게이트가 모두 이 정의를 쓴다.
    """
    cutoff = date.today() - timedelta(days=14)
    recent = [r for r in rows if r.created_at and r.created_at.date() >= cutoff]
    if not recent:
        return None
    by_student: dict[str, list[LearningAttempt]] = {}
    for r in recent:
        by_student.setdefault(r.student_id, []).append(r)
    return sum(1 for g in by_student.values() if len(g) >= 5 and (_acc(g) or 0) < 70)


def need_help_count(db: Session, students: Sequence[StudentProfile]) -> int | None:
    """엔드포인트용 — 학급 학생들의 14일 창 도움 필요 수 (시도 없으면 None)."""
    ids = [s.id for s in students]
    rows = attempts(db, student_ids=ids, since=date.today() - timedelta(days=14))
    return _need_help(rows)


def _attention(students: Sequence[StudentProfile], rows: Sequence[LearningAttempt]) -> list[dict]:
    """도움 필요 학생 목록 — KPI(need_help)와 동일 기준(시도≥5·정답률<70)으로 게이트.

    기준이 다르면 대시보드 KPI "1명" 옆에 카드 3명이 떠서 서로 모순돼 보인다.
    """
    by_student: dict[str, list[LearningAttempt]] = {}
    for r in rows:
        by_student.setdefault(r.student_id, []).append(r)
    students_by_id = {s.id: s for s in students}
    scored = []
    for sid, g in by_student.items():
        s = students_by_id.get(sid)
        if s is None or len(g) < 5:
            continue
        a = _acc(g) or 0
        if a >= 70:
            continue
        scored.append((a, s, g))
    scored.sort(key=lambda t: t[0])
    out = []
    for acc, s, g in scored[:3]:
        weak_sub = _weak_subject(g) or "학습"
        reasons = [r.estimated_reason for r in g if r.result != "correct" and r.estimated_reason]
        top_reason = Counter(reasons).most_common(1)[0][0] if reasons else None
        meta = _REASON_META.get(top_reason or "", {})
        note = f"{weak_sub} 정답률 {acc}%"
        if top_reason:
            note += f" · {top_reason} 추정"
        out.append({"name": _display_name(s), "note": note, "tag": meta.get("tag", "학습 점검")})
    return out


def _weak_subject(rows: Sequence[LearningAttempt]) -> str | None:
    by_sub: dict[str, list[LearningAttempt]] = {}
    for r in rows:
        by_sub.setdefault(r.subject, []).append(r)
    worst, worst_acc = None, 101
    for sub, g in by_sub.items():
        if len(g) < 3:
            continue
        a = _acc(g) or 0
        if a < worst_acc:
            worst, worst_acc = sub, a
    return worst


# ---------------------------------------------------------------- 분석 (교사/기관 공용)
def _period_buckets(period: str, axis_len: int) -> tuple[list[tuple[date, date]], date, date]:
    """period → 축 길이에 맞춘 버킷 + (기간 시작, 기간 끝exclusive)."""
    today = date.today()
    if period == "week":  # 일별 (이번 주 월~일)
        ws = _week_start(today)
        buckets = [(ws + timedelta(days=i), ws + timedelta(days=i + 1)) for i in range(axis_len)]
    elif period == "month":  # 주별 (이번 달)
        first = today.replace(day=1)
        next_month = (first + timedelta(days=32)).replace(day=1)
        buckets = []
        for i in range(axis_len):
            s = first + timedelta(days=7 * i)
            e = min(first + timedelta(days=7 * (i + 1)), next_month)
            if s >= next_month:
                s = e = next_month
            buckets.append((s, e))
    else:  # term/semester/year — 월별 (최근 axis_len개월, 이번 달 포함)
        first_of_month = today.replace(day=1)
        starts = []
        cur = first_of_month
        for _ in range(axis_len):
            starts.append(cur)
            cur = (cur - timedelta(days=1)).replace(day=1)
        starts.reverse()
        buckets = [
            (s, (s + timedelta(days=32)).replace(day=1)) for s in starts
        ]
    return buckets, buckets[0][0], buckets[-1][1]


def analytics(
    db: Session,
    students: Sequence[StudentProfile],
    period: str,
    axis_len: int,
    subject: str | None,
) -> dict | None:
    """kAcc/kActive/kSolved/kHelp/accSeries/subjects/reasons/students/attention 실집계."""
    ids = [s.id for s in students]
    buckets, start, end = _period_buckets(period, axis_len)
    span = (end - start).days
    # kHelp(14일 단일 정의) 계산분까지 포함하도록 fetch 하한 보장 — 주 초(월요일)처럼
    # 기간 창이 14일보다 짧을 때도 도움필요 수가 대시보드와 같게 나온다.
    fetch_since = min(start - timedelta(days=span), date.today() - timedelta(days=14))
    all_rows = attempts(db, student_ids=ids, since=fetch_since, until=end)
    rows = [r for r in all_rows if r.created_at.date() >= start]
    # 전기간 비교 창은 원래 정의([start-span, start)) 유지 — fetch 하한 확장(14일 보장)이
    # kAccDelta/과목 delta/학생 trend 계산에 기간 밖 시도를 섞지 않게 한다.
    prev_rows = [
        r for r in all_rows if start - timedelta(days=span) <= r.created_at.date() < start
    ]
    if not rows:
        return None
    filtered = [r for r in rows if r.subject == subject] if subject else rows
    if subject and not filtered:
        return None
    acc = _acc(filtered) or 0
    acc_prev = _acc([r for r in prev_rows if r.subject == subject] if subject else prev_rows)

    active = len({r.student_id for r in rows})
    by_student: dict[str, list[LearningAttempt]] = {}
    for r in rows:
        by_student.setdefault(r.student_id, []).append(r)
    # 도움 필요 수는 기간 탭과 무관하게 대시보드와 같은 창(최근 14일)으로 —
    # 화면마다 1명/2명으로 갈리지 않게 단일 정의(14일·시도≥5·정답률<70)를 쓴다
    help_n = _need_help(all_rows) or 0

    # 과목별 total/pct/correct (+ 전기간 대비 delta)
    subjects = []
    for sub in D.SUBJECT_ORDER:
        g = [r for r in rows if r.subject == sub]
        if not g:
            continue
        pct = _acc(g) or 0
        prev_pct = _acc([r for r in prev_rows if r.subject == sub])
        subjects.append(
            {
                "name": sub,
                "icon": D.SUBJECT_META[sub]["icon"],
                "pct": pct,
                "delta": pct - prev_pct if prev_pct is not None else 0,
                "total": len(g),
                "correct": sum(1 for r in g if r.result == "correct"),
            }
        )

    # 학생별 표
    students_by_id = {s.id: s for s in students}
    student_rows = []
    prev_by_student: dict[str, list[LearningAttempt]] = {}
    for r in prev_rows:
        prev_by_student.setdefault(r.student_id, []).append(r)
    for sid, g in by_student.items():
        s = students_by_id.get(sid)
        if s is None:
            continue
        a = _acc(g) or 0
        pa = _acc(prev_by_student.get(sid, []))
        trend = "유지" if pa is None or abs(a - pa) < 2 else ("상승" if a > pa else "하락")
        sessions = len({(r.created_at.date(), r.subject) for r in g})
        student_rows.append(
            {
                "name": _display_name(s),
                "acc": a,
                "sessions": f"{sessions}회",
                "weak": _weak_subject(g) or "-",
                "trend": trend,
            }
        )
    student_rows.sort(key=lambda r: -r["acc"])

    return {
        "accSeries": _acc_series(filtered, buckets),
        "avg": acc,
        "kAcc": str(acc),
        "kAccDelta": _delta_str(acc, acc_prev),
        "kActive": f"{active} / {len(students)}명",
        "kSolved": _fmt_n(len(rows)),
        "kHelp": str(help_n),
        "subjects": subjects,
        "reasons": _reasons_dist(rows),
        "students": student_rows,
        "attention": _attention(students, rows),
    }


# ---------------------------------------------------------------- 기관: 대시보드
def _org_period_range(period: str) -> tuple[date, date]:
    today = date.today()
    if period == "week":
        return _week_start(today), today + timedelta(days=1)
    if period == "month":
        first = today.replace(day=1)
        return first, today + timedelta(days=1)
    return today.replace(month=1, day=1), today + timedelta(days=1)


def org_dashboard_overrides(db: Session, org_id: str, period: str) -> dict:
    """D blob 위에 덮어쓸 실집계 키만 반환 (원천 없으면 키 제외 → D 유지)."""
    today = date.today()
    start, end = _org_period_range(period)
    out: dict = {}

    students_n = (
        db.query(StudentProfile)
        .filter(StudentProfile.organization_id == org_id, StudentProfile.status != "disabled")
        .count()
    )
    if students_n:
        out["kStudents"] = _fmt_n(students_n)

    # API 사용량: week 화면 라벨이 '오늘 API 요청'이므로 week는 오늘 카운트
    api_since = today if period == "week" else start
    api_n = (
        db.query(func.count(ApiUsageLog.id))
        .filter(ApiUsageLog.organization_id == org_id, ApiUsageLog.created_at >= _dt(api_since))
        .scalar()
        or 0
    )
    if api_n:
        out["kApi"] = _fmt_n(api_n)
        ok = (
            db.query(func.count(ApiUsageLog.id))
            .filter(
                ApiUsageLog.organization_id == org_id,
                ApiUsageLog.created_at >= _dt(api_since),
                ApiUsageLog.status_code < 400,
            )
            .scalar()
            or 0
        )
        out["kPass"] = f"{ok / api_n * 100:.1f}"

    # 행동 요약: 조작 실패율 + 위험 신호 분포
    b_rows = (
        db.query(BehaviorSummary)
        .filter(BehaviorSummary.organization_id == org_id, BehaviorSummary.created_at >= _dt(start))
        .all()
    )
    if b_rows:
        fails = sum(1 for b in b_rows if (b.interaction_result or "") == "fail")
        out["kFail"] = f"{fails / len(b_rows) * 100:.1f}"
        risk = Counter(b.risk_level for b in b_rows)
        total = len(b_rows)
        low = round(risk.get("low", 0) / total * 100)
        review = round(risk.get("review", 0) / total * 100)
        out["dLow"] = low
        out["dReview"] = review
        out["dElevated"] = max(0, 100 - low - review)

    # 평균 풀이 시간 + 학년별 표 (attempts → class.grade group)
    rows = attempts(db, org_id=org_id, since=start, until=end)
    if rows:
        out["kAvg"] = f"{sum(r.solve_time_ms for r in rows) / len(rows) / 1000:.1f}"
        out["grades"] = _org_grades(db, org_id, rows)

    # 교사(스태프) 수 · 학급 수 · 동적 제목 — 원천(users/classes)이 있으면 항상 실집계
    from app.models import ClassRoom, Organization, User

    staff_n = (
        db.query(func.count(User.id))
        .filter(
            User.organization_id == org_id,
            User.role.in_(["teacher", "grade_head"]),
            User.status != "disabled",
        )
        .scalar()
        or 0
    )
    if staff_n:
        out["kTeachers"] = _fmt_n(staff_n)
        class_n = (
            db.query(func.count(ClassRoom.id))
            .filter(ClassRoom.organization_id == org_id)
            .scalar()
            or 0
        )
        out["kTeachersSub"] = f"교사 / {class_n} 학급"
    org = db.get(Organization, org_id)
    wk = (_week_start(today).day - 1) // 7 + 1
    out["subtitle"] = (
        f"{org.name if org else '우리 학교'} · {today.year}년 {today.month}월 {wk}주차 · 실시간 집계"
    )
    if "kApi" in out:  # apiCallValue는 kApi와 동일 소스(오늘 호출) — 표시 불일치 제거
        out["apiCallValue"] = out["kApi"]

    # 캡차 위험 신호 그래프 (r/pass/block/gradeBars) — behavior_summaries 실집계
    out.update(_org_security(db, org_id, period, start, end))
    return out


def _org_security(db: Session, org_id: str, period: str, start: date, end: date) -> dict:
    """behavior_summaries 실집계 → 위험 분포 r + 요일/주차별 통과·차단 시계열 + 학년별 pass/fail/block.

    캡차 위험 신호 데이터가 없는 기간/학년은 키를 제외(또는 D 유지) — 실트래픽이 쌓이면 채워진다.
    정의: pass=사람 통과율(risk_level=='low' 비율), block=플래그율(review+elevated 비율).
    """
    out: dict = {}
    b_rows = (
        db.query(BehaviorSummary)
        .filter(
            BehaviorSummary.organization_id == org_id,
            BehaviorSummary.created_at >= _dt(start),
            BehaviorSummary.created_at < _dt(end),
        )
        .all()
    )
    if not b_rows:
        return out

    def _pass(rows: Sequence) -> int:
        return round(sum(1 for b in rows if b.risk_level == "low") / len(rows) * 100)

    def _block(rows: Sequence) -> int:
        return round(sum(1 for b in rows if b.risk_level in ("review", "elevated")) / len(rows) * 100)

    total = len(b_rows)
    out["r"] = [
        round(sum(1 for b in b_rows if b.risk_level == "low") / total * 100),
        round(sum(1 for b in b_rows if b.risk_level == "review") / total * 100),
        round(sum(1 for b in b_rows if b.risk_level == "elevated") / total * 100),
        round(sum(1 for b in b_rows if (b.interaction_result or "") == "fail") / total * 100),
    ]

    # 시계열 버킷 (week=요일 7, month=주 5, year=월 12) — 원천 있는 버킷만 실값, 없으면 직전값 유지
    axis_len = {"week": 7, "month": 5}.get(period, 12)
    buckets, _s, _e = _period_buckets(period, axis_len)
    grouped: list[list] = [[] for _ in buckets]
    for b in b_rows:
        d = (b.created_at.date() if b.created_at else start)
        for i, (bs, be) in enumerate(buckets):
            if bs <= d < be:
                grouped[i].append(b)
                break
    pass_series, block_series = [], []
    pprev, bprev = None, None
    for g in grouped:
        p = _pass(g) if g else (pprev if pprev is not None else 0)
        bl = _block(g) if g else (bprev if bprev is not None else 0)
        pass_series.append(p)
        block_series.append(bl)
        pprev, bprev = p, bl
    out["pass"] = pass_series
    out["block"] = block_series

    # 학년별 pass/fail/block (behavior → class.grade)
    from app.models import ClassRoom

    grade_by_class = {
        c.id: c.grade for c in db.query(ClassRoom).filter(ClassRoom.organization_id == org_id).all()
    }
    class_by_student = {
        s.id: s.class_id
        for s in db.query(StudentProfile).filter(StudentProfile.organization_id == org_id).all()
    }
    by_grade: dict[int, list] = {}
    for b in b_rows:
        g = grade_by_class.get(class_by_student.get(b.student_id))
        if g is not None:
            by_grade.setdefault(g, []).append(b)
    grade_bars = []
    for g in sorted(by_grade):
        rows = by_grade[g]
        p = _pass(rows)
        fail = round(sum(1 for b in rows if (b.interaction_result or "") == "fail") / len(rows) * 100)
        block = round(sum(1 for b in rows if b.risk_level == "elevated") / len(rows) * 100)
        grade_bars.append({"label": f"{g}학년", "pass": p, "fail": fail, "block": block})
    if grade_bars:
        out["gradeBars"] = grade_bars
    return out


def _org_grades(db: Session, org_id: str, rows: Sequence[LearningAttempt]) -> list[dict]:
    from app.models import ClassRoom

    grade_by_class = {
        c.id: c.grade for c in db.query(ClassRoom).filter(ClassRoom.organization_id == org_id).all()
    }
    class_by_student = {
        s.id: s.class_id
        for s in db.query(StudentProfile).filter(StudentProfile.organization_id == org_id).all()
    }
    by_grade: dict[int, list[LearningAttempt]] = {}
    students_by_grade: dict[int, set] = {}
    for r in rows:
        g = grade_by_class.get(class_by_student.get(r.student_id))
        if g is None:
            continue
        by_grade.setdefault(g, []).append(r)
        students_by_grade.setdefault(g, set()).add(r.student_id)
    grades = [dict(row) for row in D.ORG_DASHBOARD_GRADES]  # 데이터 없는 학년은 D 유지
    for i, row in enumerate(grades):
        g = i + 1
        grade_rows = by_grade.get(g)
        if not grade_rows:
            continue
        acc = _acc(grade_rows) or 0
        avg_s = sum(r.solve_time_ms for r in grade_rows) / len(grade_rows) / 1000
        row.update(
            {
                "count": f"{len(students_by_grade[g])}명",
                "acc": f"{acc}%",
                "wrong": f"{100 - acc}%",
                "time": f"{avg_s:.1f}초",
            }
        )
    return grades


def org_api_usage_month(db: Session, org_id: str) -> int:
    first = date.today().replace(day=1)
    return (
        db.query(func.count(ApiUsageLog.id))
        .filter(ApiUsageLog.organization_id == org_id, ApiUsageLog.created_at >= _dt(first))
        .scalar()
        or 0
    )


def key_usage_this_month(db: Session, org_id: str) -> dict[str, int]:
    """키별 이번 달 challenge 발급 수(과금 단위=challenge 1건). {api_key_id: count}."""
    first = date.today().replace(day=1)
    rows = (
        db.query(ApiUsageLog.api_key_id, func.count(ApiUsageLog.id))
        .filter(
            ApiUsageLog.organization_id == org_id,
            ApiUsageLog.created_at >= _dt(first),
            ApiUsageLog.endpoint.like("%challenge%"),
            ApiUsageLog.api_key_id.isnot(None),
        )
        .group_by(ApiUsageLog.api_key_id)
        .all()
    )
    return {kid: n for kid, n in rows}


def subject_usage_this_month(db: Session, org_id: str) -> dict[str, int]:
    """과목별 이번 달 교육형 challenge 수 — 과목별 사용량 대시보드용. {subject: count}."""
    first = date.today().replace(day=1)
    rows = (
        db.query(ApiUsageLog.subject, func.count(ApiUsageLog.id))
        .filter(
            ApiUsageLog.organization_id == org_id,
            ApiUsageLog.created_at >= _dt(first),
            ApiUsageLog.endpoint.like("%challenge%"),
            ApiUsageLog.subject.isnot(None),
        )
        .group_by(ApiUsageLog.subject)
        .all()
    )
    return {s: n for s, n in rows}


def org_analytics_extras(db: Session, org_id: str, start: date, end: date) -> dict:
    """학급별/학년별 표 — 데이터 있는 학급만, 없으면 빈 리스트(D fallback)."""
    from app.models import ClassRoom, User

    rows = attempts(db, org_id=org_id, since=start, until=end)
    if not rows:
        return {}
    classes = db.query(ClassRoom).filter(ClassRoom.organization_id == org_id).all()
    class_by_student = {
        s.id: s.class_id
        for s in db.query(StudentProfile).filter(StudentProfile.organization_id == org_id).all()
    }
    teacher_names = {
        u.id: u.name for u in db.query(User).filter(User.organization_id == org_id).all()
    }
    span = (end - start).days
    prev_rows = attempts(db, org_id=org_id, since=start - timedelta(days=span), until=start)

    by_class: dict[str, list[LearningAttempt]] = {}
    prev_by_class: dict[str, list[LearningAttempt]] = {}
    for r in rows:
        cid = class_by_student.get(r.student_id)
        if cid:
            by_class.setdefault(cid, []).append(r)
    for r in prev_rows:
        cid = class_by_student.get(r.student_id)
        if cid:
            prev_by_class.setdefault(cid, []).append(r)

    class_rows, grade_data = [], {}
    for c in classes:
        g = by_class.get(c.id)
        if not g:
            continue
        a = _acc(g) or 0
        pa = _acc(prev_by_class.get(c.id, []))
        trend = "유지" if pa is None or abs(a - pa) < 2 else ("상승" if a > pa else "하락")
        class_rows.append(
            {
                "name": c.name,
                "teacher": teacher_names.get(c.teacher_id, "미배정"),
                "acc": a,
                "sessions": f"{len(g)}회",
                "weak": _weak_subject(g) or "-",
                "trend": trend,
            }
        )
        gd = grade_data.setdefault(c.grade, {"rows": [], "prev": [], "students": set()})
        gd["rows"].extend(g)
        gd["prev"].extend(prev_by_class.get(c.id, []))
        gd["students"].update(r.student_id for r in g)
    class_rows.sort(key=lambda r: -r["acc"])

    grade_rows = []
    for g in sorted(grade_data):
        gd = grade_data[g]
        a = _acc(gd["rows"]) or 0
        pa = _acc(gd["prev"])
        grade_rows.append(
            {
                "label": f"{g}학년",
                "pct": a,
                "delta": a - pa if pa is not None else 0,
                "students": len(gd["students"]),
            }
        )
    return {"classes": class_rows, "grades": grade_rows}


# ---------------------------------------------------------------- 학부모
def parent_week_kpis(db: Session, child: StudentProfile) -> list[dict] | None:
    """이번 주 자녀 KPI 4종 (+ 전주 대비 delta) — 시도 없으면 None.

    '평균 정답률'만은 최근 28일 창 — 자녀 목록(_child_row)·교사 우리반과 같은
    표준 창을 써서, 같은 아이의 정답률이 화면마다 다르게 보이지 않게 한다.
    """
    today = date.today()
    ws = _week_start(today)
    rows = attempts(db, student_ids=[child.id], since=today - timedelta(days=56))
    cur = [r for r in rows if r.created_at.date() >= ws]
    prev = [r for r in rows if ws - timedelta(weeks=1) <= r.created_at.date() < ws]
    if not cur:
        return None

    def _cnt_delta(c: int, p: int, unit: str) -> str:
        d = c - p
        return f"{'+' if d >= 0 else ''}{d}{unit}"

    acc28 = [r for r in rows if r.created_at.date() >= today - timedelta(days=28)]
    acc28_prev = [
        r
        for r in rows
        if today - timedelta(days=56) <= r.created_at.date() < today - timedelta(days=28)
    ]
    acc_c, acc_p = _acc(acc28) or 0, _acc(acc28_prev)
    t_c = round(sum(r.solve_time_ms for r in cur) / len(cur) / 1000)
    t_p = round(sum(r.solve_time_ms for r in prev) / len(prev) / 1000) if prev else None
    badges_c = (
        db.query(StudentBadge)
        .filter(StudentBadge.student_id == child.id, StudentBadge.earned_at >= _dt(ws))
        .count()
    )
    badges_p = (
        db.query(StudentBadge)
        .filter(
            StudentBadge.student_id == child.id,
            StudentBadge.earned_at >= _dt(ws - timedelta(weeks=1)),
            StudentBadge.earned_at < _dt(ws),
        )
        .count()
    )
    return [
        {"value": f"{len(cur)}회", "label": "이번 주 학습 횟수", "delta": _cnt_delta(len(cur), len(prev), "회")},
        {"value": f"{acc_c}%", "label": "평균 정답률", "delta": _delta_str(acc_c, acc_p)},
        {"value": f"{t_c}초", "label": "평균 풀이 시간", "delta": _cnt_delta(t_c, t_p, "초") if t_p is not None else "+0초"},
        {"value": f"{badges_c}개", "label": "이번 주 새 배지", "delta": _cnt_delta(badges_c, badges_p, "개")},
    ]


_PARENT_REASON_BODY = {
    "개념 혼동": "개념에서 헷갈린 부분이 있었어요. 함께 천천히 짚어보면 좋아요.",
    "조작 실수": "답은 알지만 터치·드래그 조작에서 실수가 있었어요. 큰 화면으로 연습해요.",
    "선택지 혼동": "비슷한 선택지 사이에서 여러 번 오갔어요. 헷갈린 것을 함께 읽어봐요.",
    "UI 문제": "화면 조작이 조금 어려웠던 것 같아요.",
}
_PARENT_REASON_ICON = {
    "개념 혼동": "ph-fill ph-lightbulb",
    "조작 실수": "ph-fill ph-hand-tap",
    "선택지 혼동": "ph-fill ph-arrows-left-right",
    "UI 문제": "ph-fill ph-cursor-click",
}


def parent_reasons(db: Session, child: StudentProfile) -> list[dict] | None:
    """자녀 최근 28일 오답의 estimated_reason 분포 → 원인 카드 (없으면 None → D 유지)."""
    rows = attempts(db, student_ids=[child.id], since=date.today() - timedelta(days=28))
    wrong = [r.estimated_reason for r in rows if r.result != "correct" and r.estimated_reason]
    if not wrong:
        return None
    out = []
    for reason, _n in Counter(wrong).most_common(3):
        out.append(
            {
                "tag": reason,
                "body": _PARENT_REASON_BODY.get(reason, f"{reason} 경향이 보였어요."),
                "icon": _PARENT_REASON_ICON.get(reason, "ph-fill ph-lightbulb"),
            }
        )
    return out


def parent_strengths_weaknesses(db: Session, child: StudentProfile) -> dict | None:
    """과목별 정답률 상/하위 3 — 최근 28일."""
    rows = attempts(db, student_ids=[child.id], since=date.today() - timedelta(days=28))
    by_sub: dict[str, list[LearningAttempt]] = {}
    for r in rows:
        by_sub.setdefault(r.subject, []).append(r)
    scored = sorted(
        ((sub, _acc(g) or 0) for sub, g in by_sub.items() if len(g) >= 3),
        key=lambda t: -t[1],
    )
    if len(scored) < 2:
        return None
    top = [{"name": s, "pct": f"{a}%"} for s, a in scored[:3]]
    bottom = [{"name": s, "pct": f"{a}%"} for s, a in sorted(scored[-3:], key=lambda t: t[1])]
    return {"strengths": top, "weaknesses": bottom}


def parent_report_overrides(
    db: Session, child: StudentProfile, subject: str | None
) -> dict:
    """kpis/bars/trend/grade/percentile/trend_delta 실집계 — 없으면 키 제외(D 유지)."""
    today = date.today()
    ws = _week_start(today)
    out: dict = {}

    kpis = parent_week_kpis(db, child)
    if kpis:
        icons = ["ph-fill ph-calendar-check", "ph-fill ph-target", "ph-fill ph-timer", "ph-fill ph-medal"]
        labels = ["학습 횟수", "평균 정답률", "평균 풀이 시간", "새 배지"]
        out["kpis"] = [
            {"icon": icons[i], "value": k["value"], "label": labels[i]} for i, k in enumerate(kpis)
        ]

    rows = attempts(db, student_ids=[child.id], since=ws - timedelta(weeks=6))
    if not rows:
        return out

    # 기간별 막대 (최근 4주 주별 정답률)
    four = [(ws - timedelta(weeks=i), ws - timedelta(weeks=i - 1)) for i in range(3, -1, -1)]
    labels = ["3주전", "2주전", "지난주", "이번주"]
    bars_acc = _acc_series(rows, four)
    out["bars"] = [{"label": labels[i], "v": bars_acc[i]} for i in range(4)]
    d = bars_acc[-1] - bars_acc[-2]
    out["trend_delta"] = f"{'+' if d >= 0 else ''}{d}%p {'상승' if d >= 0 else '하락'}"

    # 6주 추이 (자녀 vs 반 평균)
    six = [(ws - timedelta(weeks=i), ws - timedelta(weeks=i - 1)) for i in range(5, -1, -1)]
    child_rows = [r for r in rows if r.subject == subject] if subject else rows
    clamp = lambda v: max(45, min(99, v))  # noqa: E731 — 차트 y축 범위 보호 (디자인 로직)
    if child_rows:
        series = [clamp(v) for v in _acc_series(child_rows, six)]
        out["trend_series"] = series
        out["trend_avg"] = round(sum(series) / len(series))

    classmates = []
    if child.class_id:
        classmates = (
            db.query(StudentProfile)
            .filter(StudentProfile.class_id == child.class_id, StudentProfile.status != "disabled")
            .all()
        )
    if classmates:
        class_rows = attempts(
            db,
            student_ids=[s.id for s in classmates],
            since=ws - timedelta(weeks=6),
            subject=subject,
        )
        if class_rows:
            out["trend_class_series"] = [clamp(v) for v in _acc_series(class_rows, six)]

        # 반 내 정답률 순위 → 등급/상위 백분위 (최근 28일)
        rank_rows = attempts(db, student_ids=[s.id for s in classmates], since=today - timedelta(days=28))
        by_student: dict[str, list[LearningAttempt]] = {}
        for r in rank_rows:
            by_student.setdefault(r.student_id, []).append(r)
        ranked = sorted(((_acc(g) or 0, sid) for sid, g in by_student.items()), reverse=True)
        if child.id in by_student and len(ranked) >= 2:
            rank = next(i for i, (_, sid) in enumerate(ranked, start=1) if sid == child.id)
            p = max(1, round(rank / len(ranked) * 100))
            out["percentile"] = f"{p}%"
            out["grade"] = (
                "A+" if p <= 10 else "A" if p <= 20 else "B+" if p <= 35 else "B" if p <= 55 else "C+" if p <= 75 else "C"
            )
    return out
