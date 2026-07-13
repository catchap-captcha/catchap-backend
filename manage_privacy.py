# -*- coding: utf-8 -*-
"""개인정보 파기 관리 커맨드 — 보존만료 자동 익명화 배치.

사용(컨테이너 안):
  python manage_privacy.py retention           # 기본 365일(1년) 비활성 학생 익명화
  python manage_privacy.py retention 180        # 180일 비활성 학생 익명화
  python manage_privacy.py student <student_id> # 특정 학생 즉시 익명화(탈퇴)

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
