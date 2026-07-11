"""레드팀 봇 트래픽 생성기 — 지도학습의 음성 클래스(sample_label='bot') 확보용.

현재 behavior_summaries는 실트래픽(organic)뿐이라 '사람 vs 봇' 학습을 시작할 수 없다.
여기서 봇다운 합성 궤적을 만들어(record_behavior_event 경로로) bot 라벨을 박아 넣는다.

격리: 실제 기관이 아닌 sentinel org(REDTEAM_ORG_ID)에 적재한다. 고객용 집계
(aggregate.py)는 전부 organization_id==org_id로 필터하므로, 이 sentinel org의 봇 행은
어떤 고객 기관 차트/대시보드에도 절대 잡히지 않는다. 운영 콘솔(학습셋 관리)에서는 보인다.

봇 패턴은 위험 스코어링(_behavior_risk_level)이 잡는 특성 — 너무 빠름·너무 곧음·순간이동·
과속 — 을 의도적으로 만든다. 다양성을 위해 4개 패턴을 섞는다(단조로운 음성 클래스 방지).
"""

import math
import random

# 실제 기관이 아닌 sentinel — 봇 행 격리 표식(org_id에 FK 없음). 36자 hex형.
REDTEAM_ORG_ID = "5ed7ea70-0000-4000-8000-5ed7ea700b07"

_BOX_SIZES = [(320, 480), (360, 560), (390, 640), (420, 600), (800, 600)]


def _line_trace(rng, box_w, box_h, n_points):
    """기계 직선 — 시작~끝을 등간격으로, 픽셀 떨림 거의 0(사람 손과 정반대)."""
    sx, sy = rng.uniform(0.1, 0.3), rng.uniform(0.1, 0.3)
    ex, ey = rng.uniform(0.7, 0.9), rng.uniform(0.7, 0.9)
    dt = rng.choice([16, 20, 33])  # 균일 간격(봇 리듬)
    pts = []
    for i in range(n_points):
        f = i / (n_points - 1)
        # 정규화 0.5px 미만 떨림만 — 스코어링의 3px 직선 기준을 통과(=봇)
        jx = rng.uniform(-0.4, 0.4) / box_w
        jy = rng.uniform(-0.4, 0.4) / box_h
        pts.append([i * dt, round(sx + (ex - sx) * f + jx, 4), round(sy + (ey - sy) * f + jy, 4)])
    return pts


def generate_bot_behaviors(n: int, seed: int | None = None) -> list[dict]:
    """봇다운 behavior dict n개. 각 dict는 record_behavior_event의 behavior 인자 형태.

    반환 dict에 내부용 '_correct'(정답 여부)를 실어 보낸다 — 호출부가 꺼내 쓴다.
    """
    rng = random.Random(seed)
    n = max(0, min(2000, int(n)))
    out: list[dict] = []
    for _ in range(n):
        box_w, box_h = rng.choice(_BOX_SIZES)
        box = {"w": box_w, "h": box_h}
        pattern = rng.choice(["straight", "teleport", "superspeed", "uniform"])
        input_type = rng.choices(["mouse", "touch", "unknown"], weights=[6, 3, 1])[0]

        if pattern == "straight":
            # 곧은 드래그 + 즉답 → 직진 + 즉답 = elevated
            trace = _line_trace(rng, box_w, box_h, rng.randint(8, 14))
            solve = rng.randint(250, 750)
        elif pattern == "teleport":
            # 점 2개, 큰 변위, 시간은 흐름 → 순간이동 + 즉답
            sx, sy = rng.uniform(0.05, 0.2), rng.uniform(0.05, 0.2)
            ex, ey = rng.uniform(0.8, 0.95), rng.uniform(0.8, 0.95)
            dur = rng.randint(500, 1500)
            trace = [[0, round(sx, 4), round(sy, 4)], [dur, round(ex, 4), round(ey, 4)]]
            solve = rng.randint(300, 780)
        elif pattern == "superspeed":
            # 조밀 시간(gap<300ms)에 거대 거리 → avg_speed 폭발 + 즉답
            pts = []
            x, y = rng.uniform(0.05, 0.15), rng.uniform(0.05, 0.15)
            t = 0
            for _i in range(rng.randint(4, 6)):
                pts.append([t, round(x, 4), round(y, 4)])
                x = min(0.98, x + rng.uniform(0.25, 0.4))
                y = min(0.98, y + rng.uniform(0.25, 0.4))
                t += rng.choice([8, 12, 16])  # 아주 짧은 간격
            trace = pts
            solve = rng.randint(200, 700)
        else:  # uniform — 정확히 200ms 간격의 균일 직선(봇 리듬 + 직진)
            trace = _line_trace(rng, box_w, box_h, rng.randint(6, 10))
            for i, p in enumerate(trace):
                p[0] = i * 200
            solve = rng.randint(400, 1200)

        out.append({
            "solve_time_ms": solve,
            "input_type": input_type,
            "trace": trace,
            "box": box,
            "_correct": rng.random() > 0.15,  # 봇은 대개 통과, 15%는 실패로 다양성
        })
    return out


def inject_bot_behaviors(db, n: int, seed: int | None = None) -> int:
    """봇 behavior n개를 sentinel org에 sample_label='bot'으로 적재. commit까지. 적재 건수 반환."""
    from app.services.captcha_service import record_behavior_event

    behaviors = generate_bot_behaviors(n, seed)
    for b in behaviors:
        correct = bool(b.pop("_correct", True))
        record_behavior_event(
            db,
            organization_id=REDTEAM_ORG_ID,
            student_id=None,
            source_type="edu-api",
            behavior=b,
            correct=correct,
            sample_label="bot",
        )
    db.commit()
    return len(behaviors)
