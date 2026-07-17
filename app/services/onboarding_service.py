"""반 배정 이력(SIS enrollment) 기록.

종전의 온보딩(학생 가입코드·학부모 초대코드) 기능은 학교·학부모 은퇴(0717~18)로
제거됐다 — 남은 것은 기존 데이터의 학년도 절단 계산이 읽는 배정 이력 기록뿐.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import ClassAssignment, StudentProfile


def record_class_assignment(db: Session, student: StudentProfile, new_class_id: str | None) -> None:
    """반 배정 이력 기록(SIS enrollment) — 배정이 바뀌는 모든 지점에서 호출한다.

    열린 행(ended_on IS NULL)을 오늘로 닫고, 새 반이 있으면 새 행을 연다.
    같은 반 재배정은 무시(이력 오염 방지). 시각은 KST 로컬(datetime.now()) 규약.
    교사 명단의 '학년도(배정 기간)' 학습시간 절단이 이 이력을 쓴다."""
    try:
        open_row = (
            db.query(ClassAssignment)
            .filter(ClassAssignment.student_id == student.id, ClassAssignment.ended_on.is_(None))
            .first()
        )
    except Exception:
        # 테이블이 아직 없으면(DDL 미적용 배포 창) 이력 기록만 건너뛴다 —
        # 배정 자체(class_id 변경)가 이력 때문에 실패하면 안 된다.
        db.rollback()
        return
    if open_row is not None and open_row.class_id == new_class_id:
        return  # 변화 없음
    now = datetime.now()
    if open_row is not None:
        open_row.ended_on = now
    if new_class_id:
        db.add(
            ClassAssignment(
                organization_id=student.organization_id,
                student_id=student.id,
                class_id=new_class_id,
                started_on=now,
            )
        )

# 혼동 문자(0/O, 1/I/L) 제외한 고엔트로피 알파벳


# --- 이하 은퇴(제품 전환 0717~18) ---
# 학생 가입코드 발급/활성화(generate_join_codes·reissue·check·activate_student)와
# 학부모 초대코드(issue_parent_invite·consume_parent_invite)는 학교·학부모 은퇴로
# 제거됐다 — 종전 코드는 git 이력 참고. 이 모듈에 남은 것은 반 배정 이력 기록뿐이다
# (기존 데이터의 학년도 절단 계산이 계속 읽는다).
