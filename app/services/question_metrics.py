"""문항 지표 — 문제은행 각 문항의 노출수·정답률을 learning_attempts에서 집계한다.

문제은행 2단계(구조 재편)의 '문항별 정답률·노출수 → 대시보드' 항목. 운영자가 너무 쉬운/
너무 어려운/표본이 적은 문항을 찾아 정비(재검수·교체)하도록 돕는다.

왜 graded만 세나: `graded=True`는 서버가 정답을 검증한 시도(위젯 verify·game-answer)이고,
False는 `/learning/attempts` 자기신고(비검증)라 위조가 가능하다. 품질 지표는 조작 불가능한
근거만 써야 하므로 graded 시도만 집계한다(랭킹·코인이 graded만 세는 것과 같은 원칙).

성능: content_id에 인덱스가 없어 GROUP BY가 learning_attempts 전체 스캔이다. 운영자 전용·
저빈도 대시보드라 현 규모(수십만 행)에선 감당 가능. 10만+ 문항·수백만 시도 규모가 되면
집계 결과를 주기적 롤업 테이블로 캐시하는 것을 고려한다(알려진 한계로 기록).
"""
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models import LearningAttempt, Question

LOW_SAMPLE = 5   # 시도 수가 이 미만이면 정답률을 신뢰하지 않는다 → 'low_sample'
TOO_EASY = 90    # 정답률(%) ≥ → 'too_easy'(너무 쉬움 — 변별력 낮음)
TOO_HARD = 40    # 정답률(%) ≤ → 'too_hard'(너무 어려움 — 오류·난이도 점검 대상)

SORTS = ("most_shown", "least_shown", "hardest", "easiest")


def _flags(attempts: int, accuracy: int) -> list[str]:
    """표본이 적으면 난이도 판정을 보류하고(low_sample), 충분하면 쉬움/어려움을 표시한다."""
    if attempts < LOW_SAMPLE:
        return ["low_sample"]
    if accuracy >= TOO_EASY:
        return ["too_easy"]
    if accuracy <= TOO_HARD:
        return ["too_hard"]
    return []


def compute(
    db: Session,
    *,
    subject: str | None = None,
    sort: str = "most_shown",
    min_attempts: int = 1,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """문항별 지표 집계. 반환 = {summary, items, page}. graded 시도만 센다."""
    if sort not in SORTS:
        sort = "most_shown"
    min_attempts = max(1, min_attempts)
    limit = max(1, min(200, limit))
    offset = max(0, offset)

    q = db.query(
        LearningAttempt.content_id.label("id"),
        LearningAttempt.subject.label("subject"),
        func.count(LearningAttempt.id).label("attempts"),
        func.coalesce(
            func.sum(case((LearningAttempt.result == "correct", 1), else_=0)), 0
        ).label("correct"),
    ).filter(
        LearningAttempt.graded.is_(True),
        LearningAttempt.content_id.isnot(None),
    )
    if subject:
        q = q.filter(LearningAttempt.subject == subject)
    rows = q.group_by(LearningAttempt.content_id, LearningAttempt.subject).all()

    # 문항 원문(prompt/type/topic) 보강 — 은퇴·삭제된 문항 id는 원문 없이 그대로 노출한다
    # (시도 이력은 남아 있으므로 지표는 유효하다).
    ids = [r.id for r in rows]
    qmap: dict[str, dict] = {}
    if ids:
        for qq in db.query(Question).filter(Question.id.in_(ids)).all():
            payload = qq.payload or {}
            qmap[qq.id] = {
                "type": qq.type,
                "prompt": payload.get("prompt"),
                "topic": payload.get("topic"),
            }

    items: list[dict] = []
    for r in rows:
        attempts = int(r.attempts or 0)
        if attempts < min_attempts:
            continue
        correct = int(r.correct or 0)
        accuracy = round(correct / attempts * 100) if attempts else 0
        meta = qmap.get(r.id) or {}
        items.append({
            "id": r.id,
            "subject": r.subject,
            "type": meta.get("type"),
            "topic": meta.get("topic"),
            "prompt": meta.get("prompt") or "(삭제된 문항)",
            "attempts": attempts,
            "correct": correct,
            "accuracy": accuracy,
            "flags": _flags(attempts, accuracy),
        })

    if sort == "most_shown":
        items.sort(key=lambda x: x["attempts"], reverse=True)
    elif sort == "least_shown":
        items.sort(key=lambda x: x["attempts"])
    elif sort == "hardest":
        items.sort(key=lambda x: (x["accuracy"], -x["attempts"]))
    elif sort == "easiest":
        items.sort(key=lambda x: (-x["accuracy"], -x["attempts"]))

    total_attempts = sum(x["attempts"] for x in items)
    total_correct = sum(x["correct"] for x in items)
    summary = {
        "questions": len(items),
        "attempts": total_attempts,
        # 문항 평균이 아니라 시도 가중 평균(전체 정답/전체 시도) — 표본 큰 문항이 더 반영된다
        "avg_accuracy": round(total_correct / total_attempts * 100) if total_attempts else None,
        "too_easy": sum(1 for x in items if "too_easy" in x["flags"]),
        "too_hard": sum(1 for x in items if "too_hard" in x["flags"]),
        "low_sample": sum(1 for x in items if "low_sample" in x["flags"]),
    }
    return {
        "summary": summary,
        "items": items[offset : offset + limit],
        "page": {"limit": limit, "offset": offset, "total": len(items)},
    }
