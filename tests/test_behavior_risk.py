"""행동 위험 스코어링 v2 (아동 보정) — 회귀 테스트.

0710 skeptic이 재현한 오탐 3경로(탭홀드 순간이동 오탐·직진 드래그 경로비 오탐·
탭 사이 '비행' 속도 폭등)가 다시 생기지 않는지 + 진성 봇 패턴이 잡히는지 고정한다.
"""

from app.services.captcha_service import _behavior_risk_level, _trace_metrics


def _trace(points, box_w=400, box_h=600):
    return {"points": points, "box_w": box_w, "box_h": box_h}


def _risk(trace, solve_ms, correct=True):
    m = _trace_metrics(trace) if trace else None
    return _behavior_risk_level(
        solve_time_ms=solve_ms, correct=correct, trace=trace, metrics=m, input_type="touch"
    )


# ---- 진성 봇 패턴 (elevated) ----

def test_machine_straight_line_instant_answer_is_elevated():
    """기계 직선(떨림 0) + 800ms 미만 즉답 = 강신호 2 → elevated"""
    pts = [[i * 50, 0.1 + 0.08 * i, 0.1 + 0.08 * i] for i in range(10)]  # 완전 직선
    assert _risk(_trace(pts), solve_ms=500) == "elevated"


def test_teleport_with_instant_answer_is_elevated():
    """점 2개·큰 변위·500ms 경과(순간이동) + 즉답 → elevated"""
    pts = [[0, 0.1, 0.1], [700, 0.9, 0.9]]
    assert _risk(_trace(pts), solve_ms=600) == "elevated"


def test_superspeed_flagged():
    """이동 구간 평균 속도 10px/ms 초과 = 강신호 (단독이면 review)"""
    # 12ms 간격으로 정규화 0.3(=120px)씩 — 10px/ms 초과. 지그재그로 직선 신호는 회피.
    pts = [[0, 0.05, 0.05], [12, 0.35, 0.10], [24, 0.65, 0.45], [36, 0.95, 0.50]]
    level = _risk(_trace(pts), solve_ms=5000, correct=False)  # 즉답 신호 없이 속도만
    assert level == "review"


# ---- skeptic 오탐 3경로 회귀 (아이의 정상 입력 = low) ----

def test_tap_hold_is_not_teleport():
    """지긋한 탭홀드(점 2개·변위 0·450ms)는 순간이동이 아니다 — v1 오탐 회귀"""
    pts = [[0, 0.5, 0.5], [450, 0.5, 0.5]]
    assert _risk(_trace(pts), solve_ms=4000, correct=False) == "low"


def test_confident_child_drag_with_jitter_is_low():
    """자신 있는 직진 드래그라도 픽셀 떨림(±11px)이 있으면 사람 — 경로비 방식 오탐 회귀"""
    # 직선 경로에 수직 방향 ±11px(정규화 11/400=0.0275) 떨림
    pts = []
    for i in range(10):
        f = i / 9
        jitter = 0.0275 if i % 2 else -0.0275
        pts.append([i * 60, 0.1 + 0.8 * f, 0.1 + 0.8 * f + jitter])
    assert _risk(_trace(pts), solve_ms=4000, correct=False) == "low"


def test_two_taps_far_apart_do_not_inflate_speed():
    """멀리 떨어진 두 탭 사이 '비행'은 gap이라 avg_speed에서 제외 — 속도 폭등 오탐 회귀"""
    # 탭1(0~100ms 정지) → 1초 gap → 탭2(반대편). gap 구간 거리가 분자에 남으면 속도 폭발.
    pts = [[0, 0.1, 0.1], [100, 0.1, 0.1], [1100, 0.9, 0.9], [1200, 0.9, 0.9]]
    m = _trace_metrics(_trace(pts))
    assert m["avg_speed"] < 1.0, f"gap 거리가 속도에 새면 안 됨: {m['avg_speed']}"
    assert _risk(_trace(pts), solve_ms=4000, correct=False) == "low"


# ---- 아동 보정 대원칙 ----

def test_slow_wrong_child_is_low():
    """느림·오답·멈춤 많음은 위험이 아니다 (아이의 증거)"""
    pts = [[0, 0.2, 0.2], [400, 0.3, 0.35], [1200, 0.45, 0.5], [2500, 0.6, 0.62], [4200, 0.8, 0.85]]
    assert _risk(_trace(pts), solve_ms=9000, correct=False) == "low"


def test_fast_but_possible_answer_is_review_not_elevated():
    """800~1500ms 정답은 약신호 → review까지만 (오탐이 미탐보다 비싸다)"""
    assert _risk(None, solve_ms=1200, correct=True) == "review"


# ---- 적재 통합: record_behavior_event가 risk_level/sample_label을 실제로 박는지 ----

def test_record_behavior_event_sets_risk_and_label(db, seed_org):
    from app.models import BehaviorSummary
    from app.services.captcha_service import record_behavior_event

    org = seed_org["org"]
    bot_behavior = {
        "solve_time_ms": 400,
        "input_type": "mouse",
        "trace": [[i * 40, round(0.1 + 0.08 * i, 4), round(0.1 + 0.08 * i, 4)] for i in range(10)],
        "box": {"w": 400, "h": 600},
    }
    record_behavior_event(
        db, organization_id=org.id, student_id=None, source_type="edu-api",
        behavior=bot_behavior, correct=True, sample_label="bot",
    )
    db.commit()
    row = db.query(BehaviorSummary).order_by(BehaviorSummary.created_at.desc()).first()
    assert row.risk_level == "elevated"
    assert row.sample_label == "bot"


def test_redteam_injects_bots_into_sentinel_org(db, seed_org):
    from app.models import BehaviorSummary
    from app.services.redteam import REDTEAM_ORG_ID, inject_bot_behaviors

    created = inject_bot_behaviors(db, 20, seed=42)
    assert created == 20
    rows = db.query(BehaviorSummary).filter(
        BehaviorSummary.organization_id == REDTEAM_ORG_ID
    ).all()
    assert len(rows) == 20
    assert all(r.sample_label == "bot" for r in rows)
    # 봇다운 패턴이므로 대부분 review 이상으로 잡혀야 한다 (음성 클래스의 의미)
    flagged = [r for r in rows if r.risk_level in ("review", "elevated")]
    assert len(flagged) >= 15, f"봇 20건 중 {len(flagged)}건만 감지 — 스코어링 회귀 의심"


def test_verified_student_attribution_first_party(db, seed_org):
    """인앱(1st-party) 인증 학생은 키 기관이 달라도 본인·학교 기관으로 귀속 — 전부 익명이던 버그 회귀"""
    from app.models import ApiKey, BehaviorSummary, Organization, Site
    from app.services.captcha_service import record_behavior

    student = seed_org["student"]
    platform = Organization(name="CatChap", code="TS-CAT-9000", org_type="플랫폼")
    db.add(platform)
    db.flush()
    site = Site(organization_id=platform.id, name="inapp", domain="app.catchap.dev")
    db.add(site)
    db.flush()
    key = ApiKey(
        organization_id=platform.id, site_id=site.id, product="edu", subject="math",
        site_key="sk_test_fp", secret_key_hash="x", first_party=True,
    )
    db.add(key)
    db.commit()

    record_behavior(db, key, {"solve_time_ms": 4000}, True, verified_student=student)
    db.commit()
    row = db.query(BehaviorSummary).order_by(BehaviorSummary.created_at.desc()).first()
    assert row.student_id == student.id  # 익명이 아니라 본인 귀속
    assert row.organization_id == student.organization_id  # 키 기관(CatChap)이 아니라 학생의 학교

    # 자기신고 경로(3rd-party 위조 방지)는 그대로: 키 기관 불일치 student_id는 익명 처리
    record_behavior(db, key, {"solve_time_ms": 4000, "student_id": student.id}, True)
    db.commit()
    row2 = db.query(BehaviorSummary).order_by(BehaviorSummary.created_at.desc()).first()
    assert row2.student_id is None


def test_bot_label_is_immutable(client, db, seed_org):
    """bot 라벨은 확정 — 어떤 값으로도 재라벨 불가(locked), 감사에도 근거가 남는다"""
    from app.models import BehaviorSummary, User
    from app.core.security import hash_password
    from app.services.captcha_service import record_behavior_event

    ops = User(
        email="ops@test.dev", password_hash=hash_password("Password123!"),
        name="운영자", role="ops",
        email_verified_at=__import__("datetime").datetime.utcnow(),
    )
    db.add(ops)
    org = seed_org["org"]
    record_behavior_event(db, organization_id=org.id, student_id=None, source_type="edu-api",
                          behavior={"solve_time_ms": 5000}, correct=True, sample_label="bot")
    record_behavior_event(db, organization_id=org.id, student_id=None, source_type="edu-api",
                          behavior={"solve_time_ms": 5000}, correct=True)  # organic
    db.commit()
    rows = db.query(BehaviorSummary).all()
    bot_id = next(r.id for r in rows if r.sample_label == "bot")
    org_id = next(r.id for r in rows if r.sample_label == "organic")

    res = client.post(
        "/api/v1/auth/ops-login",
        json={"email": "ops@test.dev", "password": "Password123!"},
    )
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # bot→human 시도: 거부(locked), organic→human은 성공
    r = client.patch(
        "/api/v1/ops/behavior/records/label",
        json={"ids": [bot_id, org_id], "sample_label": "human"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["locked"] == 1 and body["changed"] == 1
    db.expire_all()
    assert db.get(BehaviorSummary, bot_id).sample_label == "bot"  # 불변
    assert db.get(BehaviorSummary, org_id).sample_label == "human"

    # bot→organic(미검증 되돌리기)도 거부
    r2 = client.patch(
        "/api/v1/ops/behavior/records/label",
        json={"ids": [bot_id], "sample_label": "organic"},
        headers=headers,
    )
    assert r2.json()["locked"] == 1 and r2.json()["changed"] == 0
    db.expire_all()
    assert db.get(BehaviorSummary, bot_id).sample_label == "bot"
