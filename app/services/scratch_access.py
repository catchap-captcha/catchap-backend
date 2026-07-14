"""연습장 필기(ScratchRecord) 조회 — 역할별 재생/집계 공용 로직 (B 백엔드).

접근 모델(사용자 결정 2026-07-14):
- 원본 필기 재생: 학생 본인 · 모든 교사(열람 시 감사 기록) · 보호자(자녀만).
- 운영자: 원본이 아니라 익명 집계 지표(획수·거리)만. 필적은 재식별 가능하므로 원본 미노출.
- 탈퇴/보존기한으로 파기된(purged) 레코드는 strokes 없이 메타·집계만 남는다.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import ScratchRecord

# 필기 원본 보존 동의 약관 버전 — 문구 갱신 시 올린다(동의 증빙 추적, Consent.terms_version).
SCRATCH_TERMS_VERSION = "scratch_retain_v1"


def has_retain_consent(db: Session, student_id: str) -> bool:
    """이 학생 필기 원본 '보존 동의'가 활성(철회 안 됨)인지 — 보호자가 켠 상태."""
    from app.models import Consent

    return (
        db.query(Consent.id)
        .filter(
            Consent.student_id == student_id,
            Consent.consent_type == "scratch_retain",
            Consent.withdrawn_at.is_(None),
        )
        .first()
        is not None
    )


def set_retain_consent(
    db: Session, student_id: str, organization_id: str | None, granted_by_user_id: str, retain: bool
) -> None:
    """보호자의 필기 원본 보존 동의 설정(멱등). 커밋은 호출자 책임.

    - retain=True: 활성 동의가 없으면 Consent(scratch_retain) 신규 기록(누가·언제·약관버전).
    - retain=False: 활성 동의가 있으면 withdrawn_at 채워 철회.
    - 두 경우 모두 이 학생의 '미파기' 필기 레코드 consent_retain을 즉시 갱신
      (이미 파기된 원본은 되살리지 않는다). 이후 새 레코드는 저장 시점 동의로 결정된다.
    """
    from app.models import Consent

    active = (
        db.query(Consent)
        .filter(
            Consent.student_id == student_id,
            Consent.consent_type == "scratch_retain",
            Consent.withdrawn_at.is_(None),
        )
        .first()
    )
    now = datetime.utcnow()
    if retain and active is None:
        db.add(
            Consent(
                student_id=student_id,
                organization_id=organization_id or "",
                granted_by_user_id=granted_by_user_id,
                consent_type="scratch_retain",
                terms_version=SCRATCH_TERMS_VERSION,
                granted_at=now,
            )
        )
        db.flush()  # autoflush=False라 같은 세션 내 재호출이 pending 동의를 보게 함(멱등 보장)
    elif not retain and active is not None:
        active.withdrawn_at = now
    db.query(ScratchRecord).filter(
        ScratchRecord.student_id == student_id,
        ScratchRecord.purged.is_(False),
    ).update({ScratchRecord.consent_retain: bool(retain)}, synchronize_session=False)


def _question_view(subject: str, content_id: str | None) -> dict | None:
    """content_id로 문제은행 문항을 되살려 '보기용' 요약을 만든다(필기를 문제와 나란히 보기 위함).

    저장 시 content_id = 문항 id(qid)이므로 get_question으로 O(1) 조회된다. 문항이
    갱신·삭제됐거나 매칭 실패면 None(재생은 필기만 표시)."""
    if not content_id:
        return None
    try:
        from app.services import subject_banks

        q = subject_banks.get_question(subject, content_id)
    except Exception:
        q = None
    if not q:
        return None
    view: dict = {
        "prompt": q.get("prompt"),
        "topic": q.get("topic"),
        "type": q.get("type"),
        "explain": q.get("explain"),
    }
    opts = q.get("options") or q.get("items")
    if isinstance(opts, list):
        view["options"] = [
            {"id": o.get("id"), "text": o.get("text") or o.get("e") or o.get("emoji") or ""}
            for o in opts
            if isinstance(o, dict)
        ]
    if q.get("answer") is not None:
        view["answer"] = q.get("answer")
    if q.get("answers"):
        view["answers"] = q.get("answers")
    if q.get("figure"):
        view["figure"] = q.get("figure")
    return view


def _meta(r: ScratchRecord) -> dict:
    """목록용 메타 — 원본 획(strokes)은 제외(용량↓). 파기 여부·집계 지표 + 문제 미리보기(prompt)."""
    qv = _question_view(r.subject, r.content_id)
    return {
        "id": r.id,
        "subject": r.subject,
        "content_id": r.content_id,
        "prompt": (qv or {}).get("prompt"),  # 목록에서 '문항 id' 대신 문제 원문 미리보기
        "stroke_count": r.stroke_count,
        "distance_px": r.distance_px,
        "first_write_ms": r.first_write_ms,
        "draw_ms": r.draw_ms,
        "purged": r.purged,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _full(r: ScratchRecord) -> dict:
    """재생용 — 원본 획(strokes) + 문항(question) 포함. 파기됐으면 빈 획 + purged=True."""
    d = _meta(r)
    d["strokes"] = [] if r.purged else (r.strokes or [])
    d["question"] = _question_view(r.subject, r.content_id)  # 문제(프롬프트·보기·정답·해설)를 필기와 함께
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
    """운영자용 익명 집계 — 원본 필기·학생 신원 미노출, 과목별·시계열 통계만.

    필적은 재식별 가능하므로 운영자에겐 개별 원본을 절대 노출하지 않고 이 집계만 제공한다.
    실무 대시보드용 확장(0714): 최근 7일 수집·일별 14일 추이·필기 학생 수(익명 카운트)·
    개인정보 지표(파기/보존동의 건수).
    """
    from datetime import datetime, timedelta

    from sqlalchemy import func

    now = datetime.utcnow()
    d7 = now - timedelta(days=7)
    d14 = now - timedelta(days=14)

    rows = (
        db.query(
            ScratchRecord.subject,
            func.count(ScratchRecord.id),
            func.avg(ScratchRecord.stroke_count),
            func.avg(ScratchRecord.distance_px),
            func.avg(ScratchRecord.draw_ms),
            func.sum(ScratchRecord.stroke_count),
            func.count(func.distinct(ScratchRecord.student_id)),
        )
        .group_by(ScratchRecord.subject)
        .all()
    )
    last7 = dict(
        db.query(ScratchRecord.subject, func.count(ScratchRecord.id))
        .filter(ScratchRecord.created_at >= d7)
        .group_by(ScratchRecord.subject)
        .all()
    )
    by_subject = [
        {
            "subject": s,
            "records": int(c or 0),
            "week_records": int(last7.get(s, 0)),
            "students": int(st or 0),  # 익명 카운트 — 신원 미노출
            "avg_strokes": round(float(a or 0), 1),
            "total_strokes": int(ts or 0),
            "avg_distance_px": round(float(d or 0)),
            "avg_draw_ms": round(float(m or 0)),
        }
        for s, c, a, d, m, ts, st in rows
    ]

    # 최근 14일 일별 수집 추이 — 대시보드 스파크바
    daily_rows = (
        db.query(func.date(ScratchRecord.created_at), func.count(ScratchRecord.id))
        .filter(ScratchRecord.created_at >= d14)
        .group_by(func.date(ScratchRecord.created_at))
        .all()
    )
    by_date = {str(d): int(c) for d, c in daily_rows}
    daily = []
    for i in range(13, -1, -1):
        day = (now - timedelta(days=i)).date()
        daily.append({"date": day.isoformat(), "count": by_date.get(day.isoformat(), 0)})

    # 개인정보 지표 — 파기·보존동의 현황(운영 책임 가시화)
    purged_n = db.query(func.count(ScratchRecord.id)).filter(ScratchRecord.purged.is_(True)).scalar() or 0
    retain_n = (
        db.query(func.count(ScratchRecord.id)).filter(ScratchRecord.consent_retain.is_(True)).scalar() or 0
    )

    return {
        "by_subject": by_subject,
        "total_records": sum(x["records"] for x in by_subject),
        "week_records": sum(x["week_records"] for x in by_subject),
        "total_students": sum(x["students"] for x in by_subject),
        "daily": daily,
        "privacy": {"purged": int(purged_n), "consent_retain": int(retain_n)},
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
