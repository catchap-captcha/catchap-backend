"""수강 취소·환불 시 코스 학습 데이터 정리(purge).

수강 취소(무료) 또는 환불 시, 그 코스에 대한 학생의 학습 이력·풀이 데이터를 삭제한다
(사용자 결정 2026-08-05 — "수강취소 또는 환불 시 학습 이력 및 풀이 데이터 삭제").

삭제 대상(코스/강의에 귀속된 것만):
  - LectureWatchProgress    시청 진행(하이워터·완주 상태)
  - LectureCheckpointEvent  인비디오 확인문항 통과/실패 이력(풀이 데이터)
  - CourseExamSitting       수료시험 회차
  - CourseExamAttempt       수료시험 문항 응답(풀이 데이터 · 정복 원장)
  - CourseCompletion        수료 기록

유지: CourseOrder(결제 감사 근거), LectureReview(후기)·LectureQuestionReport(문항 신고=운영
      피드백), 과목기반 학습데이터(learning.py — course_id가 없어 코스 단위로 못 가름).

주의: db.commit()은 호출부가 한다(취소/환불의 다른 상태 변경과 한 트랜잭션으로 묶기 위해).
"""

from sqlalchemy.orm import Session


def purge_course_learning_data(db: Session, student_id: str, course_id: str) -> dict[str, int]:
    """student_id가 course_id 코스에 대해 쌓은 학습 이력·풀이 데이터를 삭제하고 삭제 건수를 반환.

    commit은 하지 않는다 — 호출부(수강취소/환불)가 자신의 커밋으로 함께 확정한다.
    """
    from app.models.course_exam import CourseCompletion, CourseExamAttempt, CourseExamSitting
    from app.models.lecture import Lecture, LectureCheckpointEvent, LectureWatchProgress

    lec_ids = [r[0] for r in db.query(Lecture.id).filter(Lecture.course_id == course_id).all()]
    counts: dict[str, int] = {}

    if lec_ids:
        counts["watch"] = (
            db.query(LectureWatchProgress)
            .filter(
                LectureWatchProgress.student_id == student_id,
                LectureWatchProgress.lecture_id.in_(lec_ids),
            )
            .delete(synchronize_session=False)
        )
        counts["checkpoint"] = (
            db.query(LectureCheckpointEvent)
            .filter(
                LectureCheckpointEvent.student_id == student_id,
                LectureCheckpointEvent.lecture_id.in_(lec_ids),
            )
            .delete(synchronize_session=False)
        )

    counts["exam_sitting"] = (
        db.query(CourseExamSitting)
        .filter(
            CourseExamSitting.student_id == student_id,
            CourseExamSitting.course_id == course_id,
        )
        .delete(synchronize_session=False)
    )
    counts["exam_attempt"] = (
        db.query(CourseExamAttempt)
        .filter(
            CourseExamAttempt.student_id == student_id,
            CourseExamAttempt.course_id == course_id,
        )
        .delete(synchronize_session=False)
    )
    counts["completion"] = (
        db.query(CourseCompletion)
        .filter(
            CourseCompletion.student_id == student_id,
            CourseCompletion.course_id == course_id,
        )
        .delete(synchronize_session=False)
    )
    return counts
