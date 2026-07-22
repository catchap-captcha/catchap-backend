"""강의 시청 검증 도메인 서비스 — 진행(하트비트) 검증·체크포인트 예약/기록·
동시접속 차단(claim_session). 출제 시점은 전부 고정 핀(문항의 position_sec 정각).

시각 비교는 전부 로컬 naive(_now, app/db/base.py와 동일 소스)로 한다. utcnow()가 섞이면
로컬(KST) created_at/updated_at과의 차이가 -32400초가 되어 wall-clock 전진 허용량이
음수 → 정상 시청자 전원이 '속도위반'으로 클램프되는 오탐이 실제로 발생한다(선행 프로젝트
사고 사례). tests/test_lectures.py의 변이 테스트가 이 규약을 지킨다.

commit은 호출자(엔드포인트) 책임 — audit()와 같은 규약.
"""

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import _now
from app.models import (
    Lecture,
    LectureCheckpointEvent,
    LectureMaterial,
    LectureQuestion,
    LectureQuestionGenJob,
    LectureTranscript,
    LectureWatchProgress,
)

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

# ---- 상호작용 면제: 제거됨(0716) ----
# 체크포인트 도달 시 interacted(자기신고)가 있으면 캡차를 건너뛰던 장치를 걷어냈다.
# 명분은 '성실한 시청자를 덜 방해한다'였는데, 전제가 틀렸다:
#   ① 강의에 집중하는 학생은 아무것도 만지지 않는다 — 그냥 본다. 즉 면제가 도우려던
#      바로 그 사람이 면제를 못 받았다.
#   ② interacted는 클라이언트 자기신고라 위조가 가능하다. 하트비트에 "interacted": true
#      한 줄을 넣는 쪽(봇·딴짓)만 이득을 봤다 — 방향이 거꾸로였다.
#   ③ 실제로 구멍을 만들었다: 위조 한 줄로 강사가 지정한 고정 문항을 건너뛰는 것이
#      적대적 검토에서 실증됐다(고정 3개 중 2개 스킵).
# 이 장치를 감싸던 예외들(streak 상한·마지막 게이트 면제 금지·고정 시점 면제 거부)도
# 함께 사라진다 — 전부 잘못된 전제를 떠받치던 땜빵이었다.
# 흐름 보호는 위조 불가능한 수단으로 한다: 강사가 고르는 확인 간격(느슨히=5~10분)과
# 출제 구간(끊어도 되는 대목을 강사가 지정, 정확한 초는 서버가 무작위).
# 근본 제약: 웹에서 '집중하고 있는가'를 측정할 방법이 없다. 추정 가능한 신호는 전부
# 위조 가능하다. 그래서 '봤는가'를 직접 묻는 콘텐츠 캡차가 유일한 수단이다.

# ---- 랜덤 간격·의심 가중: 제거됨(0717) ----
# 출제 시점은 이제 전부 핀이다 — 문항마다 position_sec이 있고, 체크포인트는 그 시점에만
# 잡힌다. '아무 때나 무작위 확인'(pinned=False 풀 + check_min/max_sec 간격)은 옛
# 시청-감시 설계의 잔재로, 학습 설계에서는 강사(또는 LLM)가 "이 대목을 물어라"를
# 지정하므로 쓸 곳이 없었다. 거기 딸려 있던 suspicion(의심 누적 시 간격 축소)과
# tab_hidden 자기신고도 좁힐 간격이 사라져 함께 걷어냈다.
# 속도 상한·체크포인트 클램프(위)는 position 위조 차단의 본체라 그대로 남는다.

# ---- 구간 출제(window_sec): 제거됨(0717 lecture_pin_03) ----
# [position, position+window] 안 무작위 초에 내던 구간 모드를 걷어내고 고정 핀만 남겼다.
# 되감기(아래 오답 상한)가 cp-REWIND_SEC 기준인데, 구간은 cp가 내용 시점(position)에서
# 최대 window만큼 멀어질 수 있어 문항과 무관한 대목을 되감았다(position=200·window=300이면
# cp=480에서 450~480을 다시 보게 되지만 내용은 200 근처). 고정만 남기면 cp == position이라
# 이 어긋남이 구조적으로 성립하지 않는다. 구간의 목적이던 '매번 같은 초면 학생이 지점을
# 외운다' 방어는 의도적으로 버린다 — 학습 강화 설계에선 지점을 외워도 내용을 봐야 답한다.

# ---- 오답 상한(되감기) ----
# 한 체크포인트에서 이 횟수만큼 연속 오답하면, 그 대목을 다시 보도록 watched_max를
# 되감는다. 되감기 전에는 오답 → 재발급이 무한 반복돼 보기 전수 대입이
# 대가 없이 가능했다. 되감으면 watched_max < cp가
# 되어 _lecture_challenge가 409로 새 문항 발급을 거부한다 — 실제로 다시 시청해 cp까지
# 올라와야(실시간 하트비트) 다음 문항을 받는다. 학습적으로도 옳다: 세 번 틀렸다는 건
# 그 대목을 다시 봐야 한다는 뜻이다. (프론트 상수 MAX_CHECKPOINT_FAILS와 맞춰 둘 것)
MAX_CHECKPOINT_FAILS = 3
# 되감기 폭 '폴백'(초) — 문항에 내용 시작 시점(content_start_sec)이 지정돼 있으면 그리로
# 되감고(강사가 아는 사실 — 대목의 시작), 미지정 문항만 cp-REWIND_SEC로 되감는다.
# 30이라는 값 자체는 근거가 없다(대목 길이는 문항마다 다르다) — 그래서 정본을 문항별
# 필드로 옮겼고, 이 상수는 미지정 문항의 보수적 기본값으로만 남는다.
REWIND_SEC = 30


def question_pins(db: Session, lecture_id: str) -> list[int]:
    """그 강의의 출제 시점(핀) 목록 — 오름차순, 중복 제거.

    모든 공개(active) 문항이 고정 핀이다: 학생이 position_sec에 닿는 순간 그 문항이 뜬다.
    (구간(window_sec) 출제는 제거됨 — 위 '구간 출제: 제거됨' 주석 참조.)"""
    rows = (
        db.query(LectureQuestion.position_sec)
        .filter(
            LectureQuestion.lecture_id == lecture_id,
            LectureQuestion.status == "active",
        )
        .all()
    )
    return sorted({int(pos) for (pos,) in rows})


def passed_positions(db: Session, student_id: str, lecture_id: str) -> set[int]:
    """이 학생이 이미 '통과한' 체크포인트 시점 집합 — LectureCheckpointEvent(passed)가 정본.

    되감기(watched_max 감소)가 생기면서 'watched < pin = 미통과' 추정이 깨졌다: 되감긴
    학생은 이미 통과한 핀 아래로 내려가 있다. 그 상태에서 watched 기준으로 재예약하면
    통과한 핀이 다시 잡혀 소급 재출제된다(skeptic 실증 — 핀 간격이 REWIND_SEC보다 좁을
    때 운영자 문항 수정 한 번으로 재현). 재예약·정합화의 '지나온 핀' 판정은 이 집합으로
    한다 — 통과 이벤트는 verify 트랜잭션에서 원자적으로 적재되는 사실 기록이다."""
    rows = (
        db.query(LectureCheckpointEvent.position_sec)
        .filter(
            LectureCheckpointEvent.student_id == student_id,
            LectureCheckpointEvent.lecture_id == lecture_id,
            LectureCheckpointEvent.result == "passed",
        )
        .all()
    )
    return {int(p) for (p,) in rows}


def reservable_pins(db: Session, student_id: str, lecture_id: str) -> list[int]:
    """이 학생에게 새로 예약할 수 있는 핀 — active 핀에서 통과한 시점을 뺀 목록."""
    done = passed_positions(db, student_id, lecture_id)
    return [p for p in question_pins(db, lecture_id) if p not in done]


def next_checkpoint(
    watched_max: int,
    duration_sec: int,
    pins: Sequence[int],
) -> int | None:
    """다음 체크포인트 예약 — 아직 안 닿은 가장 이른 핀, 없으면 None.

    ★ 핀 소진 판정은 'watched < pin'으로 한다. 클램프(cp+GRACE)가 있어 캡차를 풀지
    않고는 핀을 지날 수 없으므로, watched가 핀에 닿았다는 것은 그 체크포인트를 이미
    겪었다는 뜻이다. (0초 핀이 영영 안 잡히는 것도 이 판정의 귀결 — 생성/수정 검증이
    active 문항의 position>=1을 강제해 그 상태 자체를 막는다.)

    ★ 영상 밖(pin >= duration) 핀은 예약하지 않는다 — 도달 불가한 예약은 게이트가
    영영 안 열려 완주를 막는다(운영자 길이 수정·오타 방어).

    ★ 낼 문제가 없으면(핀 없음·전부 소진·전부 영상 밖) 예약하지 않는다: 예약만 해두고
    게이트 순간에 문항이 없어 4xx를 내면, 학생 화면에서는 '캡차가 그냥 안 뜨는데 진도도
    안 나가는' 조용한 실패가 된다(라이브에서 실제로 겪음)."""
    duration = int(duration_sec or 0)
    watched = int(watched_max)
    for pin in sorted(pins):
        if watched < pin < duration:
            return int(pin)
    return None


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
    # FOR UPDATE — 하트비트(advance)와 캡차 채점(record_checkpoint)이 같은 진행 행을
    # 다른 트랜잭션에서 읽고-고쳐-쓰면 READ COMMITTED에서 lost update가 난다: 캡차를
    # 푸는 동안 in-flight였던 하트비트가 되감기 '전' 스냅샷 기준의 큰 watched_max를
    # 나중에 커밋해 되감기를 통째로 덮는다(skeptic CONFIRMED). 학생·강의당 1행이라
    # 잠금 경합 비용은 미미하다. SQLite(테스트)에선 no-op.
    row = (
        db.query(LectureWatchProgress)
        .filter(
            LectureWatchProgress.student_id == student_id,
            LectureWatchProgress.lecture_id == lecture.id,
        )
        .with_for_update()
        .first()
    )
    if row is not None:
        return row
    pins = question_pins(db, lecture.id)
    row = LectureWatchProgress(
        student_id=student_id,
        lecture_id=lecture.id,
        watched_max_sec=0,
        next_checkpoint_sec=next_checkpoint(0, int(lecture.duration_sec or 0), pins),
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
) -> dict:
    """하트비트 1건 검증 — 서버 정본 watched_max/next_checkpoint를 갱신해 반환.

    ① 속도 상한: 직전 갱신(updated_at, 로컬 naive) 대비 wall-clock 경과 × SPEED_FACTOR
       + HEADROOM 이상으로는 전진 불가(클라이언트 position 자기신고 위조 차단).
    ② 체크포인트 클램프: next_checkpoint_sec + GRACE_SEC를 넘어서면 그 지점에서 정지
       (캡차를 풀기 전까지 진행 없음).
    체크포인트에 도달하면 예외 없이 캡차를 요구한다 — 상호작용 면제는 제거됐다(위 주석).
    watched_max는 절대 감소하지 않는다.
    """
    now = _now()
    duration = int(lec.duration_sec or 0)
    position = max(0, min(int(position_sec), duration))
    watched = int(progress.watched_max_sec or 0)

    anchor = progress.updated_at or progress.created_at
    elapsed = (now - anchor).total_seconds() if anchor else 0.0
    # HEADROOM은 시작 직후(재생 1~2초의 반올림·전송 지연)에만 준다 — 매 비트에 무조건
    # 더하면 elapsed≈0인 back-to-back 하트비트 N번이 N×HEADROOM을 공짜로 얻어, 되감기
    # 30초가 스팸 6번에 실시청 0초로 무효화된다(skeptic 실증 — 봇의 되감기 우회).
    # 시작 구간 밖에서는 SPEED_FACTOR(2.5배)의 여유가 지연·반올림을 이미 흡수한다.
    headroom = HEARTBEAT_HEADROOM_SEC if watched < HEARTBEAT_HEADROOM_SEC else 0
    allowed = elapsed * SPEED_FACTOR + headroom
    new_max = min(position, int(watched + allowed))

    # 예약이 비어 있으면 여기서 다시 잡는다 — next_checkpoint_sec=None은 '검증 끝'이 아니라
    # '아직 안 잡힘'일 수도 있다. 운영자가 강의 길이를 바꾸거나 문항을 새로 등록하면
    # 낡은 예약을 None으로 지우는데(lectures.py), 재예약 경로가 없으면 그 학생은 남은 강의
    # 내내 캡차가 한 번도 안 뜬다 = 시청 검증이 조용히 꺼진다(실제로 라이브에 나갔던 버그).
    # 영상을 끝까지 본 뒤에는 next_checkpoint가 다시 None을 돌려주므로 완주 판정은 그대로다.
    # 통과한 핀은 제외(reservable_pins) — 되감긴 학생(watched < 통과한 핀)의 예약이
    # 정합화로 풀린 경우, watched 기준만 보면 이미 통과한 핀을 다시 잡아 소급 재출제된다.
    # ★영상 밖(≥duration) 낡은 예약 자가치유: duration을 줄이거나 낡은 예약이 남으면
    # next_checkpoint_sec이 '도달 불가한 핀'(≥duration)에 묶여, 게이트가 영영 안 뜨고
    # watched_max가 duration에 닿아도 status가 done으로 못 넘어간다 = 완주 불가 → 문제은행
    # 영구 잠금(라이브 버그). next_checkpoint가 영상 밖 핀을 제외하므로 재계산하면 유효 핀
    # 또는 None으로 풀린다. 유효한 '영상 안(<duration)' 예약은 건드리지 않아 정상 잠금은 유지된다.
    cp_cur = progress.next_checkpoint_sec
    if cp_cur is None or cp_cur >= duration:
        pins = reservable_pins(db, progress.student_id, progress.lecture_id)
        progress.next_checkpoint_sec = next_checkpoint(watched, duration, pins)

    cp = progress.next_checkpoint_sec
    if cp is not None and new_max > cp + GRACE_SEC:
        new_max = cp + GRACE_SEC  # 캡차 미통과 — 체크포인트에서 클램프

    progress.watched_max_sec = max(watched, new_max)
    # 매 하트비트마다 앵커를 전진시킨다 — 클램프로 변경분이 없어도(더티 없음) updated_at이
    # 오래 머물면 다음 계산의 allowed가 부풀어, 체크포인트 통과 직후 점프 여지가 생긴다.
    progress.updated_at = now

    # 체크포인트에 닿으면 예외 없이 캡차 — 면제 경로 없음(위 '상호작용 면제: 제거됨' 참조).
    checkpoint_due = cp is not None and progress.watched_max_sec >= cp

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
        "checkpoints_passed": int(progress.checkpoints_passed or 0),
        "status": progress.status,
        "duration_sec": duration,
    }


def record_checkpoint(
    db: Session,
    *,
    student_id: str,
    lecture_id: str,
    position_sec: int,
    passed: bool,
    rewind_to_sec: int | None = None,
    question_id: str | None = None,
) -> LectureWatchProgress | None:
    """체크포인트 캡차 결과 기록 — 이벤트 적재 + 통과 시 카운트 증가·다음 지점 재예약.

    rewind_to_sec = 오답 상한 도달 시 되감을 지점(문항의 content_start_sec — 호출자가
    출제된 문항에서 해석해 전달). None이면 max(0, cp - REWIND_SEC) 폴백.
    반환은 갱신된 진행 행(없으면 None). commit은 호출자 책임."""
    db.add(
        LectureCheckpointEvent(
            student_id=student_id,
            lecture_id=lecture_id,
            question_id=question_id,  # 문항별 난이도·불량 탐지용(없으면 NULL)
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
        .with_for_update()  # 하트비트와의 lost update 차단 — ensure_progress 주석 참조
        .first()
    )
    if progress is None:
        return None
    if passed:
        lec = db.get(Lecture, lecture_id)
        progress.checkpoints_passed = int(progress.checkpoints_passed or 0) + 1
        progress.checkpoint_fails = 0  # 통과 — 연속 오답 카운터 리셋
        # 재예약 기준은 '통과한 체크포인트 시점(cp)' — watched_max를 쓰면 클램프 유예로
        # 부푼 값(cp+GRACE까지)이 (cp, cp+GRACE] 안의 다음 핀을 '이미 겪은 것'으로 오판해
        # 그 핀이 영구 스킵된다(skeptic 실증 — position 크게 신고하는 봇이 인접 핀을 통째로
        # 우회). cp 기준이면 그 핀이 정상 예약되고, watched가 이미 지나 있어도 다음
        # 하트비트의 checkpoint_due가 즉시 게이트를 연다(건너뛰기 불가).
        base = int(position_sec)
        if lec is not None:
            # 통과한 핀 제외 — watched<pin 판정은 되감기 이후 '통과'와 동치가 아니다
            pins = reservable_pins(db, student_id, lecture_id)
            progress.next_checkpoint_sec = next_checkpoint(
                base, int(lec.duration_sec or 0), pins
            )
        else:
            progress.next_checkpoint_sec = None
        if (
            lec is not None
            and progress.next_checkpoint_sec is None
            and int(progress.watched_max_sec or 0) >= int(lec.duration_sec or 0)
        ):
            progress.status = "done"
    else:
        # 오답 — 연속 실패 누적. 상한에 닿으면 그 대목을 다시 보도록 되감는다.
        fails = int(progress.checkpoint_fails or 0) + 1
        if fails >= MAX_CHECKPOINT_FAILS:
            cp = int(position_sec)
            if rewind_to_sec is not None:
                # 문항이 지정한 내용 시작 시점 — 반드시 cp '앞'으로 클램프한다. cp 이상으로
                # '되감으면' watched >= cp 그대로라 게이트가 즉시 재발급되고, 재시청 없는
                # 무한 재도전(브루트포스)이 부활한다. 생성/수정 검증이 < position을 강제하지만
                # 서비스는 호출자를 믿지 않는다(방어적 클램프 — 레거시 데이터·경로 추가 대비).
                progress.watched_max_sec = min(max(0, int(rewind_to_sec)), max(0, cp - 1))
            else:
                progress.watched_max_sec = max(0, cp - REWIND_SEC)
            progress.checkpoint_fails = 0
            # 앵커 갱신 필수 — 없으면 게이트가 열려 있던 동안 늘어난 elapsed로 allowed가
            # 부풀어, 되감긴 watched_max가 다음 하트비트 한 번에 cp로 다시 튀어 오른다
            # (되감기 무력화 — 특히 프론트 없이 position=cp를 반복 신고하는 봇).
            progress.updated_at = _now()
            # next_checkpoint_sec는 cp 그대로 — 다시 시청해 cp에 닿으면 같은 체크포인트가
            # 재트리거되고 그 시점의 핀 문항이 다시 나온다(재시청 후 재도전).
        else:
            progress.checkpoint_fails = fails
    return progress


# ---- 기동 시 고아 잡 정리 (스위퍼) ----
# AI 확인문항 생성 잡(LectureQuestionGenJob)은 프로세스 내 BackgroundTasks로 돈다.
# 프로세스가 재배포·크래시로 죽으면 'running'(또는 아직 안 잡힌 'pending') 잡을 마감할
# 코드가 다시 실행되지 않아, DB에 유령 행으로 영원히 남는다(프론트는 done을 기다리며
# "생성 중…"을 무한 표시). 기동 시 이 스위퍼가 한 번 돌아 '오래 멈춰 있는' 잡만 error로
# 정직하게 마감한다(_run_question_gen_job의 실패 마감과 동일 규약).
STUCK_GEN_JOB_MINUTES = 30  # updated_at이 이만큼 지난 pending/running만 고아로 간주


def sweep_stuck_gen_jobs(db: Session, stale_minutes: int = STUCK_GEN_JOB_MINUTES) -> int:
    """오래 멈춰 있는 생성 잡을 error로 마감하고, 정리한 개수를 반환한다.

    stale_minutes 임계값을 두는 이유: 워커를 2개 이상 띄운 경우, 한 워커가 재시작하는
    순간 '다른 워커에서 정상 작동 중'인 잡(updated_at이 방금 갱신됨)까지 죽이면 안 된다.
    updated_at은 status/phase 갱신마다 onupdate로 새로고쳐지므로, 임계값을 넘겨 멈춰 있는
    잡만 진짜 고아다. commit은 이 함수가 직접 한다(기동 훅에서 세션 하나로 호출)."""
    cutoff = _now() - timedelta(minutes=stale_minutes)
    stuck = (
        db.query(LectureQuestionGenJob)
        .filter(
            LectureQuestionGenJob.status.in_(["pending", "running"]),
            LectureQuestionGenJob.updated_at < cutoff,
        )
        .all()
    )
    for job in stuck:
        job.status = "error"
        job.phase = None
        job.error_detail = "서버 재시작으로 생성이 중단되었습니다. 다시 시도해 주세요."
        job.finished_at = _now()
    if stuck:
        db.commit()
    return len(stuck)


# ==================================================================== 휴지통·완전삭제
# 강의 삭제는 2단계다. 소프트 삭제(=휴지통, status='deleted'+deleted_at)는 파일·문항·전사를
# 전부 보존해 복구할 수 있게 하고, 완전 삭제(hard_delete_lecture)만이 되돌릴 수 없이 행·파일을
# 물리 제거한다. 30일 지난 휴지통 강의는 purge_expired_trash가 자동으로 완전 삭제한다.
TRASH_RETENTION_DAYS = 30


def _question_image_paths(media_dir: Path, payload: dict) -> list[Path]:
    """문항 payload의 이미지 참조(prompt_image + option_images) → 파일 경로 목록.
    엔드포인트의 _question_image_refs/_question_image_path와 같은 규약(id+ext로만 유도)."""
    refs: list[dict] = []
    pi = (payload or {}).get("prompt_image")
    if isinstance(pi, dict) and pi.get("id"):
        refs.append(pi)
    for ref in ((payload or {}).get("option_images") or {}).values():
        if isinstance(ref, dict) and ref.get("id"):
            refs.append(ref)
    return [media_dir / "questions" / f"{r['id']}{r.get('ext') or ''}" for r in refs]


def hard_delete_lecture(db: Session, lec: Lecture) -> dict:
    """강의를 영구 완전 삭제 — 문항·전사·시청이력·확인이벤트·생성잡·자료 행과 모든 파일을
    물리 제거한다. 소프트 삭제(휴지통)와 달리 되돌릴 수 없다.

    파일은 commit '성공 후'에 unlink한다(commit 실패 시 파일은 없는데 행은 남는 최악 방지 —
    기존 소프트삭제와 같은 순서 규약). unlink는 멱등(missing_ok)이라 이미 없어도 무해하다.
    반환: 지운 행 수(테이블별)·파일 수(감사·로그용). commit은 이 함수가 직접 한다."""
    media_dir = Path(get_settings().LECTURE_MEDIA_DIR)
    lec_id = lec.id
    video_ext = lec.video_ext

    # 파일 경로를 commit 전에 모아 둔다(commit 후 행이 사라지면 payload를 못 읽는다).
    paths: list[Path] = [media_dir / f"{lec_id}{video_ext}"]
    for m in db.query(LectureMaterial).filter(LectureMaterial.lecture_id == lec_id).all():
        if m.kind == "file" and m.file_ext:
            paths.append(media_dir / "materials" / f"{m.id}{m.file_ext}")
    for qr in db.query(LectureQuestion).filter(LectureQuestion.lecture_id == lec_id).all():
        paths.extend(_question_image_paths(media_dir, qr.payload or {}))

    # 자식 행부터 물리 삭제(소프트 참조라 DB 캐스케이드가 없다 — 코드가 직접 지운다).
    counts: dict[str, int] = {}
    for model in (
        LectureQuestion,
        LectureTranscript,
        LectureWatchProgress,
        LectureCheckpointEvent,
        LectureQuestionGenJob,
        LectureMaterial,
    ):
        counts[model.__tablename__] = (
            db.query(model)
            .filter(model.lecture_id == lec_id)
            .delete(synchronize_session=False)
        )
    db.delete(lec)
    db.commit()

    files_removed = 0
    for p in paths:
        try:
            if p.exists():
                files_removed += 1
            p.unlink(missing_ok=True)
        except OSError:
            pass  # 파일 삭제 실패는 치명적이지 않다(행은 이미 삭제됨) — 조용히 넘어간다
    return {"lecture_id": lec_id, "rows": counts, "files_removed": files_removed}


def purge_expired_trash(db: Session, retention_days: int = TRASH_RETENTION_DAYS) -> int:
    """휴지통에 retention_days 넘게 있은 강의를 자동 완전 삭제하고, 정리한 개수를 반환한다.

    스케줄러가 없는 구조라(워커 2개·주기잡 없음) 콘솔 조회·기동 시 기회적으로 호출된다.
    멱등: 대상이 없으면 0. deleted_at이 NULL인 구데이터는 만료 판단서 제외(영구 보존)한다."""
    cutoff = _now() - timedelta(days=retention_days)
    expired = (
        db.query(Lecture)
        .filter(
            Lecture.status == "deleted",
            Lecture.deleted_at.isnot(None),
            Lecture.deleted_at < cutoff,
        )
        .all()
    )
    for lec in expired:
        hard_delete_lecture(db, lec)  # 각자 commit(부분 실패해도 나머지는 정리됨)
    return len(expired)
