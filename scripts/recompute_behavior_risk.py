"""behavior_summaries.risk_level 재계산 — 실트래픽 행만 (백필, 멱등).

적재 코드가 위험 판정 없이 쌓아 실데이터가 전부 'low' 고정이었다. 적재와 동일한
_behavior_risk_level(아동 보정 규칙)로 재판정한다. seed 데모 행은 건드리지 않는다
— seed는 interaction_result가 pass|fail(실트래픽은 correct|incorrect)이고
의도된 90/8/2 분포를 명시 세팅한다(app/db/seed.py).

궤적이 있는 행은 저장된 요약 지표(path_length/avg_speed/pause_count)도 궤적에서
다시 계산해 갱신한다 — avg_speed의 gap 왜곡(멀리 떨어진 두 탭 사이 '비행'이 거리만
더해지던 문제, 0710 수정) 이전에 적재된 값을 정정하기 위함.

실행(백엔드 루트에서): python scripts/recompute_behavior_risk.py
"""

from collections import Counter

from app.db.session import SessionLocal
from app.models import BehaviorSummary, BehaviorTrace
from app.services.captcha_service import _behavior_risk_level, _trace_metrics


def main() -> None:
    db = SessionLocal()
    try:
        rows = (
            db.query(BehaviorSummary)
            .filter(BehaviorSummary.interaction_result.in_(("correct", "incorrect")))
            .all()
        )
        if not rows:
            print("실트래픽(correct|incorrect) 행이 없습니다 — 변경 없음.")
            return
        traces = {
            t.behavior_id: t
            for t in db.query(BehaviorTrace).filter(
                BehaviorTrace.behavior_id.in_([r.id for r in rows])
            )
        }
        before = Counter(r.risk_level for r in rows)
        changed = 0
        for r in rows:
            t = traces.get(r.id)
            # 적재 시점과 동일 입력을 재구성 — 궤적이 있으면 지표도 궤적에서 다시 계산
            trace = (
                {"points": t.points, "box_w": t.box_w, "box_h": t.box_h}
                if t and t.points and len(t.points) >= 2
                else None
            )
            m = _trace_metrics(trace) if trace else None
            level = _behavior_risk_level(
                solve_time_ms=r.solve_time_ms or 0,
                correct=r.interaction_result == "correct",
                trace=trace,
                metrics=m,
                input_type=r.input_type or "unknown",
            )
            if m is not None:
                # gap 왜곡 수정 전에 적재된 부풀려진 avg_speed 등을 궤적 기준으로 정정
                r.path_length = m["path_length"]
                r.avg_speed = m["avg_speed"]
                r.pause_count = m["pause_count"]
            if level != r.risk_level:
                r.risk_level = level
                changed += 1
        db.commit()
        after = Counter(r.risk_level for r in rows)
        print(f"대상(실트래픽) {len(rows)}건 중 {changed}건 변경")
        print(f"before: {dict(before)}")
        print(f"after : {dict(after)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
