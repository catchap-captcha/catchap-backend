# -*- coding: utf-8 -*-
"""개인정보 파기 관리 커맨드 — 보존만료 자동 익명화 배치.

사용(컨테이너 안):
  python manage_privacy.py retention           # 기본 365일(1년) 비활성 학생 익명화
  python manage_privacy.py retention 180        # 180일 비활성 학생 익명화
  python manage_privacy.py student <student_id> # 특정 학생 즉시 익명화(탈퇴)
  python manage_privacy.py minors               # 만14세 미만 파기 '대상 목록'(드라이런)
  python manage_privacy.py minors --execute     # 만14세 미만 전원 익명화 실행
                                                # (행동데이터는 삭제하지 않음 — actor_band 태그로 보존·사용)

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
            from app.models import BehaviorSummary, StudentProfile

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
            before_behavior = db.query(BehaviorSummary).count()
            n = privacy_service.purge_minor_students(db)
            after_behavior = db.query(BehaviorSummary).count()
            print(f"익명화 실행: {n}건")
            # 불변식: 행동데이터는 삭제되지 않는다(성인 생성분 보존 — 사용자 지시 0718)
            print(f"행동데이터 행수 {before_behavior} → {after_behavior} (불변이어야 함)")
            if before_behavior != after_behavior:
                print("!! 행동데이터 행수가 변했다 — 즉시 조사 필요")
                return 1
            return 0
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
