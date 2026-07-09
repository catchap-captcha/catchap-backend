"""개발용 seed 데이터 — `python -m app.db.seed` 로 실행.

- 테이블은 alembic 이 이미 만들었다고 가정한다.
- 멱등: 햇살초(HS-EDU-2041)가 이미 있으면 skip.
- 개인정보 없음: 디자인 속 가명/가짜 도메인만 사용.

개발용 계정:
  admin@catchap.dev   / Password123!  (org_admin=교장, 햇살초 김서연)
  gradehead@catchap.dev / Password123!  (grade_head=학년부장, 1학년 담당 한지원)
  teacher@catchap.dev / Password123!  (교사 이수진, 1-2반 담임, 코드 T-4821)
  parent@catchap.dev  / Password123!  (학부모 김서연, 자녀 하은·도윤 연결)
  ops@catchap.dev     / Password123!  (운영자)
  학생 로그인: 햇살초 + student01/1234 (하은) · student02/1234 (도윤)
"""

import random
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.core.security import generate_token, hash_password, sha256_hash
from app.db.session import SessionLocal
from app.models import (
    ApiKey,
    Badge,
    CaptchaSetting,
    Chapter,
    ClassRoom,
    CoinTransaction,
    ConceptRead,
    Content,
    DailyQuizStatus,
    FamilyMessage,
    Institution,
    Invoice,
    LearningAttempt,
    LearningSummary,
    Membership,
    ModelVersion,
    Notification,
    Organization,
    ParentStudentLink,
    PaymentMethod,
    Plan,
    Recommendation,
    Report,
    ShopItem,
    Site,
    StudentBadge,
    StudentItem,
    StudentProfile,
    StudentProgress,
    Subscription,
    User,
    WrongAnswer,
)
from app.services import design_data as D
from app.utils.helpers import status_key

ORG_CODE = "HS-EDU-2041"
DEV_PASSWORD = "Password123!"
STUDENT_PASSWORD = "1234"


def seed() -> None:
    # 프로덕션에는 알려진 비밀번호의 개발 계정(admin/ops/teacher/parent, 학생 1234)을
    # 절대 넣지 않는다 — ENV가 dev가 아니면 시딩 거부.
    from app.core.config import get_settings

    if get_settings().is_production:
        raise RuntimeError(
            "프로덕션 환경에서는 seed를 실행할 수 없습니다 (개발용 계정 유입 방지). ENV를 확인하세요."
        )
    db: Session = SessionLocal()
    try:
        # 대시보드/차트 수치 blob은 항상 보충 (없는 key만 추가 — 멱등)
        from app.services.stats import seed_stat_blobs

        added = seed_stat_blobs(db)
        if added:
            print(f"seed: stat_blobs {added}개 key 추가")

        org = db.query(Organization).filter(Organization.code == ORG_CODE).first()
        if org is not None:
            print(f"seed: 기본 seed 이미 있음 (org {ORG_CODE}) — skip")
        else:
            _seed(db)
            db.commit()
            org = db.query(Organization).filter(Organization.code == ORG_CODE).first()
            print("seed: 기본 seed 완료")
            print("  admin@catchap.dev / teacher@catchap.dev / parent@catchap.dev / ops@catchap.dev — Password123!")
            print("  학생: 햇살초 + student01/1234 (하은), student02/1234 (도윤)")

        # 실집계용 히스토리 (자체 멱등 체크 — 기존 seed 위에도 추가 가능)
        _seed_history(db, org)
        db.commit()
    finally:
        db.close()


# ================================================================ 히스토리 seed
# 과목별 목표 정답률 (디자인 수치 근사) — 학생별 편차는 acc 오프셋으로 반영
_SUBJECT_TARGET_ACC = {"국어": 94, "영어": 82, "수학": 71, "과학": 90, "사회": 85, "생활": 88}
_REASONS = ["개념 혼동", "조작 실수", "선택지 혼동", "UI 문제"]
_REASON_W = [41, 27, 20, 12]
_WEEKDAY_W = [1.0, 1.1, 0.9, 1.2, 1.15, 0.5, 0.35]  # 월~일 (주말 저조)


def _seed_history(db: Session, org: Organization | None) -> None:
    """과거 6주 learning_attempts + behavior_summaries + api_usage_logs 벌크 생성.

    결정적(random seed 고정)·멱등(이미 충분히 있으면 각 단계 skip).
    """
    if org is None:
        return
    from app.core.security import new_uuid
    from app.models import ApiUsageLog, BehaviorSummary, LearningAttempt

    rng = random.Random(20260703)
    today = date.today()

    def _ts(day: date) -> datetime:
        return datetime.combine(day, time(rng.randint(9, 19), rng.randint(0, 59), rng.randint(0, 59)))

    # ---------------- learning_attempts (1-2반 8명 × 6과목 × 6주) ----------------
    existing = (
        db.query(LearningAttempt).filter(LearningAttempt.organization_id == org.id).count()
    )
    if existing >= 500:
        print(f"seed: learning_attempts 이미 {existing}건 — skip")
    else:
        codes = [s["code"] for s in D.MY_CLASS_STUDENTS]
        design = {s["code"]: s for s in D.MY_CLASS_STUDENTS}
        students = (
            db.query(StudentProfile)
            .filter(
                StudentProfile.organization_id == org.id,
                StudentProfile.student_code.in_(codes),
            )
            .all()
        )
        w_sum = sum(_WEEKDAY_W)
        mappings: list[dict] = []
        for s in sorted(students, key=lambda x: x.student_code):
            meta = design[s.student_code]
            weekly = meta["solved"]  # 디자인의 '주간 푼 문제 수'
            offset = round((meta["acc"] - 86) * 0.6)  # 학생별 정답률 편차
            slacker = s.student_code == "CAT-6042"  # 최서아: 학습 뜸함 (최근 무학습)
            star = s.student_code == "CAT-4823"  # 하은: 주 86문제·92%·연속 12일
            for back in range(42, -1, -1):
                day = today - timedelta(days=back)
                if slacker and back < 4:
                    continue  # 최근 3일+오늘 학습 없음 → streak 0
                if star and back == 12:
                    continue  # 12일 전 하루 쉼 → 연속 학습 12일 (디자인 수치)
                base = weekly * _WEEKDAY_W[day.weekday()] / w_sum
                n = max(0, round(rng.gauss(base, base * 0.25)))
                if star and back < 12:
                    n = max(n, 3)  # 연속 12일 보장
                if back == 0:
                    n = max(1, n // 2) if not slacker else 0  # 오늘은 진행 중(절반)
                for _ in range(n):
                    sub = rng.choice(D.SUBJECT_ORDER)
                    target = max(35, min(99, _SUBJECT_TARGET_ACC[sub] + offset))
                    correct = rng.random() * 100 < target
                    solve_ms = int(
                        max(3000, min(40000, rng.gauss(12000 + (86 - meta["acc"]) * 150, 3000)))
                    )
                    mappings.append(
                        {
                            "id": new_uuid(),
                            "organization_id": org.id,
                            "student_id": s.id,
                            "subject": sub,
                            "chapter_no": rng.randint(1, 5),
                            "content_id": None,
                            "result": "correct" if correct else "incorrect",
                            "score": rng.choice([80, 90, 100]) if correct else 0,
                            "solve_time_ms": solve_ms,
                            "retry_count": 0 if correct else rng.randint(0, 2),
                            "estimated_reason": None
                            if correct
                            else rng.choices(_REASONS, weights=_REASON_W)[0],
                            "created_at": _ts(day),
                        }
                    )
        db.bulk_insert_mappings(LearningAttempt, mappings)
        print(f"seed: learning_attempts {len(mappings)}건 생성 (6주치, 8명×6과목)")

    # ---------------- behavior_summaries (위험신호 분포 90/8/2) ----------------
    existing_b = (
        db.query(BehaviorSummary).filter(BehaviorSummary.organization_id == org.id).count()
    )
    if existing_b >= 20:
        print(f"seed: behavior_summaries 이미 {existing_b}건 — skip")
    else:
        student_ids = [
            s.id
            for s in db.query(StudentProfile)
            .filter(StudentProfile.organization_id == org.id, StudentProfile.class_id.isnot(None))
            .all()
        ]
        b_mappings = []
        for _ in range(80):
            day = today - timedelta(days=rng.randint(0, 41))
            occurred = _ts(day)
            risk = rng.choices(["low", "review", "elevated"], weights=[90, 8, 2])[0]
            b_mappings.append(
                {
                    "id": new_uuid(),
                    "organization_id": org.id,
                    "student_id": rng.choice(student_ids) if student_ids else None,
                    "source_type": "game",
                    "solve_time_ms": rng.randint(8000, 20000),
                    "path_length": round(rng.uniform(200, 900), 1),
                    "avg_speed": round(rng.uniform(0.4, 2.2), 2),
                    "pause_count": rng.randint(0, 4),
                    "retry_count": rng.randint(0, 2),
                    "drop_distance_norm": round(rng.uniform(0, 0.4), 3),
                    "interaction_result": "fail" if rng.random() < 0.08 else "pass",
                    "risk_level": risk,
                    "occurred_at": occurred,
                    "created_at": occurred,
                }
            )
        db.bulk_insert_mappings(BehaviorSummary, b_mappings)
        print(f"seed: behavior_summaries {len(b_mappings)}건 생성")

    # ---------------- api_usage_logs (~34,000건 벌크) ----------------
    existing_a = db.query(ApiUsageLog).filter(ApiUsageLog.organization_id == org.id).count()
    if existing_a >= 5000:
        print(f"seed: api_usage_logs 이미 {existing_a}건 — skip")
    else:
        total_target = 34000
        day_weights = [
            _WEEKDAY_W[(today - timedelta(days=back)).weekday()] * (1 + (42 - back) * 0.01)
            for back in range(42, -1, -1)
        ]
        w_total = sum(day_weights)
        a_mappings = []
        for i, back in enumerate(range(42, -1, -1)):
            day = today - timedelta(days=back)
            n = round(total_target * day_weights[i] / w_total)
            for _ in range(n):
                r = rng.random()
                status_code = 200 if r < 0.93 else (403 if r < 0.97 else (400 if r < 0.99 else 500))
                a_mappings.append(
                    {
                        "id": new_uuid(),
                        "organization_id": org.id,
                        "site_id": None,
                        "endpoint": rng.choice(["/captcha/challenge", "/captcha/verify"]),
                        "method": "POST",
                        "status_code": status_code,
                        "latency_ms": rng.randint(80, 260),
                        "created_at": _ts(day),
                    }
                )
        for i in range(0, len(a_mappings), 5000):
            db.bulk_insert_mappings(ApiUsageLog, a_mappings[i : i + 5000])
        print(f"seed: api_usage_logs {len(a_mappings)}건 생성 (43일 분포)")


def _seed(db: Session) -> None:  # noqa: PLR0915
    dev_hash = hash_password(DEV_PASSWORD)
    student_hash = hash_password(STUDENT_PASSWORD)
    unusable_hash = hash_password(generate_token()[:32])
    today = date.today()
    now = datetime.utcnow()

    # ---------------- 기관 + Institution 디렉토리 ----------------
    org = Organization(
        name="햇살초등학교",
        code=ORG_CODE,
        org_type="초등학교",
        status="active",
        contact_email="admin@catchap.dev",
        contact_phone="02-1234-5678",
        address="서울특별시 강서구 화곡로 123",
        business_number="123-45-67890",
        code_expires_at=datetime(2026, 12, 30),
    )
    db.add(org)
    db.flush()

    for inst in D.INSTITUTIONS:
        db.add(
            Institution(
                name=inst["name"],
                inst_type=inst["type"],
                sido=inst["sido"],
                sigungu=inst["sigungu"],
                dong=inst["dong"],
                road_address=inst["road"],
                organization_id=org.id if inst["name"] == "햇살초등학교" else None,
            )
        )

    # ---------------- 계정 ----------------
    admin = User(email="admin@catchap.dev", password_hash=dev_hash, name="김서연",
                 phone="02-1234-5678", role="org_admin", status="active",
                 email_verified_at=now, organization_id=org.id)
    parent = User(email="parent@catchap.dev", password_hash=dev_hash, name="김서연",
                  phone="010-2345-6789", role="parent", status="active", email_verified_at=now)
    ops = User(email="ops@catchap.dev", password_hash=dev_hash, name="운영자",
               role="ops", status="active", email_verified_at=now)
    db.add_all([admin, parent, ops])

    # 기관 관리자 3명 (기관 마이페이지 화면) — 나머지 2명은 로그인 불가 placeholder
    for extra in D.ORG_ADMINS[1:]:
        db.add(User(email=extra["email"], password_hash=unusable_hash, name=extra["name"],
                    role="org_admin", status="active", email_verified_at=now, organization_id=org.id))
    db.flush()
    db.add(Membership(user_id=admin.id, organization_id=org.id, role="org_admin",
                      status="active", joined_at=now))

    # ---------------- 교사 6명 (선생님관리 화면) ----------------
    teacher_users: dict[str, User] = {}
    for t in D.ORG_TEACHERS:
        if t["name"] == "이수진":
            u = User(email="teacher@catchap.dev", password_hash=dev_hash, name="이수진",
                     phone="010-3456-7890", role="teacher", status="active",
                     email_verified_at=now, organization_id=org.id)
        else:
            u = User(email=t["email"], password_hash=unusable_hash, name=t["name"],
                     role="teacher", status="active" if t["status"] == "active" else "pending",
                     email_verified_at=now if t["status"] == "active" else None,
                     organization_id=org.id)
        db.add(u)
        teacher_users[t["name"]] = u
    db.flush()
    for t in D.ORG_TEACHERS:
        db.add(
            Membership(
                user_id=teacher_users[t["name"]].id,
                organization_id=org.id,
                role="teacher",
                status=t["status"],
                teacher_code=t["code"],
                position=t["role"],
                career_years=t["years"],
                joined_at=now,
            )
        )

    # ---------------- 학급 9개 (학급학생관리 화면) ----------------
    classes: dict[str, ClassRoom] = {}
    for c in D.ORG_CLASSES:
        grade = int(c["key"].split("-")[0])
        # 1-3반은 이수진 '수학 전담' (선생님 마이페이지 담당학급 2개)
        if c["name"] == "1-2반" or c["name"] == "1-3반":
            teacher_id = teacher_users["이수진"].id
        else:
            teacher_id = teacher_users[c["teacher"]].id if c["teacher"] in teacher_users else None
        cls = ClassRoom(organization_id=org.id, name=c["name"], grade=grade,
                        age_group="초등 저학년", teacher_id=teacher_id, status="active")
        db.add(cls)
        classes[c["name"]] = cls
    db.flush()

    # ---------------- 학년부장 데모 (1학년 담당) ----------------
    # 교장(admin) 아래 중간 관리자: 1학년 반/교사 배정·수정 권한만 (전 학년 아님)
    grade_head = User(
        email="gradehead@catchap.dev", password_hash=dev_hash, name="한지원",
        role="grade_head", status="active", email_verified_at=now, organization_id=org.id,
    )
    db.add(grade_head)
    db.flush()
    db.add(
        Membership(
            user_id=grade_head.id, organization_id=org.id, role="grade_head",
            status="active", teacher_code="T-1001", position="담임",
            managed_grade=1, career_years=10, joined_at=now,
        )
    )
    db.flush()

    # ---------------- 학생: 우리반 8명 ----------------
    students: dict[str, StudentProfile] = {}  # code -> profile
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    for s in D.MY_CLASS_STUDENTS:
        profile = StudentProfile(
            organization_id=org.id,
            class_id=classes["1-2반"].id,
            student_login_id=s["login"],
            student_code=s["code"],
            password_hash=student_hash,
            nickname=s["name"][1:] if s["code"] in ("CAT-4823", "CAT-5119") else s["name"],
            real_name=s["name"],  # 학교용 실명 (교사·기관 화면 전용)
            age=s["age"],
            grade_band="elementary_low",
            coins=340 if s["code"] == "CAT-4823" else 120,
            level=7 if s["code"] == "CAT-4823" else 3,
            status=status_key(s["status"]),
            avatar={"hat": "cap", "background": "peach", "sticker": "star"}
            if s["code"] == "CAT-4823"
            else {},
            last_login_at=now,
        )
        db.add(profile)
        students[s["code"]] = profile
    db.flush()
    haeun = students["CAT-4823"]
    doyun = students["CAT-5119"]

    for s in D.MY_CLASS_STUDENTS:
        profile = students[s["code"]]
        db.add(
            LearningSummary(
                organization_id=org.id,
                student_id=profile.id,
                period_type="week",
                period_start=week_start,
                period_end=week_end,
                total_count=s["solved"],
                correct_count=round(s["solved"] * s["acc"] / 100),
                average_solve_time_ms=12000,
                streak_days=s["streak"],
                strength_tags={"tags": ["한글 낱말 찾기", "그림 찾기 퀴즈"]},
                need_practice_tags={"tags": ["숫자 놀이터"]},
                detail={"today": s["today"]},
            )
        )

    # 학생 코드 연동 데모 디렉토리 4명 (아직 학급 미배정)
    for i, d in enumerate(D.CLASS_DIRECTORY, start=11):
        db.add(
            StudentProfile(
                organization_id=org.id,
                class_id=None,
                student_login_id=f"student{i}",
                student_code=d["code"],
                password_hash=student_hash,
                nickname=d["name"],
                age=d["age"],
                grade_band="elementary_low",
                status="good",
            )
        )

    # ---------------- roster 24명 (전체학생조회 화면) ----------------
    for i, r in enumerate(D.ROSTER, start=1):
        profile = StudentProfile(
            organization_id=org.id,
            class_id=None,  # 표시 학급은 detail.roster 메타 (우리반 8명과 분리)
            student_login_id=f"roster{i:02d}",
            student_code=f"CAT-9{100 + i}",
            password_hash=student_hash,
            nickname=r["name"],
            age=7,
            grade_band="elementary_low",
            status=status_key(r["status"]),
        )
        db.add(profile)
        db.flush()
        solved = int(r["sessions"].replace("회", "")) * 5
        db.add(
            LearningSummary(
                organization_id=org.id,
                student_id=profile.id,
                period_type="week",
                period_start=week_start,
                period_end=week_end,
                total_count=solved,
                correct_count=round(solved * r["acc"] / 100),
                streak_days=0,
                detail={"roster": True, "g": r["g"], "c": r["c"],
                        "sessions": r["sessions"], "weak": r["weak"]},
            )
        )

    # ---------------- 학부모-자녀 연결 ----------------
    db.add(ParentStudentLink(parent_user_id=parent.id, student_id=haeun.id,
                             organization_id=org.id, status="approved",
                             requested_at=now, approved_at=now,
                             daily_goal=5, time_limit_enabled=True))
    db.add(ParentStudentLink(parent_user_id=parent.id, student_id=doyun.id,
                             organization_id=org.id, status="approved",
                             requested_at=now, approved_at=now,
                             daily_goal=5, time_limit_enabled=False))

    # ---------------- 챕터 6과목 x 5 (개념 포함) ----------------
    chapters: dict[tuple[str, int], Chapter] = {}
    for subject in D.SUBJECT_ORDER:
        for i, (name, count) in enumerate(D.CHAPTERS[subject], start=1):
            concept = D.CONCEPTS[subject][i - 1]
            ch = Chapter(subject=subject, order_no=i, name=name, total_questions=count,
                         concept={"name": name, **concept}, status="active")
            db.add(ch)
            chapters[(subject, i)] = ch
    db.flush()

    # 개념 읽음 기본값 (개념설명 DEFAULT_READ)
    for cid in ["국어-1", "국어-2", "영어-1", "수학-1", "수학-2"]:
        subject, no = cid.split("-")
        db.add(ConceptRead(student_id=haeun.id, chapter_id=chapters[(subject, int(no))].id))

    # ---------------- 하은 진도/기록 ----------------
    acc_last = {k: v["data"][-1] for k, v in D.RECORD_ACC_SERIES.items() if k != "전체"}
    for subject in D.SUBJECT_ORDER:
        done = D.DEFAULT_DONE[subject]
        db.add(
            StudentProgress(
                organization_id=org.id,
                student_id=haeun.id,
                subject=subject,
                chapters_done=done,
                current_chapter=min(5, done + 1),
                questions_done=done * 5,
                accuracy=float(acc_last.get(subject, 80)),
            )
        )
    # 도윤 진도 (조금 낮게)
    for subject in D.SUBJECT_ORDER:
        db.add(
            StudentProgress(
                organization_id=org.id,
                student_id=doyun.id,
                subject=subject,
                chapters_done=max(0, D.DEFAULT_DONE[subject] - 1),
                current_chapter=max(1, D.DEFAULT_DONE[subject]),
                questions_done=max(0, D.DEFAULT_DONE[subject] - 1) * 5,
                accuracy=float(max(50, acc_last.get(subject, 80) - 15)),
            )
        )

    # 최근 학습 시도 몇 건 (주간 통계 근거 수준)
    for i, subject in enumerate(["국어", "수학", "과학", "국어", "수학", "생활"]):
        db.add(
            LearningAttempt(
                organization_id=org.id,
                student_id=haeun.id,
                subject=subject,
                chapter_no=(i % 3) + 1,
                result="correct" if i % 3 != 2 else "incorrect",
                score=100 if i % 3 != 2 else 0,
                solve_time_ms=9000 + i * 800,
                estimated_reason=None if i % 3 != 2 else "개념 혼동",
            )
        )

    # ---------------- 배지 12 + 하은 상태 ----------------
    earned_dates = {
        "첫 걸음": datetime(2026, 6, 12), "매의 눈": now, "한글 박사": datetime(2026, 6, 28),
        "계산 왕": datetime(2026, 6, 25), "드래그 마스터": datetime(2026, 6, 30),
        "꾸준왕": datetime(2026, 6, 20), "하트 부자": datetime(2026, 6, 22),
        "별 수집가": now - timedelta(days=1),
    }
    for i, b in enumerate(D.BADGES):
        badge = Badge(name=b["name"], description=b["desc"], icon=b["icon"], color=b["color"],
                      condition_text=b["desc"], order_no=i)
        db.add(badge)
        db.flush()
        state = D.BADGE_STATE[b["name"]]
        db.add(
            StudentBadge(
                student_id=haeun.id,
                badge_id=badge.id,
                earned_at=earned_dates.get(b["name"]) if state["earned"] else None,
                progress=1.0 if state["earned"] else float(state.get("progress", 0.0)),
            )
        )

    # ---------------- 상점 24 + 하은 보유 ----------------
    owned_keys = {"hat": ["none", "cap"], "bg": ["peach", "sky"], "sticker": ["none", "star"]}
    for category, items in D.SHOP_CATALOG.items():
        for i, item in enumerate(items):
            row = ShopItem(category=category, name=item["name"], icon=item["icon"],
                           price=item["price"], order_no=i)
            db.add(row)
            db.flush()
            if item["key"] in owned_keys.get(category, []):
                db.add(StudentItem(student_id=haeun.id, item_id=row.id))
    db.add(CoinTransaction(student_id=haeun.id, amount=340, reason="누적 학습 보상"))

    # ---------------- 검색 콘텐츠 14 ----------------
    for item in D.SEARCH_ITEMS:
        db.add(
            Content(
                organization_id=None,
                title=item["title"],
                description=f"{item['desc']}\n{item['kw']}",
                category=item["tag"],
                subject=item["subject"],
                difficulty=1,
                age_group="elementary_low",
                icon=item["icon"],
                route_hint=item["href"],
                status="active",
            )
        )

    # ---------------- 오답노트 6 ----------------
    cat_label = {"word": "word", "num": "num", "img": "img", "safe": "safe"}
    for w in D.WRONG_ITEMS:
        db.add(
            WrongAnswer(
                student_id=haeun.id,
                organization_id=org.id,
                subject=w["subject"],
                category=cat_label[w["cat"]],
                question=w["question"],
                my_answer=w["wrong"],
                correct_answer=w["answer"],
                tip=w["tip"],
                reviewed=False,
                wrong_date=today - timedelta(days=w["days_ago"]),
            )
        )

    # ---------------- 추천 6 ----------------
    for r in D.RECOMMENDATIONS:
        db.add(
            Recommendation(
                student_id=haeun.id,
                subject=r["subject"],
                chapter_no=r["chapter"],
                priority=r["priority"],
                reason=r["reason"],
                status="active",
            )
        )

    # ---------------- 오늘의퀴즈 (오늘, 6과목) ----------------
    for q in D.DAILY_QUIZ:
        db.add(
            DailyQuizStatus(
                student_id=haeun.id,
                quiz_date=today,
                subject=q["subject"],
                topic=q["topic"],
                status=q["status"],
                reward_coins=q["reward"],
            )
        )

    # ---------------- 알림 (학생/학부모 화면 데이터) ----------------
    for i, n in enumerate(D.STUDENT_NOTIFICATIONS):
        db.add(
            Notification(
                student_id=haeun.id,
                organization_id=org.id,
                type=n["type"],
                category=n["category"],
                title=n["title"],
                message=n["message"],
                read_at=None if n["unread"] else now - timedelta(hours=1),
                created_at=now - timedelta(hours=i * 6),
            )
        )
    child_by_name = {"하은": haeun.id, "도윤": doyun.id}
    for i, n in enumerate(D.PARENT_NOTIFICATIONS):
        db.add(
            Notification(
                user_id=parent.id,
                organization_id=org.id,
                type=n["type"],
                category=n["category"],
                title=n["title"],
                message=n["message"],
                child_id=child_by_name.get(n["child"]),
                read_at=None if n["unread"] else now - timedelta(hours=1),
                created_at=now - timedelta(hours=i * 7),
            )
        )

    # ---------------- 가정안내 발송 이력 ----------------
    db.add(FamilyMessage(organization_id=org.id, teacher_id=teacher_users["이수진"].id,
                         student_id=haeun.id, status="read", read_at=now,
                         message="하은이가 오늘 숫자 놀이터를 끝까지 잘 해냈어요. 집에서도 칭찬 많이 해주세요!",
                         created_at=now - timedelta(hours=1)))
    db.add(FamilyMessage(organization_id=org.id, teacher_id=teacher_users["이수진"].id,
                         student_id=students["CAT-6402"].id, status="sent",
                         message="내일 준비물을 안내드려요: 색연필, 가위, 풀",
                         created_at=now - timedelta(days=1)))

    # ---------------- 리포트 ----------------
    db.add(Report(organization_id=org.id, student_id=haeun.id, report_type="weekly",
                  period_start=datetime(2026, 6, 22), period_end=datetime(2026, 6, 28),
                  status="ready", file_url=None))
    db.add(Report(organization_id=org.id, student_id=doyun.id, report_type="weekly",
                  period_start=datetime(2026, 6, 22), period_end=datetime(2026, 6, 28),
                  status="ready", file_url=None))

    # ---------------- 요금제/구독/결제 ----------------
    plans: dict[str, Plan] = {}
    for i, p in enumerate(D.BILLING_PLANS):
        plan = Plan(key=p["key"], name=p["name"], monthly_price=p["monthly"],
                    yearly_price=p["yearly"], api_quota=p["api_quota"],
                    student_seats=p["seats"], teacher_seats=p["teacher_seats"],
                    features=p["features"], order_no=i)
        db.add(plan)
        plans[p["key"]] = plan
    db.flush()
    db.add(Subscription(organization_id=org.id, plan_id=plans["Pro"].id,
                        billing_cycle="monthly", status="active", auto_renew=True))
    db.add(PaymentMethod(organization_id=org.id, card_brand="신한 법인카드",
                         card_last4="4821", is_default=True))
    for v in D.BILLING_INVOICES:
        db.add(Invoice(organization_id=org.id, invoice_no=v["invoice_no"],
                       description=v["description"], amount=v["amount"],
                       status="paid", billed_on=v["billed_on"]))

    # ---------------- AI 모델 6 ----------------
    for m in D.MODEL_VERSIONS:
        db.add(ModelVersion(category=m["category"], name=m["name"], provider=m["provider"],
                            version=m["version"], status=m["status"],
                            description=m["description"], updated_on=m["updated_on"]))

    # ---------------- 캡차 설정 ----------------
    db.add(CaptchaSetting(organization_id=org.id,
                          active_types={"image_select": True, "word_select": True,
                                        "drag": False, "arithmetic": False},
                          round_count=2, shuffle=True))

    # ---------------- 사이트 + API 키 ----------------
    site = Site(organization_id=org.id, name="햇살초등학교 홈페이지",
                domain="school.hatsal.kr", allowed_origins=["https://school.hatsal.kr"],
                status="active")
    db.add(site)
    db.flush()
    import secrets

    site_key = f"ck_live_8f2{secrets.token_hex(5)}a1"  # 화면 마스킹: ck_live_8f2•••a1
    db.add(ApiKey(organization_id=org.id, site_id=site.id, site_key=site_key,
                  secret_key_hash=sha256_hash(f"cs_live_{secrets.token_hex(24)}"),
                  status="active", last_used_at=now))


if __name__ == "__main__":
    seed()
