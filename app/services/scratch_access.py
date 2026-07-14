"""연습장 필기(ScratchRecord) 조회 — 역할별 재생/집계 공용 로직 (B 백엔드).

접근 모델(사용자 결정 2026-07-14):
- 원본 필기 재생: 학생 본인 · 모든 교사(열람 시 감사 기록) · 보호자(자녀만).
- 운영자: 원본이 아니라 익명 집계 지표(획수·거리)만. 필적은 재식별 가능하므로 원본 미노출.
- 탈퇴/보존기한으로 파기된(purged) 레코드는 strokes 없이 메타·집계만 남는다.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ScratchRecord


def _meta(r: ScratchRecord) -> dict:
    """목록용 메타 — 원본 획(strokes)은 제외(용량↓). 파기 여부·집계 지표 포함."""
    return {
        "id": r.id,
        "subject": r.subject,
        "content_id": r.content_id,
        "stroke_count": r.stroke_count,
        "distance_px": r.distance_px,
        "first_write_ms": r.first_write_ms,
        "draw_ms": r.draw_ms,
        "purged": r.purged,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _full(r: ScratchRecord) -> dict:
    """재생용 — 원본 획(strokes) 포함. 파기됐으면 빈 획 + purged=True."""
    d = _meta(r)
    d["strokes"] = [] if r.purged else (r.strokes or [])
    return d


def list_scratch(db: Session, student_id: str, subject: str | None = None, limit: int = 200) -> list[dict]:
    """한 학생의 연습장 필기 목록(과목별 필터). 최신순. 원본 획 미포함."""
    q = db.query(ScratchRecord).filter(ScratchRecord.student_id == student_id)
    if subject:
        q = q.filter(ScratchRecord.subject == subject)
    rows = q.order_by(ScratchRecord.created_at.desc()).limit(max(1, min(500, limit))).all()
    return [_meta(r) for r in rows]


def get_scratch(db: Session, student_id: str, record_id: str) -> dict | None:
    """한 학생의 특정 필기 재생(strokes 포함). 소유 불일치/없음이면 None(스코프 강제)."""
    r = db.get(ScratchRecord, record_id)
    if r is None or r.student_id != student_id:
        return None
    return _full(r)


def ops_aggregate(db: Session) -> dict:
    """운영자용 익명 집계 — 원본 필기·학생 신원 미노출, 과목별 획수·거리·시간 통계만.

    필적은 재식별 가능하므로 운영자에겐 개별 원본을 절대 노출하지 않고 이 집계만 제공한다.
    """
    from sqlalchemy import func

    rows = (
        db.query(
            ScratchRecord.subject,
            func.count(ScratchRecord.id),
            func.avg(ScratchRecord.stroke_count),
            func.avg(ScratchRecord.distance_px),
            func.avg(ScratchRecord.draw_ms),
        )
        .group_by(ScratchRecord.subject)
        .all()
    )
    by_subject = [
        {
            "subject": s,
            "records": int(c or 0),
            "avg_strokes": round(float(a or 0), 1),
            "avg_distance_px": round(float(d or 0)),
            "avg_draw_ms": round(float(m or 0)),
        }
        for s, c, a, d, m in rows
    ]
    return {
        "by_subject": by_subject,
        "total_records": sum(x["records"] for x in by_subject),
    }


def subject_summary(db: Session, student_id: str) -> list[dict]:
    """학생의 과목별 필기 요약(개수·총 획수) — 재생 목록 화면 상단용."""
    from collections import defaultdict

    agg: dict[str, dict] = defaultdict(lambda: {"count": 0, "strokes": 0})
    for subj, sc in db.query(ScratchRecord.subject, ScratchRecord.stroke_count).filter(
        ScratchRecord.student_id == student_id
    ):
        agg[subj]["count"] += 1
        agg[subj]["strokes"] += int(sc or 0)
    return [{"subject": s, **v} for s, v in agg.items()]
