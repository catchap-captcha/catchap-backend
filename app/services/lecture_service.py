"""강의 시청 검증 도메인 서비스 — 진행(하트비트) 검증·체크포인트 예약/기록·
동시접속 차단(claim_session)·행동 기반 캡차 트리거(상호작용 면제·의심 가중).

시각 비교는 전부 로컬 naive(_now, app/db/base.py와 동일 소스)로 한다. utcnow()가 섞이면
로컬(KST) created_at/updated_at과의 차이가 -32400초가 되어 wall-clock 전진 허용량이
음수 → 정상 시청자 전원이 '속도위반'으로 클램프되는 오탐이 실제로 발생한다(선행 프로젝트
사고 사례). tests/test_lectures.py의 변이 테스트가 이 규약을 지킨다.

commit은 호출자(엔드포인트) 책임 — audit()와 같은 규약.
"""

import random
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import _now
from app.models import Lecture, LectureCheckpointEvent, LectureWatchProgress

# 하트비트 간 position 전진 상한 = wall-clock 경과 × SPEED_FACTOR.
# 2배속 시청까지는 정상 사용이므로 2.5배로 여유를 둔다(전송 지연·반올림 포함).
SPEED_FACTOR = 2.5
# 첫 하트비트·짧은 간격의 반올림/네트워크 지연 여유(초) — 이게 없으면 경과 0초 시점의
# 정상 하트비트(재생 1~2초)까지 전부 0으로 클램프된다.
HEARTBEAT_HEADROOM_SEC = 5
# 체크포인트 유예(초) — next_checkpoint_sec + GRACE 이상으로는 캡차를 풀기 전까지
# watched_max가 전진하지 않는다(건너뛰기 차단의 핵심).
GRACE_SEC = 15

# ---- 동시접속 차단 ----
# 마지막 하트비트 후 이 시간(초)이 지나면 죽은 세션으로 간주한다. 하트비트 주기(수 초)의
# 여유 배수 — 새로고침·일시 네트워크 단절이 성실한 사용자를 영영 잠그지 않게 하는 값.
SESSION_TTL_SEC = 30

# ---- 상호작용 면제 ----
# 체크포인트 도달 시 최근 상호작용(interacted 자기신고)이 있으면 캡차를 건너뛰는 연속 상한.
# 2회로 둔 근거: 기본 간격 60~180초 기준 최대 2×180초(약 6분)까지만 캡차 없이 지나가고,
# 그다음 체크포인트는 무조건 캡차 — interacted=true를 계속 위조해 보내도 세 번째마다는
# 반드시 캡차를 풀어야 하므로 남용 피해가 유한하다. 추가로 '마지막 체크포인트'(재예약
# 후보가 None인 지점)는 streak와 무관하게 면제 불가 — 면제가 남은 게이트를 없애 캡차
# 0회 완주가 되는 구멍(advance ④)을 막는다.
# ⚠️ interacted는 클라이언트 자기신고라 위조 가능하다. 이 면제는 봇 차단 수단이 아니라
# '성실한 시청자(입력이 있었던 사람)를 덜 방해'하는 UX 장치이고, 위조 남용은 이 상한으로만
# 제한된다. 봇 차단의 정본은 캡차 자체와 서버 하트비트 검증(속도상한·클램프)이다.
EXEMPT_STREAK_MAX = 2

# ---- 의심 가중 ----
# suspicion 누적 시 체크포인트 간격을 나눠 좁히되, 이 하한(초) 아래로는 절대 내리지 않는다 —
# 오탐(정상 사용자의 실수 seek 등)이 쌓여도 '몇 초마다 캡차' 같은 징벌이 되지 않게 하는 안전판.
CHECKPOINT_FLOOR_SEC = 20
# 속도상한 클램프를 '의심 이벤트'로 셀 때의 여유(초) — 반올림·전송 지연으로 살짝 넘친
# 정상 하트비트를 seek 시도로 오인하지 않기 위한 값.
SEEK_TOLERANCE_SEC = 2
# suspicion 상한 — seek 1회도 watched가 따라잡을 때까지 매 하트비트 카운트되므로(적대적
# 검토에서 실측: seek 1회→+20) 무제한 누적을 막는다. 8이면 간격이 이미 하한까지 좁혀지는
# 수준이라 탐지력 손실 없이, 아래 반감 회복이 캡차 3회(8→4→2→1) 안에 끝난다.
SUSPICION_MAX = 8


def next_checkpoint(watched_max: int, lec: Lecture, suspicion: int = 0) -> int | None:
    """다음 체크포인트 지점 예약 — watched_max + [check_min, check_max] 무작위.

    suspicion(의심 이벤트 누적)이 있으면 간격을 (1+suspicion)로 나눠 좁힌다.
    단 CHECKPOINT_FLOOR_SEC(강의 설정 최소가 그보다 작으면 그 값) 아래로는 내리지
    않는다 — 오탐 누적이 '몇 초마다 캡차' 지옥이 되는 것을 하한으로 차단.
    영상 길이(duration_sec)를 넘으면 None(남은 체크포인트 없음).

    ★ 최소 1회 보장: 간격이 강의 길이보다 길면 체크포인트가 영상 밖으로 나가
    '시청 검증이 0회인 강의'가 조용히 만들어진다(3분 강의 + '보통' 설정이면 실측
    66%가 0회). 강사는 간격만 골랐을 뿐 검증을 끄려던 게 아니므로, 아직 한 번도
    확인하지 않은 강의(watched_max=0)에서는 간격을 영상 안으로 접어 최소 1회는
    반드시 뜨게 한다. 이미 한 번이라도 확인했다면(watched_max>0) 남은 구간이
    짧은 건 정상이므로 None을 그대로 돌려준다."""
    duration = int(lec.duration_sec or 0)
    lo = max(1, int(lec.check_min_sec or 60))
    hi = max(lo, int(lec.check_max_sec or 180))
    s = max(0, int(suspicion or 0))
    if s > 0:
        floor = min(CHECKPOINT_FLOOR_SEC, lo)  # 축소가 원래 최소 간격보다 커지지 않게
        lo = max(lo // (1 + s), floor)
        hi = max(hi // (1 + s), lo)
    watched = int(watched_max)
    # 아직 한 번도 확인 안 한 강의는 반드시 영상 안에서 뽑는다.
    # lo만 접으면 hi 쪽에서 큰 값이 뽑혀 여전히 영상을 벗어난다(3분+보통 66% 0회) —
    # 상한을 duration-1로 함께 잘라야 0회가 사라진다.
    if watched <= 0 and duration > 1:
        hi = min(hi, duration - 1)
        lo = min(lo, hi)
    cp = watched + random.randint(lo, hi)
    if cp >= duration:
        return None
    return cp


def claim_session(
    db: Session, progress: LectureWatchProgress, session_id: str, *, force: bool = False
) -> None:
    """학생 단위 단일 활성 시청 세션 강제 — 동시접속 차단(법정 요건, 캡차로 대신하지 않는다).

    session_id는 반드시 서버가 발급한 값(엔드포인트에서 new_uuid)이어야 한다 — 클라
    생성값을 받던 시절엔 두 기기가 같은 값을 짜고 보내 한 세션으로 위장할 수 있었다
    (skeptic CONFIRMED). 클라에는 서명 토큰만 나가고 여기엔 복원된 원문이 들어온다.
    같은 학생의 '다른' 활성 세션(다른 강의 포함 — 한 사람이 두 강의를 동시에 볼 수 없다)이
    last_heartbeat_at 기준 SESSION_TTL_SEC 이내면 409를 던진다(구조화 detail에
    active_elsewhere=True — 프론트의 '여기서 계속하기' 안내용). force=True(takeover)면
    다른 세션들을 무효화(session_id=None)하고 이 세션이 이어받는다 — 무효화된 쪽의
    다음 하트비트가 409를 받는다. TTL을 넘긴 세션은 죽은 것으로 보고 조용히 재점유한다
    (새로고침·크래시 오탐이 성실한 사용자를 잠그지 않게).
    시각 비교는 _now(로컬 naive)만 — last_heartbeat_at도 같은 소스로 기록된다."""
    now = _now()
    threshold = now - timedelta(seconds=SESSION_TTL_SEC)
    live = (
        db.query(LectureWatchProgress)
        .filter(
            LectureWatchProgress.student_id == progress.student_id,
            LectureWatchProgress.session_id.isnot(None),
            LectureWatchProgress.last_heartbeat_at.isnot(None),
            LectureWatchProgress.last_heartbeat_at >= threshold,
        )
        .all()
    )
    others = [
        row
        for row in live
        if not (row.id == progress.id and row.session_id == session_id)
    ]
    if others:
        if not force:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "message": "다른 곳에서 이 계정으로 시청 중이에요. 여기서 계속하려면 이어보기를 눌러 주세요.",
                    "active_elsewhere": True,
                },
            )
        for row in others:  # takeover — 이전 세션 무효화(그쪽 하트비트는 이후 409)
            row.session_id = None
    progress.session_id = session_id
    progress.last_heartbeat_at = now


def ensure_progress(db: Session, student_id: str, lecture: Lecture) -> LectureWatchProgress:
    """학생·강의당 1행 진행 upsert — UniqueConstraint + IntegrityError 재조회(동시요청 안전)."""
    row = (
        db.query(LectureWatchProgress)
        .filter(
            LectureWatchProgress.student_id == student_id,
            LectureWatchProgress.lecture_id == lecture.id,
        )
        .first()
    )
    if row is not None:
        return row
    row = LectureWatchProgress(
        student_id=student_id,
        lecture_id=lecture.id,
        watched_max_sec=0,
        next_checkpoint_sec=next_checkpoint(0, lecture),
        checkpoints_passed=0,
        status="watching",
    )
    db.add(row)
    try:
        db.flush()
        return row
    except IntegrityError:
        # 동시 요청이 먼저 만든 경우 — 롤백 후 그 행을 사용
        db.rollback()
        row = (
            db.query(LectureWatchProgress)
            .filter(
                LectureWatchProgress.student_id == student_id,
                LectureWatchProgress.lecture_id == lecture.id,
            )
            .first()
        )
        if row is None:  # 롤백 직후에도 없으면 데이터 이상 — 조용히 넘기지 않는다
            raise
        return row


def advance(
    db: Session,
    progress: LectureWatchProgress,
    lec: Lecture,
    position_sec: int,
    *,
    interacted: bool = False,
    tab_hidden: bool = False,
) -> dict:
    """하트비트 1건 검증 — 서버 정본 watched_max/next_checkpoint를 갱신해 반환.

    ① 속도 상한: 직전 갱신(updated_at, 로컬 naive) 대비 wall-clock 경과 × SPEED_FACTOR
       + HEADROOM 이상으로는 전진 불가(클라이언트 position 자기신고 위조 차단).
    ② 체크포인트 클램프: next_checkpoint_sec + GRACE_SEC를 넘어서면 그 지점에서 정지
       (캡차를 풀기 전까지 진행 없음).
    ③ 의심 가중: 속도상한을 유의미하게 넘긴 position 신고(안 본 구간 seek/과속)와
       탭 백그라운드 자기신고(tab_hidden — 위조 가능, 참고용)를 suspicion에 누적한다.
       suspicion은 다음 체크포인트 간격 축소에만 쓰이고 시청을 막지는 않는다.
    ④ 상호작용 면제: 체크포인트 도달 시 interacted(자기신고 — 위조 가능, 아래 주석)면
       캡차 없이 다음 지점을 재예약한다. 연속 EXEMPT_STREAK_MAX회까지만 — 그다음은
       무조건 캡차(캡차 통과 시 streak 리셋). '입력이 있었다=사람이 있었다'는 참이지만
       그 신고 자체는 클라이언트 위조가 가능하므로, 이 면제는 봇 차단이 아니라 성실한
       시청자의 방해를 줄이는 장치이고 남용 피해는 상한으로만 제한된다.
    watched_max는 절대 감소하지 않는다.
    """
    now = _now()
    duration = int(lec.duration_sec or 0)
    position = max(0, min(int(position_sec), duration))
    watched = int(progress.watched_max_sec or 0)

    anchor = progress.updated_at or progress.created_at
    elapsed = (now - anchor).total_seconds() if anchor else 0.0
    allowed = elapsed * SPEED_FACTOR + HEARTBEAT_HEADROOM_SEC
    new_max = min(position, int(watched + allowed))

    # 의심 이벤트 ①: 속도상한을 여유(SEEK_TOLERANCE) 이상 넘긴 신고 — 안 본 구간
    # seek 또는 과속 하트비트. 클램프는 위에서 이미 걸렸고, 여기서는 그 '사건'을 센다.
    # SUSPICION_MAX 상한 — seek 지점을 계속 신고하는 정상 플레이어가 비트마다 누적돼
    # 회복 불가 수준으로 치솟는 것을 막는다(통과 시 반감 회복과 짝, record_checkpoint).
    if position > int(watched + allowed) + SEEK_TOLERANCE_SEC:
        progress.suspicion = min(SUSPICION_MAX, int(progress.suspicion or 0) + 1)
    # 의심 이벤트 ②: 탭 백그라운드 자기신고 — 위조(미신고) 가능하므로 참고용 가중일 뿐,
    # 이 신호가 없다고 결백으로 치지 않는다.
    if tab_hidden:
        progress.suspicion = min(SUSPICION_MAX, int(progress.suspicion or 0) + 1)

    cp = progress.next_checkpoint_sec
    if cp is not None and new_max > cp + GRACE_SEC:
        new_max = cp + GRACE_SEC  # 캡차 미통과 — 체크포인트에서 클램프

    progress.watched_max_sec = max(watched, new_max)
    # 매 하트비트마다 앵커를 전진시킨다 — 클램프로 변경분이 없어도(더티 없음) updated_at이
    # 오래 머물면 다음 계산의 allowed가 부풀어, 체크포인트 통과 직후 점프 여지가 생긴다.
    progress.updated_at = now

    checkpoint_due = cp is not None and progress.watched_max_sec >= cp
    exempted = False
    if (
        checkpoint_due
        and interacted
        and int(progress.exempt_streak or 0) < EXEMPT_STREAK_MAX
    ):
        # 상호작용 면제 — 캡차 없이 통과 처리하되 checkpoints_passed는 올리지 않는다
        # (그 카운트는 '캡차를 실제로 푼 횟수'). 감사용 이벤트는 exempted로 남긴다.
        base = max(int(progress.watched_max_sec or 0), int(cp))
        candidate = next_checkpoint(base, lec, int(progress.suspicion or 0))
        if candidate is None:
            # 마지막 체크포인트는 면제 불가 — 여기서 면제하면 남은 게이트가 없어져
            # interacted 스팸만으로 캡차 0회 완주가 된다(적대적 검토에서 실증:
            # 기본 60~180초 설정의 9분 미만 강의 전부 노출). 무조건 캡차를 요구한다.
            pass
        else:
            exempted = True
            progress.exempt_streak = int(progress.exempt_streak or 0) + 1
            db.add(
                LectureCheckpointEvent(
                    student_id=progress.student_id,
                    lecture_id=progress.lecture_id,
                    position_sec=int(cp),
                    result="exempted",
                )
            )
            progress.next_checkpoint_sec = candidate
            checkpoint_due = False

    if (
        progress.watched_max_sec >= duration
        and progress.next_checkpoint_sec is None
        and duration > 0
    ):
        progress.status = "done"

    return {
        "watched_max_sec": int(progress.watched_max_sec),
        "next_checkpoint_sec": progress.next_checkpoint_sec,
        "checkpoint_due": bool(checkpoint_due),
        "exempted": exempted,  # True면 프론트는 캡차를 띄우지 않고 계속 재생
        "checkpoints_passed": int(progress.checkpoints_passed or 0),
        "status": progress.status,
        "duration_sec": duration,
    }


def record_checkpoint(
    db: Session, *, student_id: str, lecture_id: str, position_sec: int, passed: bool
) -> LectureWatchProgress | None:
    """체크포인트 캡차 결과 기록 — 이벤트 적재 + 통과 시 카운트 증가·다음 지점 재예약.

    반환은 갱신된 진행 행(없으면 None). commit은 호출자 책임."""
    db.add(
        LectureCheckpointEvent(
            student_id=student_id,
            lecture_id=lecture_id,
            position_sec=int(position_sec),
            result="passed" if passed else "failed",
        )
    )
    progress = (
        db.query(LectureWatchProgress)
        .filter(
            LectureWatchProgress.student_id == student_id,
            LectureWatchProgress.lecture_id == lecture_id,
        )
        .first()
    )
    if progress is None:
        return None
    if passed:
        lec = db.get(Lecture, lecture_id)
        progress.checkpoints_passed = int(progress.checkpoints_passed or 0) + 1
        progress.exempt_streak = 0  # 캡차를 실제로 풀었다 — 상호작용 면제 연속 상한 리셋
        # 캡차 통과 = 사람 확인 — suspicion 반감(회복 경로). 오탐이 쌓인 정상 학생이
        # 캡차 몇 번(최대 8→4→2→1→0)으로 정상 간격을 되찾는다. 계속 seek하는 쪽은
        # 다시 쌓이므로 탐지력은 유지된다.
        progress.suspicion = int(progress.suspicion or 0) // 2
        base = max(int(progress.watched_max_sec or 0), int(position_sec))
        # 재예약도 (반감된) suspicion 반영 — 의심이 남은 학생은 통과 후에도 좁은 간격
        progress.next_checkpoint_sec = (
            next_checkpoint(base, lec, int(progress.suspicion or 0)) if lec else None
        )
        if (
            lec is not None
            and progress.next_checkpoint_sec is None
            and int(progress.watched_max_sec or 0) >= int(lec.duration_sec or 0)
        ):
            progress.status = "done"
    return progress
