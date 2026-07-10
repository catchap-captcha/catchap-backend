"""차트/대시보드 KPI가 learning_attempts 등 원천 테이블 실집계로 나오는지 검증.

- 학생 나의기록/홈 growth: 시도 추가 → weeks/accuracy/week_solved 변화
- POST /learning/attempts → 주간 문제 수 +1, 진도(StudentProgress) 갱신
- 교사 대시보드/분석 KPI: 학급 시도 실집계
- 학부모 요약 KPI: 자녀 주간 시도 실집계
- 기관 대시보드: api_usage_logs / behavior_summaries 실집계
- 시도 없으면 D(디자인 수치) fallback — 화면이 비지 않는다
"""

from datetime import date, datetime, time, timedelta


def _student_token(client, seed_org):
    res = client.post(
        "/api/v1/auth/student-login",
        json={
            "organization_id": seed_org["org"].id,
            "student_login_id": "stu01",
            "password": "1234",
        },
    )
    return res.json()["access_token"]


def _teacher_token(client):
    res = client.post(
        "/api/v1/auth/login",
        json={"role": "teacher", "email": "t1@test.dev", "password": "Password123!"},
    )
    return res.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def _at(days_ago: int) -> datetime:
    return datetime.combine(date.today() - timedelta(days=days_ago), time(10, 0))


def _add_attempts(db, org_id, student_id, specs):
    """specs: (subject, result, days_ago) 목록 — created_at을 명시해 결정적으로."""
    from app.models import LearningAttempt

    for subject, result, days_ago in specs:
        db.add(
            LearningAttempt(
                organization_id=org_id,
                student_id=student_id,
                subject=subject,
                chapter_no=1,
                result=result,
                score=100 if result == "correct" else 0,
                solve_time_ms=10000,
                estimated_reason=None if result == "correct" else "개념 혼동",
                created_at=_at(days_ago),
            )
        )
    db.commit()


# ---------------------------------------------------------------- 학생
def test_records_and_growth_reflect_attempts(client, db, seed_org):
    org_id, sid = seed_org["org"].id, seed_org["student"].id
    # 오늘 수학 4문제(3정답) + 지난주 국어 2문제(모두 정답)
    _add_attempts(
        db,
        org_id,
        sid,
        [("수학", "correct", 0), ("수학", "correct", 0), ("수학", "correct", 0),
         ("수학", "incorrect", 0), ("국어", "correct", 7), ("국어", "correct", 7)],
    )
    token = _student_token(client, seed_org)

    rec = client.get("/api/v1/students/me/records", headers=auth(token)).json()
    assert rec["weeks"][-1]["pct"] == 100  # 이번 주 4건 = 최대
    math = next(m for m in rec["mastery"] if m["solved"] == 4)
    assert math["pct"] == 75  # 3/4
    assert rec["accuracy_series"]["전체"]["data"][-1] == 75  # 이번 주 전체 정답률
    assert rec["calendar"]["today"] == date.today().day
    assert date.today().day in rec["calendar"]["learned"]
    assert any("4문제" in a["sub"] for a in rec["activities"])

    dash = client.get("/api/v1/students/me/dashboard", headers=auth(token)).json()
    assert dash["growth"]["week_solved"] == 4
    assert dash["growth"]["accuracy"] == 75
    assert dash["growth"]["streak_days"] >= 1

    # POST /learning/attempts → 주간 문제 수 +1 즉시 반영
    res = client.post(
        "/api/v1/learning/attempts",
        json={"subject": "국어", "result": "correct", "score": 100,
              "solve_time_ms": 8000, "completed": False},
        headers=auth(token),
    )
    assert res.status_code == 200
    dash2 = client.get("/api/v1/students/me/dashboard", headers=auth(token)).json()
    assert dash2["growth"]["week_solved"] == 5

    # 진도(StudentProgress) 보강 반영
    prog = client.get("/api/v1/students/me/progress?subject=국어", headers=auth(token)).json()
    assert prog["questions_done"] >= 1
    assert prog["accuracy"] == 100.0  # 국어 3/3 정답


def test_records_fallback_without_attempts(client, db, seed_org):
    """시도가 전혀 없으면 D(디자인 수치)로 화면 유지."""
    from app.services import design_data as raw

    token = _student_token(client, seed_org)
    rec = client.get("/api/v1/students/me/records", headers=auth(token)).json()
    assert [w["pct"] for w in rec["weeks"]] == [w["v"] for w in raw.RECORDS_WEEKS]
    dash = client.get("/api/v1/students/me/dashboard", headers=auth(token)).json()
    assert dash["growth"]["week_solved"] == raw.HOME_GROWTH["week_solved"]


def test_result_reflects_today_attempts(client, db, seed_org):
    _add_attempts(
        db, seed_org["org"].id, seed_org["student"].id,
        [("수학", "correct", 0), ("수학", "correct", 0), ("수학", "incorrect", 0)],
    )
    token = _student_token(client, seed_org)
    res = client.get("/api/v1/students/me/result?subject=수학", headers=auth(token)).json()
    assert res["correct"] == 2
    assert res["wrong"] == 1
    assert res["score"] == "+200"


def test_result_shows_only_last_session_not_daily_total(client, db, seed_org):
    """결과 화면은 오늘 누적이 아니라 '방금 끝낸 세션'만 보여준다.

    오늘 사회를 옛 세션(6문제)+최근 세션(5문제)로 나눠 풀면, result는 최근 5문제만
    집계해야 한다(correct/total이 452/5처럼 어긋나던 버그 회귀 방지).
    """
    from app.models import LearningAttempt

    org_id, sid = seed_org["org"].id, seed_org["student"].id
    base = datetime.combine(date.today(), time(10, 0))

    def add(offset_min, result):
        db.add(LearningAttempt(
            organization_id=org_id, student_id=sid, subject="사회", chapter_no=1,
            result=result, score=100 if result == "correct" else 0, solve_time_ms=0,
            created_at=base + timedelta(minutes=offset_min),
        ))

    # 옛 세션: 10:00~10:05, 6문제 전부 정답 (오늘 누적에 섞임)
    for i in range(6):
        add(i, "correct")
    # 최근 세션: 14:00~14:02, 4정답 + 마지막 1오답 (15분 이상 벌어져 별도 세션)
    recent = ["correct", "correct", "correct", "correct", "incorrect"]
    for i, r in enumerate(recent):
        add(240 + i * 0.5, r)
    db.commit()

    token = _student_token(client, seed_org)
    res = client.get("/api/v1/students/me/result?subject=사회", headers=auth(token)).json()
    # 오늘 정답은 10건이지만, 결과는 최근 세션 5건만 — 누적이면 여기서 깨진다
    assert res["total"] == 5
    assert res["correct"] == 4
    assert res["wrong"] == 1
    assert res["score"] == "+400"


# ---------------------------------------------------------------- 교사
def test_teacher_dashboard_and_analytics_reflect_attempts(client, db, seed_org):
    from app.core.security import hash_password
    from app.models import StudentProfile

    org_id = seed_org["org"].id
    low = StudentProfile(
        organization_id=org_id,
        class_id=seed_org["class"].id,
        student_login_id="stu02",
        student_code="CAT-2222",
        password_hash=hash_password("1234"),
        nickname="저조학생",
    )
    db.add(low)
    db.commit()

    # stu01: 오늘 4문제 전부 정답 / stu02: 오늘 6문제 중 1정답 (정답률 17% → 도움 필요)
    _add_attempts(db, org_id, seed_org["student"].id, [("국어", "correct", 0)] * 4)
    _add_attempts(
        db, org_id, low.id,
        [("수학", "correct", 0)] + [("수학", "incorrect", 0)] * 5,
    )

    token = _teacher_token(client)
    dash = client.get("/api/v1/teacher/dashboard", headers=auth(token)).json()
    assert dash["kpis"]["total_students"] == 2
    assert dash["kpis"]["today_done"] == 2  # 오늘 학습한 학생 수
    assert dash["kpis"]["today_done_pct"] == "100%"
    assert dash["kpis"]["avg_accuracy"] == 50  # (4+1)/10
    assert dash["kpis"]["need_help"] == 1  # 정답률 17% 학생
    today_bar = next(b for b in dash["bar_data"] if b.get("today"))
    assert today_bar["n"] == 2
    assert {"저조학생"} <= {a["name"] for a in dash["attention"]}
    assert any(a["tag"] == "개념 보강" for a in dash["attention"])

    ana = client.get("/api/v1/teacher/analytics?period=week", headers=auth(token)).json()
    assert ana["kSolved"] == "10"
    assert ana["kActive"] == "2 / 2명"
    assert ana["kAcc"] == "50"
    assert len(ana["accSeries"]) == len(ana["axis"])
    subj = {s["name"]: s for s in ana["subjects"]}
    assert subj["국어"]["total"] == 4 and subj["국어"]["pct"] == 100
    assert subj["수학"]["total"] == 6 and subj["수학"]["correct"] == 1
    assert ana["reasons"][0]["label"] == "개념 오답 추정"
    assert ana["reasons"][0]["pct"] == "100%"
    rows = {r["name"]: r for r in ana["students"]}
    assert rows["저조학생"]["acc"] == 17


# ---------------------------------------------------------------- 학부모
def test_parent_summary_kpis_reflect_attempts(client, db, seed_org):
    from datetime import datetime as dt

    from app.core.security import hash_password
    from app.models import User

    parent = User(
        email="p1@test.dev",
        password_hash=hash_password("Password123!"),
        name="테스트학부모",
        role="parent",
        email_verified_at=dt.utcnow(),
    )
    db.add(parent)
    db.commit()

    # 이번 주 6문제(4정답: 국어3 + 수학1), 지난주 2문제 — 과목 2개 이상(강/약점)
    _add_attempts(
        db, seed_org["org"].id, seed_org["student"].id,
        [("국어", "correct", 0)] * 3
        + [("수학", "correct", 0), ("수학", "incorrect", 0), ("수학", "incorrect", 0)]
        + [("국어", "correct", 7), ("국어", "correct", 7)],
    )

    res = client.post(
        "/api/v1/auth/login",
        json={"role": "parent", "email": "p1@test.dev", "password": "Password123!"},
    )
    token = res.json()["access_token"]
    sid = seed_org["student"].id
    from app.services import onboarding_service

    invite = onboarding_service.issue_parent_invite(
        db, student_id=sid, organization_id=seed_org["org"].id
    )
    client.post(
        "/api/v1/parents/me/children/link-invite",
        json={"invite_code": invite},
        headers=auth(token),
    )

    summary = client.get(f"/api/v1/parents/me/children/{sid}/summary", headers=auth(token)).json()
    kpis = {k["label"]: k for k in summary["kpis"]}
    assert kpis["이번 주 학습 횟수"]["value"] == "6회"
    assert kpis["이번 주 학습 횟수"]["delta"] == "+4회"  # 지난주 2회 대비
    # 평균 정답률은 28일 표준 창 — 자녀 목록·교사 우리반과 동일 정의 (6정답/8시도)
    assert kpis["평균 정답률"]["value"] == "75%"
    names = [s["name"] for s in summary["strengths"]]
    assert names[0] == "국어"  # 국어 100% > 수학

    report = client.get(f"/api/v1/parents/me/children/{sid}/report", headers=auth(token)).json()
    assert report["bars"][-1]["label"] == "이번주"
    assert report["bars"][-1]["v"] == 67
    assert len(report["trend"]["series"]) == 6


# ---------------------------------------------------------------- 기관
def test_org_dashboard_and_billing_reflect_usage_logs(client, db, seed_org):
    from datetime import datetime as dt

    from app.core.security import hash_password
    from app.models import ApiUsageLog, BehaviorSummary, User

    org_id = seed_org["org"].id
    admin = User(
        email="admin1@test.dev",
        password_hash=hash_password("Password123!"),
        name="테스트관리자",
        role="org_admin",
        organization_id=org_id,
        email_verified_at=dt.utcnow(),
    )
    db.add(admin)
    for i in range(5):
        db.add(
            ApiUsageLog(
                organization_id=org_id, endpoint="/captcha/verify", method="POST",
                status_code=200 if i < 4 else 403, latency_ms=120, created_at=_at(0),
            )
        )
    for risk in ["low", "low", "low", "low", "low", "low", "low", "low", "review", "elevated"]:
        db.add(
            BehaviorSummary(
                organization_id=org_id, risk_level=risk,
                interaction_result="pass" if risk == "low" else "fail",
                occurred_at=_at(0), created_at=_at(0),
            )
        )
    db.commit()

    res = client.post(
        "/api/v1/auth/login",
        json={"role": "org_admin", "email": "admin1@test.dev", "password": "Password123!"},
    )
    token = res.json()["access_token"]

    dash = client.get(f"/api/v1/orgs/{org_id}/dashboard", headers=auth(token)).json()
    assert dash["kApi"] == "5"  # 오늘 api_usage_logs 카운트
    assert dash["kPass"] == "80.0"  # 4/5 성공
    assert dash["dLow"] == 80 and dash["dReview"] == 10 and dash["dElevated"] == 10
    assert dash["site"]["calls_today"] == 5

    billing = client.get(f"/api/v1/orgs/{org_id}/billing", headers=auth(token)).json()
    assert billing["usage"]["api"]["used"] == 5


# ---------------------------------------------------------------- 신규 응답 필드 (하드코딩 제거 배치)
def test_dehardcoded_student_fields(client, db, seed_org):
    """홈 week_total / 기록 stats / 배지 recent·next / 퀴즈·추천 coins 필드 검증."""
    from app.models import Badge, StudentBadge

    org_id, sid = seed_org["org"].id, seed_org["student"].id
    _add_attempts(
        db, org_id, sid,
        [("수학", "correct", 0), ("수학", "correct", 0), ("국어", "incorrect", 0)],
    )
    b1 = Badge(name="첫 걸음", description="첫 학습", icon="i", color="#000",
               condition_text="첫 학습", order_no=0)
    b2 = Badge(name="불꽃 학습왕", description="14일 연속 학습", icon="i", color="#000",
               condition_text="14일 연속", order_no=1)
    db.add_all([b1, b2])
    db.flush()
    db.add(StudentBadge(student_id=sid, badge_id=b1.id, earned_at=_at(1), progress=1.0))
    db.add(StudentBadge(student_id=sid, badge_id=b2.id, earned_at=None, progress=12 / 14))
    db.commit()

    token = _student_token(client, seed_org)

    # 홈 growth: 주간 총 학습시간(solve_time_ms 합) + 요일별 time 라벨
    dash = client.get("/api/v1/students/me/dashboard", headers=auth(token)).json()
    assert dash["growth"]["week_total"] in ("0m", "1m")  # 3건 × 10초 실집계 (반올림)
    assert all("time" in b for b in dash["growth"]["week_bars"])

    # 나의기록 상단 통계: 전체 기간 실집계
    rec = client.get("/api/v1/students/me/records", headers=auth(token)).json()
    assert rec["stats"]["total_solved"] == 3
    assert rec["stats"]["avg_accuracy"] == 67  # 2/3
    assert rec["stats"]["streak_days"] >= 1

    # 배지: 최근 획득(recent) + 다음 배지(next, progress 최고 미획득)
    badges = client.get("/api/v1/students/me/badges", headers=auth(token)).json()
    assert badges["recent"]["name"] == "첫 걸음"
    assert badges["next"]["name"] == "불꽃 학습왕"
    # 연속 학습 배지 진행도는 저장값(12/14)이 아니라 실 streak/14 —
    # 홈·기록·데일리퀴즈와 같은 정의. 오늘 시도만 있으므로 1/14.
    assert badges["next"]["current"] == 1 and badges["next"]["total"] == 14
    assert 0 < badges["next"]["progress"] < 0.2
    assert badges["level"] == seed_org["student"].level

    # 오늘의퀴즈/추천: NAV 코인 칩 + 주간 도전 실집계
    quiz = client.get("/api/v1/students/me/daily-quiz", headers=auth(token)).json()
    assert quiz["coins"] == seed_org["student"].coins
    assert quiz["streak_days"] >= 0
    today_bar = next(w for w in quiz["week"] if w.get("today"))
    assert today_bar["done"] is True  # 오늘 시도 존재

    reco = client.get("/api/v1/students/me/recommendations", headers=auth(token)).json()
    assert "recommendations" in reco and reco["coins"] == seed_org["student"].coins
    assert isinstance(reco["summary"], str) and reco["summary"]


def test_dehardcoded_org_teacher_fields(client, db, seed_org):
    """기관 sidebar/security-stats/analytics ai_summary, 교사 analytics class_name 검증."""
    from datetime import datetime as dt

    from app.core.security import hash_password
    from app.models import User

    org_id = seed_org["org"].id
    db.add(
        User(
            email="admin1@test.dev",
            password_hash=hash_password("Password123!"),
            name="테스트관리자",
            role="org_admin",
            organization_id=org_id,
            email_verified_at=dt.utcnow(),
        )
    )
    db.commit()
    res = client.post(
        "/api/v1/auth/login",
        json={"role": "org_admin", "email": "admin1@test.dev", "password": "Password123!"},
    )
    token = res.json()["access_token"]

    side = client.get(f"/api/v1/orgs/{org_id}/sidebar", headers=auth(token)).json()
    assert {"pro", "semester", "insight"} <= set(side)
    assert "sub" in side["pro"] and "pct" in side["pro"]

    sec = client.get(f"/api/v1/orgs/{org_id}/security-stats", headers=auth(token)).json()
    assert sec["consent_rate"].endswith("%")

    ana = client.get(f"/api/v1/orgs/{org_id}/analytics", headers=auth(token)).json()
    assert {"strength", "warning", "recommend"} <= set(ana["ai_summary"])
    assert isinstance(ana["reasons"], list) and ana["reasons"]

    t = _teacher_token(client)
    ta = client.get("/api/v1/teacher/analytics", headers=auth(t)).json()
    assert ta["class_name"]
    assert {"strength", "warning", "recommend"} <= set(ta["ai_summary"])
    assert isinstance(ta["insight"], str)
