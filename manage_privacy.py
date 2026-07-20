# -*- coding: utf-8 -*-
"""개인정보 파기 관리 커맨드 — 보존만료 자동 익명화 배치.

사용(컨테이너 안):
  python manage_privacy.py retention           # 기본 365일(1년) 비활성 학생 익명화
  python manage_privacy.py retention 180        # 180일 비활성 학생 익명화
  python manage_privacy.py student <student_id> # 특정 학생 즉시 익명화(탈퇴)
  python manage_privacy.py minors               # 만14세 미만 파기 '대상 목록'(드라이런)
  python manage_privacy.py minors --execute     # 만14세 미만 전원 익명화 실행
                                                # (행동데이터는 삭제하지 않음 — actor_band 태그로 보존·사용)
  python manage_privacy.py all-except-ops           # 운영자 제외 전원 익명화·탈퇴 '대상'(드라이런)
  python manage_privacy.py all-except-ops --execute # 운영자 제외 전 학생·전 비운영자 사용자 익명화·탈퇴 실행
                                                # (★행동데이터는 절대 삭제하지 않음 — 4개 테이블 불변식 검사)

크론 예:
  0 4 * * *  docker exec -e PYTHONPATH=/app catchap-backend-api-1 python /app/manage_privacy.py retention
"""
import sys


def main() -> int:
    from app.db.session import SessionLocal
    from app.services import privacy_service

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    cmd = args[0]
    db = SessionLocal()
    try:
        if cmd == "retention":
            days = int(args[1]) if len(args) > 1 else 365
            n = privacy_service.anonymize_stale_students(db, inactive_days=days)
            print(f"보존만료({days}일) 익명화: {n}건")
            return 0
        if cmd == "minors":
            # 아동 테스트데이터 파기 (사용자 승인 0718). 기본은 드라이런 —
            # --execute 없이는 아무것도 바꾸지 않고 대상만 보여준다.
            from app.models import (
                BehaviorSummary,
                BehaviorTrace,
                LearningAttempt,
                StudentProfile,
            )

            targets = privacy_service.find_minor_students(db)
            pending = [s for s in targets if s.real_name is not None or s.status != "disabled"]
            unknown = (
                db.query(StudentProfile)
                .filter(StudentProfile.birth_date.is_(None), StudentProfile.age.is_(None))
                .count()
            )
            print(f"만14세 미만(확인) 총 {len(targets)}건 / 미파기 {len(pending)}건 / 연령미상(미대상) {unknown}건")
            for s in pending:
                basis = f"birth={s.birth_date}" if s.birth_date else f"age={s.age}"
                print(f"  - {s.student_login_id} ({s.nickname}, {basis}, org={'유' if s.organization_id else '무'})")
            if "--execute" not in args:
                print("(드라이런 — 실행하려면 --execute)")
                return 0
            # 불변식: 파기는 학습·행동 데이터를 삭제하지 않는다(성인 생성분 보존 —
            # 사용자 지시 0718). 행동요약뿐 아니라 궤적·학습시도까지 셋을 검사한다
            # (skeptic 0718: 한 테이블만 세면 다른 테이블 삭제를 못 잡는다).
            def _counts():
                return {
                    "behavior_summaries": db.query(BehaviorSummary).count(),
                    "behavior_traces": db.query(BehaviorTrace).count(),
                    "learning_attempts": db.query(LearningAttempt).count(),
                }

            before = _counts()
            n = privacy_service.purge_minor_students(db)
            after = _counts()
            print(f"익명화 실행: {n}건")
            print(f"데이터 불변식 (삭제 0이어야 함):")
            leaked = False
            for k in before:
                mark = "OK" if before[k] == after[k] else "!! 변함"
                if before[k] != after[k]:
                    leaked = True
                print(f"  {k}: {before[k]} → {after[k]}  [{mark}]")
            if leaked:
                print("!! 학습/행동 데이터가 삭제됐다 — 즉시 조사 필요(파기는 PII 익명화만이어야 함)")
                return 1
            return 0
        if cmd == "all-except-ops":
            # 운영자(role='ops') 제외 전원 익명화·탈퇴 — 전 학생(student_profiles) + 전 비운영자
            # 사용자(users where role<>'ops'). ★행동데이터는 절대 삭제하지 않는다(사용자 지시):
            # 4개 행동 테이블 카운트를 익명화 전후로 검사해 삭제 0을 강제한다.
            from app.models import (
                BehaviorSummary,
                BehaviorTrace,
                LearningAttempt,
                LectureCheckpointEvent,
                StudentProfile,
                User,
            )

            students = db.query(StudentProfile).all()
            nonops = db.query(User).filter(User.role != "ops").all()
            ops_n = db.query(User).filter(User.role == "ops").count()
            s_pending = [s for s in students if s.real_name is not None or s.status != "disabled"]
            u_pending = [
                u for u in nonops
                if not (u.status == "disabled" and (u.email or "").startswith("del_"))
            ]
            print(
                f"학생 {len(students)}건(미파기 {len(s_pending)}) · 비운영자 사용자 {len(nonops)}건"
                f"(미파기 {len(u_pending)}) · 운영자 보존 {ops_n}건"
            )
            if "--execute" not in args:
                print("(드라이런 — 실행하려면 --execute)")
                return 0

            def _bcounts():
                return {
                    "learning_attempts": db.query(LearningAttempt).count(),
                    "behavior_summaries": db.query(BehaviorSummary).count(),
                    "behavior_traces": db.query(BehaviorTrace).count(),
                    "lecture_checkpoint_events": db.query(LectureCheckpointEvent).count(),
                }

            before = _bcounts()
            sn = sum(1 for s in students if privacy_service.anonymize_student(db, s))
            un = sum(1 for u in nonops if privacy_service.anonymize_user(db, u))
            db.commit()
            after = _bcounts()
            print(f"익명화·탈퇴 실행: 학생 {sn}건 · 비운영자 사용자 {un}건")
            print("★행동데이터 불변식 (삭제 0이어야 함):")
            leaked = False
            for k in before:
                mark = "OK" if before[k] == after[k] else "!! 변함(삭제됨)"
                if before[k] != after[k]:
                    leaked = True
                print(f"  {k}: {before[k]} → {after[k]}  [{mark}]")
            if leaked:
                print("!! 행동데이터가 삭제됐다 — 즉시 조사 필요(파기는 PII 익명화만이어야 함)")
                return 1
            # 운영자 보존 확인 — ops는 익명화 대상이 아니었으니 그대로여야 한다
            ops_ok = db.query(User).filter(
                User.role == "ops", User.email.like("del_%")
            ).count()
            print(f"운영자 보존 확인: 익명화된 ops = {ops_ok}건 (0이어야 정상)")
            return 1 if ops_ok else 0
        if cmd == "student" and len(args) > 1:
            from app.models import StudentProfile

            s = db.get(StudentProfile, args[1])
            if s is None:
                print("학생 없음")
                return 1
            changed = privacy_service.anonymize_student(db, s)
            db.commit()
            print("익명화 완료" if changed else "이미 익명화됨(변경 없음)")
            return 0
        print(__doc__)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
