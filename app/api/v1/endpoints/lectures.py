"""강의 시청 검증 — 학생 시청(목록/상세/하트비트/스트리밍) + 운영자 강의·문항 CRUD.

  학생(require_student)
    GET  /lectures                    active 강의 목록 + 내 진행 요약 — (subject, order_no, created_at) 목차순
    GET  /lectures/{id}               상세 + 진행 + 문항 수 + 자료실(materials) + 과목 목차(toc)
                                      (순수 조회 — 세션·스트림 URL 없음)
    GET  /lectures/{id}/materials/{mid}/download
                                      file 자료 다운로드(FileResponse attachment) — 경로는 자료 id로만 유도
    GET  /lectures/{id}/questions/{qid}/images/{img}
                                      문항 이미지 인라인 서빙(<img>용 — 무인증·UUID 3중 경로, 정답 미노출)
    POST /lectures/{id}/session       재생 시작 — 서버가 session_id 발급(서명 토큰) + 서명 stream_url.
                                      다른 활성 세션이 있으면 409(active_elsewhere)
    POST /lectures/{id}/progress      하트비트 — X-Lecture-Session 토큰으로 세션 식별, 서버 검증
                                      (속도상한·체크포인트 클램프·동시세션 409·상호작용 면제·의심 가중)
    POST /lectures/{id}/takeover      이어보기 — 이전 활성 세션 무효화, 새 세션 토큰·stream_url 발급
    GET  /lectures/{id}/stream?t=     서명 토큰(세션 바인딩) 검증 후 FileResponse(Range 네이티브).
                                      takeover로 세션이 무효화되면 이전 토큰은 403

  제작(require_lecture_manager: 운영자=전체 / 강사=자기 강의만) — 학생 실명·개별 기록은
  노출하지 않는다(PII 금지). 강사 스코프는 _get_ops_lecture(소유권 404)와 목록 필터가 강제.
    GET/POST /ops/lectures            목록 / 업로드(multipart, 청크 복사·누적 바이트 재검사)
    PUT/DELETE /ops/lectures/{id}     메타 수정 / 소프트 삭제
    GET/POST/PUT/DELETE /ops/lectures/{id}/questions[/{qid}]  확인 문항 CRUD
    POST/DELETE /ops/lectures/{id}/questions/{qid}/images     문항 이미지 첨부/제거
                                      (slot=prompt|option — 강의 화면 캡처를 보기로 출제)
    POST /ops/lectures/{id}/questions/generate                LLM 자동 생성(키 없으면 503)
    GET/POST/PUT/DELETE /ops/lectures/{id}/materials[/{mid}]  자료실 CRUD
                                      (POST: kind=link는 JSON, kind=file은 multipart 업로드)

체크포인트 캡차 발급/채점 자체는 공개 캡차 API(captcha_api.py)의 ?lecture= 분기가 담당한다.
"""

import os
import re
import time
from datetime import datetime
from pathlib import Path

import jwt
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from jwt import PyJWTError
from pydantic import BaseModel, Field
from sqlalchemy import func, not_
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.permissions import Principal, require_lecture_manager, require_student
from app.core.security import decode_token, new_uuid
from app.db.session import get_db
from app.models import (
    Course,
    Lecture,
    LectureCheckpointEvent,
    LectureMaterial,
    LectureQuestion,
    LectureWatchProgress,
)
from app.services import auth_service, lecture_service
from app.services.captcha_service import EDU_SUBJECTS
from app.utils.helpers import audit

router = APIRouter(tags=["lectures"])

# 확장자·Content-Type 화이트리스트 — 이 둘 밖의 업로드는 거절(경로조작·비디오 위장 차단)
_MEDIA_TYPES = {".mp4": "video/mp4", ".webm": "video/webm"}
_ALLOWED_CONTENT_TYPES = {"video/mp4", "video/webm"}

# 강의 자료(자료실) 확장자 화이트리스트 — 문서·이미지·압축만. 실행파일(exe/bat/sh/js …)은
# 목록에 없으므로 구조적으로 거절된다. 다운로드는 항상 attachment + octet-stream으로
# 내려보내 브라우저 인라인 실행(HTML/SVG XSS)도 차단한다.
_MATERIAL_EXTS = {
    ".pdf", ".zip", ".png", ".jpg", ".jpeg", ".gif",
    ".hwp", ".hwpx", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".txt",
}

# 확인 문항 이미지(강의 화면 캡처) 확장자·Content-Type 화이트리스트 — 래스터 이미지만.
# SVG는 인라인 렌더 시 스크립트 삽입(XSS) 위험이 있어 금지하고(자료실과 달리 문항 이미지는
# <img>로 '인라인' 서빙된다), 실행파일류는 목록에 없어 구조적으로 거절된다.
_QUESTION_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_QUESTION_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_QUESTION_IMAGE_MEDIA = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}

_UPLOAD_CHUNK = 1024 * 1024  # 1MB 단위 청크 복사 — 영상 전체를 RAM에 올리지 않는다
_STREAM_TOKEN_TTL_SEC = 6 * 60 * 60  # 스트림 서명 토큰 유효기간 6시간
# 세션 토큰 유효기간 — 스트림 토큰과 동일 6시간. 만료보다 중요한 무효화 수단은
# takeover(세션 교체)다: progress.session_id가 바뀌는 순간 이전 토큰은 즉시 죽는다.
_SESSION_TOKEN_TTL_SEC = 6 * 60 * 60

RATE_HEARTBEAT_PER_HOUR = 720  # 하트비트 — 5초 간격 시청 기준 여유
RATE_UPLOAD_PER_HOUR = 20
RATE_MATERIAL_UPLOAD_PER_HOUR = 40  # 자료 '파일' 업로드만 — link 생성(JSON)은 대상 아님
# 문항 이미지 업로드 상한 — 문항당 최대 7장(프롬프트 1 + 보기 6)이라 자료(40)보다 넉넉히.
# 시간당 문항 ~17개 분량의 실무 등록을 수용하면서 업로드 스팸(디스크 소모)은 억제한다.
RATE_QUESTION_IMAGE_UPLOAD_PER_HOUR = 120
# 세션 발급 상한 — 정상 사용(재생 시작·새로고침)은 시간당 수 회. 발급 스팸으로 자기
# session_id를 회전시키며 진행 행을 흔드는 것을 억제한다.
RATE_SESSION_PER_HOUR = 60
# takeover 상한 — 두 세션이 하트비트마다 번갈아 takeover하면 동시 재생 차단이 무력화되므로
# 별도의 낮은 상한을 둔다. 정상 사용(새로고침·기기 이동)은 시간당 수 회를 넘지 않는다.
RATE_TAKEOVER_PER_HOUR = 30


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _media_dir() -> Path:
    return Path(get_settings().LECTURE_MEDIA_DIR)


def _video_path(lec: Lecture) -> Path:
    # 경로는 DB에 저장하지 않는다 — id(UUID)+화이트리스트 확장자로만 유도(경로조작 원천 차단)
    return _media_dir() / f"{lec.id}{lec.video_ext}"


def _materials_dir() -> Path:
    return _media_dir() / "materials"


def _material_path(mat: LectureMaterial) -> Path:
    # 영상과 동일 원칙 — 자료 파일 경로도 id(UUID)+화이트리스트 확장자로만 유도
    return _materials_dir() / f"{mat.id}{mat.file_ext}"


def _question_images_dir() -> Path:
    return _media_dir() / "questions"


def _question_image_path(ref: dict) -> Path:
    # 영상·자료와 동일 원칙 — 문항 이미지 경로도 payload에 기록된 id(UUID·서버 발급)와
    # 화이트리스트 확장자로만 유도. 클라이언트 입력이 경로에 끼어들 자리가 없다.
    # ext는 .get — 손상된 참조(ext 누락)여도 삭제 연쇄(unlink missing_ok)가 500 없이 지나간다.
    return _question_images_dir() / f"{ref['id']}{ref.get('ext') or ''}"


def _question_image_url(lecture_id: str, question_id: str, ref: dict) -> str:
    """학생·콘솔 <img>가 로드할 서빙 경로 — 내부 파일 경로는 어디에도 노출하지 않는다."""
    return f"/api/v1/lectures/{lecture_id}/questions/{question_id}/images/{ref['id']}"


def _question_image_refs(payload: dict) -> list[dict]:
    """payload의 이미지 참조 전부(prompt_image + option_images 값들) — 삭제 연쇄·서빙 검증용."""
    refs: list[dict] = []
    pi = (payload or {}).get("prompt_image")
    if isinstance(pi, dict) and pi.get("id"):
        refs.append(pi)
    for ref in ((payload or {}).get("option_images") or {}).values():
        if isinstance(ref, dict) and ref.get("id"):
            refs.append(ref)
    return refs


# ---------------------------------------------------------------- 세션·스트림 서명 토큰
# 세션 식별자는 서버만 발급한다(new_uuid). 클라이언트가 만든 식별자는 어떤 경로로도
# 신뢰하지 않는다 — 클라 생성 viewer_id를 그대로 믿었다가 검증을 우회당한 선행 사고
# (LectureCaptcha)와 동형의 구멍이므로, 클라에는 원문 session_id 대신 '서명된 세션
# 토큰'을 쥐여 주고 서버가 토큰에서 session_id를 복원한다(위조·임의 조합 불가).
def _sign_session_token(session_id: str, student_id: str, lecture_id: str) -> str:
    """시청 세션 토큰 — 서버 발급 session_id를 학생·강의에 바인딩해 서명."""
    settings = get_settings()
    payload = {
        "type": "lecture-session",
        "sid": session_id,
        "sub": student_id,
        "lec": lecture_id,
        "exp": int(time.time()) + _SESSION_TOKEN_TTL_SEC,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _verify_session_token(token: str | None, lecture_id: str, student_id: str) -> str:
    """세션 토큰 검증 → session_id. 서명·만료·강의·학생 불일치는 전부 403.

    student_id까지 대조한다 — 남의 세션 토큰을 훔쳐 와도 본인 JWT와 조합할 수 없다."""
    if not token:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="시청 세션 토큰이 필요합니다. 재생 시작(POST /session)으로 발급받으세요.",
        )
    try:
        payload = decode_token(token)
    except PyJWTError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="시청 세션 토큰이 유효하지 않습니다.")
    if (
        payload.get("type") != "lecture-session"
        or payload.get("lec") != lecture_id
        or payload.get("sub") != student_id
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="시청 세션 토큰이 유효하지 않습니다.")
    return str(payload.get("sid", ""))


def _sign_stream_token(lecture_id: str, student_id: str, session_id: str) -> str:
    """스트림 접근 토큰 — JWT_SECRET 재사용, 강의·학생·세션·만료(6h) 바인딩.

    <video src>는 Authorization 헤더를 못 실으므로 쿼리 서명으로 인가한다.
    session_id 바인딩이 핵심 — takeover로 세션이 교체되면 이전 스트림 URL은
    서명이 유효해도 403(두 번째 기기는 영상 바이트 자체를 못 받는다)."""
    settings = get_settings()
    payload = {
        "type": "lecture-stream",
        "lec": lecture_id,
        "sub": student_id,
        "sid": session_id,
        "exp": int(time.time()) + _STREAM_TOKEN_TTL_SEC,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _verify_stream_token(token: str | None, lecture_id: str) -> tuple[str, str]:
    """서명·만료·강의 바인딩 검증 → (student_id, session_id). 불일치는 전부 403.

    세션이 아직 활성인지(progress.session_id 일치)는 호출자가 DB로 검사한다."""
    if not token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="스트림 접근 토큰이 필요합니다.")
    try:
        payload = decode_token(token)
    except PyJWTError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="스트림 토큰이 유효하지 않습니다.")
    if payload.get("type") != "lecture-stream" or payload.get("lec") != lecture_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="스트림 토큰이 유효하지 않습니다.")
    return str(payload.get("sub", "")), str(payload.get("sid", ""))


_OPS_STREAM_TOKEN_TTL_SEC = 60 * 60  # 운영자 미리보기 — 편집 한 세션 정도만


def _sign_ops_stream_token(lecture_id: str, ops_user_id: str) -> str:
    """운영자 미리보기 스트림 토큰 — 강의·운영자·만료 바인딩. 세션 바인딩은 없다.

    학생 스트림과 type을 분리한다("lecture-stream-ops"). 같은 type을 쓰면 학생 토큰으로
    세션 검사가 없는 운영자 경로에 들어가 동시재생 차단(세션 바인딩)을 통째로 우회한다.
    운영자는 시청 검증 대상이 아니므로 진행·세션 개념이 없고, 그래서 더더욱 학생이
    이 경로에 닿으면 안 된다.
    """
    settings = get_settings()
    payload = {
        "type": "lecture-stream-ops",
        "lec": lecture_id,
        "sub": ops_user_id,
        "exp": int(time.time()) + _OPS_STREAM_TOKEN_TTL_SEC,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _verify_ops_stream_token(token: str | None, lecture_id: str) -> str:
    if not token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="스트림 접근 토큰이 필요합니다.")
    try:
        payload = decode_token(token)
    except PyJWTError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="스트림 토큰이 유효하지 않습니다.")
    if payload.get("type") != "lecture-stream-ops" or payload.get("lec") != lecture_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="스트림 토큰이 유효하지 않습니다.")
    return str(payload.get("sub", ""))


# ---------------------------------------------------------------- 조회 공통
def _active_question_count(db: Session, lecture_id: str) -> int:
    return (
        db.query(func.count(LectureQuestion.id))
        .filter(
            LectureQuestion.lecture_id == lecture_id,
            LectureQuestion.status == "active",
        )
        .scalar()
        or 0
    )


def _progress_dict(p: LectureWatchProgress | None) -> dict | None:
    if p is None:
        return None
    return {
        "watched_max_sec": int(p.watched_max_sec or 0),
        "next_checkpoint_sec": p.next_checkpoint_sec,
        "checkpoints_passed": int(p.checkpoints_passed or 0),
        "status": p.status,
    }


def _get_active_lecture(db: Session, lecture_id: str) -> Lecture:
    lec = db.get(Lecture, lecture_id)
    if lec is None or lec.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="강의를 찾을 수 없어요.")
    return lec


# ================================================================ 학생
def _progress_map(db: Session, student_id: str, lecture_ids: list[str]) -> dict:
    return {
        p.lecture_id: p
        for p in db.query(LectureWatchProgress)
        .filter(
            LectureWatchProgress.student_id == student_id,
            LectureWatchProgress.lecture_id.in_(lecture_ids or [""]),
        )
        .all()
    }


def _student_lecture_item(db: Session, lec: Lecture, progress: LectureWatchProgress | None) -> dict:
    return {
        "id": lec.id,
        "title": lec.title,
        "description": lec.description,
        "subject": lec.subject,
        "order_no": int(lec.order_no or 0),
        "duration_sec": lec.duration_sec,
        "question_count": _active_question_count(db, lec.id),
        "progress": _progress_dict(progress),
    }


def _student_material_item(m: LectureMaterial) -> dict:
    """학생용 자료 항목 — file은 내부 경로 대신 다운로드 엔드포인트 경로만 노출."""
    item = {
        "id": m.id,
        "title": m.title,
        "kind": m.kind,
        "order_no": int(m.order_no or 0),
        "file_ext": m.file_ext,
        "file_bytes": int(m.file_bytes or 0),
    }
    if m.kind == "link":
        item["url"] = m.url  # 외부 URL — 프론트가 직접 새 탭으로 연다
    else:
        item["download_url"] = f"/api/v1/lectures/{m.lecture_id}/materials/{m.id}/download"
    return item


def _active_materials(db: Session, lecture_id: str) -> list[LectureMaterial]:
    return (
        db.query(LectureMaterial)
        .filter(
            LectureMaterial.lecture_id == lecture_id,
            LectureMaterial.status == "active",
        )
        .order_by(LectureMaterial.order_no, LectureMaterial.created_at)
        .all()
    )


@router.get("/lectures")
def list_lectures(
    subject: str | None = None,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    # 목차 정렬 — 같은 과목 안에서 order_no 오름차순이 1강·2강… 순서다.
    # order_no가 같으면(레거시 0 포함) 업로드 순(created_at asc)으로 안정 정렬.
    q = db.query(Lecture).filter(Lecture.status == "active")
    if subject:
        q = q.filter(Lecture.subject == subject)
    rows = q.order_by(Lecture.subject, Lecture.order_no, Lecture.created_at).all()

    progress = _progress_map(db, principal.id, [r.id for r in rows])
    return [_student_lecture_item(db, lec, progress.get(lec.id)) for lec in rows]


@router.get("/lectures/{lecture_id}")
def lecture_detail(
    lecture_id: str,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    lec = _get_active_lecture(db, lecture_id)
    progress = lecture_service.ensure_progress(db, principal.id, lec)
    db.commit()  # 최초 진입 시 진행 행(첫 체크포인트 예약 포함) 확정
    # 같은 과목의 강의 목차(사이드바용) — 목록과 동일한 (order_no, created_at) 오름차순
    toc_rows = (
        db.query(Lecture)
        .filter(Lecture.status == "active", Lecture.subject == lec.subject)
        .order_by(Lecture.order_no, Lecture.created_at)
        .all()
    )
    toc_progress = _progress_map(db, principal.id, [r.id for r in toc_rows])
    # 순수 조회 — 세션·stream_url을 주지 않는다. 상세만 열어 본 사용자가 다른 기기의
    # 시청을 차단(오탐)하지 않게, 재생 시작은 POST /session으로 명시적으로 분리했다.
    return {
        "id": lec.id,
        "title": lec.title,
        "description": lec.description,
        "subject": lec.subject,
        "order_no": int(lec.order_no or 0),
        "duration_sec": lec.duration_sec,
        "question_count": _active_question_count(db, lec.id),
        "progress": _progress_dict(progress),
        "next_checkpoint_sec": progress.next_checkpoint_sec,
        # 자료실 — file 자료는 내부 경로가 아니라 다운로드 엔드포인트 경로만 노출
        "materials": [_student_material_item(m) for m in _active_materials(db, lec.id)],
        # 과목 목차 — 강의실 사이드바가 바로 그릴 수 있는 형태(내 진행 포함)
        "toc": [
            _student_lecture_item(db, r, toc_progress.get(r.id)) for r in toc_rows
        ],
    }


def _session_response(progress: LectureWatchProgress, lec: Lecture, session_id: str) -> dict:
    """세션 발급/이어받기 공통 응답 — 서명 세션 토큰 + 세션 바인딩 stream_url + 진행 정본."""
    token = _sign_session_token(session_id, progress.student_id, lec.id)
    stream_token = _sign_stream_token(lec.id, progress.student_id, session_id)
    return {
        "ok": True,
        "session_id": session_id,  # 표시·디버깅용 — 인증은 오직 서명 토큰으로만 한다
        "session_token": token,  # 하트비트·takeover의 X-Lecture-Session 헤더 값
        "stream_url": f"/api/v1/lectures/{lec.id}/stream?t={stream_token}",
        "watched_max_sec": int(progress.watched_max_sec or 0),
        "next_checkpoint_sec": progress.next_checkpoint_sec,
        "checkpoints_passed": int(progress.checkpoints_passed or 0),
        "status": progress.status,
        "duration_sec": int(lec.duration_sec or 0),
    }


@router.post("/lectures/{lecture_id}/session")
def lecture_session_start(
    lecture_id: str,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """재생 시작 — 서버가 session_id를 발급(new_uuid)하고 서명 토큰으로 반환.

    클라이언트 생성 식별자는 받지 않는다(담합 우회 차단: 두 기기가 값을 짜고 와도
    각자 다른 서버 세션을 받게 되어 두 번째는 409). 같은 학생의 다른 활성 세션이
    살아 있으면 409(active_elsewhere) — 이어보기는 POST /takeover."""
    auth_service.rate_limit(
        db, f"lect-ss:{principal.id}", limit=RATE_SESSION_PER_HOUR, window_seconds=3600
    )
    lec = _get_active_lecture(db, lecture_id)
    progress = lecture_service.ensure_progress(db, principal.id, lec)
    session_id = new_uuid()  # 서버 발급 — 클라 입력이 끼어들 자리가 없다
    lecture_service.claim_session(db, progress, session_id)  # 동시 세션이면 409
    db.commit()
    return _session_response(progress, lec, session_id)


class _ProgressReq(BaseModel):
    position_sec: int
    # (제거됨) interacted — 상호작용 자기신고. 캡차 면제에만 쓰였고 그 면제를 걷어냈다.
    # 구버전 플레이어가 계속 보내도 pydantic이 조용히 무시한다(extra 무시가 기본).
    # 이유는 lecture_service의 '상호작용 면제: 제거됨' 주석 참조 — 요약하면 집중해서 보는
    # 학생은 아무것도 만지지 않아 면제가 도우려던 사람을 못 돕고, 위조 가능해 남용만 이득.
    # (제거됨 0717) tab_hidden — 탭 백그라운드 자기신고. suspicion(간격 축소)에만 쓰였고
    # 랜덤 간격과 함께 걷어냈다(lecture_service '랜덤 간격: 제거됨' 주석). 역시 조용히 무시.
    # (제거됨) session_id — 세션 식별은 X-Lecture-Session 서명 토큰으로만 한다.
    # 본문에 session_id를 실어 보내도 무시된다(pydantic이 미정의 필드를 버린다).


@router.post("/lectures/{lecture_id}/progress")
def lecture_progress(
    lecture_id: str,
    req: _ProgressReq,
    x_lecture_session: str | None = Header(default=None),
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """시청 하트비트 — 클라이언트 position은 참고값일 뿐, 서버가 검증한 정본을 돌려준다.

    세션 식별은 X-Lecture-Session 헤더의 서명 토큰(POST /session 발급)으로만 한다 —
    클라가 지어낸 session_id는 어디에도 끼지 못한다. 같은 학생의 다른 활성 세션
    (다른 강의 포함)이 살아 있으면 409(active_elsewhere) — 동시 재생은 캡차가 아니라
    차단이다. 이어보기는 POST /takeover."""
    auth_service.rate_limit(
        db, f"lect-hb:{principal.id}", limit=RATE_HEARTBEAT_PER_HOUR, window_seconds=3600
    )
    session_id = _verify_session_token(x_lecture_session, lecture_id, principal.id)
    lec = _get_active_lecture(db, lecture_id)
    progress = lecture_service.ensure_progress(db, principal.id, lec)
    lecture_service.claim_session(db, progress, session_id)  # 동시 세션이면 409
    state = lecture_service.advance(db, progress, lec, req.position_sec)
    db.commit()
    return state


@router.post("/lectures/{lecture_id}/takeover")
def lecture_takeover(
    lecture_id: str,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """이어보기 — 이전 활성 세션(새로고침 전 탭·다른 기기)을 무효화하고 '새' 서버 세션을
    발급해 이어받는다. 응답은 POST /session과 같은 형태(새 session_token·stream_url).

    호출자는 아직 유효한 세션 토큰이 없다(자기 발급 시도가 409를 받은 상태) — 그래서
    이 엔드포인트는 학생 JWT + 별도 상한(RATE_TAKEOVER_PER_HOUR)으로만 보호한다.
    두 세션이 번갈아 takeover를 스팸해 동시 차단을 우회하는 것은 이 상한이 막는다.
    무효화된 쪽의 다음 하트비트·스트림 요청은 각각 409/403을 받는다."""
    auth_service.rate_limit(
        db, f"lect-tk:{principal.id}", limit=RATE_TAKEOVER_PER_HOUR, window_seconds=3600
    )
    lec = _get_active_lecture(db, lecture_id)
    progress = lecture_service.ensure_progress(db, principal.id, lec)
    session_id = new_uuid()  # takeover도 서버 발급 — 이전 세션과 절대 겹치지 않는다
    lecture_service.claim_session(db, progress, session_id, force=True)
    db.commit()
    return _session_response(progress, lec, session_id)


@router.get("/lectures/{lecture_id}/stream")
def lecture_stream(
    lecture_id: str,
    t: str | None = None,
    db: Session = Depends(get_db),
):
    """영상 스트리밍 — 서명 토큰(쿼리) 인가 + 세션 활성 검사 후 FileResponse.

    토큰의 session_id가 현재 progress.session_id와 다르면 403 — takeover로 세션이
    교체된 순간 이전 기기의 스트림 URL이 즉시 죽는다(동시 차단이 '진도 인정'만이
    아니라 영상 바이트 전달에도 걸린다). 매 Range 요청마다 진행 행 1건을 조회하는
    비용은 유니크 인덱스(student_id, lecture_id) 단건 조회라 수용한다 — TTL(생존)
    검사는 하지 않는다: 일시정지로 하트비트가 끊겨도 세션이 교체되지 않았다면
    이어서 seek할 수 있어야 하고, 무효화의 정본은 takeover(세션 교체)이기 때문.

    starlette FileResponse가 Range(206 부분응답)를 네이티브 처리한다 — 인메모리
    lru_cache 서빙(정적 에셋 방식)은 영상엔 금지(RAM 폭발 + Range 미지원)."""
    student_id, session_id = _verify_stream_token(t, lecture_id)
    lec = _get_active_lecture(db, lecture_id)
    progress = (
        db.query(LectureWatchProgress)
        .filter(
            LectureWatchProgress.student_id == student_id,
            LectureWatchProgress.lecture_id == lecture_id,
        )
        .first()
    )
    if progress is None or not session_id or progress.session_id != session_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="이 시청 세션은 더 이상 유효하지 않습니다. 다른 곳에서 재생이 시작되었어요.",
        )
    media_type = _MEDIA_TYPES.get(lec.video_ext or "")
    if media_type is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="영상 형식이 올바르지 않습니다.")
    path = _video_path(lec)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="영상 파일을 찾을 수 없습니다.")
    return FileResponse(str(path), media_type=media_type)


@router.post("/ops/lectures/{lecture_id}/preview")
def ops_lecture_preview(
    lecture_id: str,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """운영자 미리보기 URL 발급 — 문항 시점을 눈으로 찾고 화면을 따오기 위한 재생.

    학생 재생과 달리 세션을 만들지 않는다: 운영자는 시청 검증 대상이 아니고, 여기서 세션을
    점유하면 같은 계정으로 강의를 보던 학생 세션을 걷어차게 된다."""
    lec = _get_ops_lecture(db, lecture_id, principal)
    if not lec.video_ext:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="업로드된 영상이 없습니다.")
    token = _sign_ops_stream_token(lec.id, principal.id)
    return {
        "stream_url": f"/api/v1/ops/lectures/{lec.id}/stream?t={token}",
        "duration_sec": lec.duration_sec,
    }


@router.get("/ops/lectures/{lecture_id}/stream")
def ops_lecture_stream(
    lecture_id: str,
    t: str | None = None,
    db: Session = Depends(get_db),
):
    """운영자 미리보기 스트리밍 — 서명 토큰(쿼리) 인가. 세션·진행 검사 없음.

    <video src>가 Authorization 헤더를 못 실어 쿼리 서명을 쓰는 것은 학생 스트림과 같다.
    토큰 type이 달라 학생 토큰으로는 못 들어온다(세션 바인딩 우회 차단).
    hidden/draft 강의도 열린다 — 운영자는 공개 전 검수를 해야 하고, _get_ops_lecture가
    이미 운영자 권한을 확인한 뒤 발급된 토큰이다."""
    _verify_ops_stream_token(t, lecture_id)
    lec = db.get(Lecture, lecture_id)
    if lec is None or lec.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="강의를 찾을 수 없습니다.")
    media_type = _MEDIA_TYPES.get(lec.video_ext or "")
    if media_type is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="영상 형식이 올바르지 않습니다.")
    path = _video_path(lec)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="영상 파일을 찾을 수 없습니다.")
    return FileResponse(str(path), media_type=media_type)


@router.get("/lectures/{lecture_id}/materials/{material_id}/download")
def lecture_material_download(
    lecture_id: str,
    material_id: str,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
):
    """file 자료 다운로드 — 경로는 자료 id(UUID)+화이트리스트 확장자로만 유도(경로조작 차단).

    항상 attachment + octet-stream으로 내려보낸다 — 브라우저 인라인 렌더(HTML/SVG류 XSS)를
    구조적으로 막는다. link 자료는 프론트가 url로 직접 이동하므로 이 엔드포인트 대상이 아니다."""
    _get_active_lecture(db, lecture_id)  # hidden/deleted 강의의 자료는 학생에게 닫힌다
    mat = db.get(LectureMaterial, material_id)
    if mat is None or mat.lecture_id != lecture_id or mat.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="자료를 찾을 수 없습니다.")
    if mat.kind != "file" or not mat.file_ext:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="링크 자료는 다운로드가 아니라 URL로 이동합니다."
        )
    path = _material_path(mat)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="자료 파일을 찾을 수 없습니다.")
    # 파일명은 제목+확장자 — 헤더를 깨는 문자(경로 구분자·따옴표·개행)만 치환
    safe_title = re.sub(r'[\\/\r\n"]', "_", mat.title).strip() or "material"
    return FileResponse(
        str(path),
        media_type="application/octet-stream",
        filename=f"{safe_title}{mat.file_ext}",
    )


@router.get("/lectures/{lecture_id}/questions/{question_id}/images/{image_id}")
def lecture_question_image(
    lecture_id: str,
    question_id: str,
    image_id: str,
    db: Session = Depends(get_db),
):
    """확인 문항 이미지 서빙(인라인) — 캡차 위젯·운영자 콘솔의 <img src>가 로드한다.

    인증 의존성이 없다: <img> 태그는 Authorization 헤더를 싣지 못하고, 경로가 강의·문항·
    이미지 세 UUID 조합이라 추측 불가하다(공개 캡차 /captcha/v1/img·audio와 동일한 공개
    서빙 원칙). 정답은 새지 않는다 — answer_index는 payload 밖 분리 컬럼이고 URL·응답
    어디에도 정오 신호가 없다(모든 보기 이미지가 같은 형태의 URL).

    경로는 payload에 기록된 참조(id는 서버 발급 UUID)+화이트리스트 확장자로만 유도한다
    (경로조작 원천 차단). 화이트리스트가 래스터 이미지뿐이라 인라인 렌더가 안전하다(SVG 금지).
    draft 문항·hidden 강의도 서빙한다(운영자 콘솔 미리보기) — deleted만 차단. 이미지 id는
    교체 시에도 새로 발급되어 불변이므로 캐시를 허용한다."""
    lec = db.get(Lecture, lecture_id)
    if lec is None or lec.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="이미지를 찾을 수 없습니다.")
    q = db.get(LectureQuestion, question_id)
    if q is None or q.lecture_id != lecture_id or q.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="이미지를 찾을 수 없습니다.")
    ref = next(
        (r for r in _question_image_refs(q.payload or {}) if r.get("id") == image_id),
        None,
    )
    if ref is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="이미지를 찾을 수 없습니다.")
    media_type = _QUESTION_IMAGE_MEDIA.get(str(ref.get("ext") or "").lower())
    if media_type is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="이미지를 찾을 수 없습니다.")
    path = _question_image_path(ref)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="이미지 파일을 찾을 수 없습니다.")
    return FileResponse(
        str(path),
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ================================================================ 운영자
def _lecture_row(db: Session, lec: Lecture) -> dict:
    total = (
        db.query(func.count(LectureQuestion.id))
        .filter(
            LectureQuestion.lecture_id == lec.id,
            LectureQuestion.status != "deleted",
        )
        .scalar()
        or 0
    )
    return {
        "id": lec.id,
        "title": lec.title,
        "description": lec.description,
        "subject": lec.subject,
        "course_id": lec.course_id,  # 소속 코스(없으면 None=미분류)
        "video_ext": lec.video_ext,
        "video_bytes": lec.video_bytes,
        "duration_sec": lec.duration_sec,
        "status": lec.status,
        "order_no": int(lec.order_no or 0),
        "question_count": int(total),
        # 0이면 확인(캡차)이 아예 안 떠서 시청 검증이 없는 강의 — 콘솔 경고의 근거
        "active_question_count": _active_question_count(db, lec.id),
        "created_at": lec.created_at.isoformat() if lec.created_at else None,
    }


# ================================================================ 강사 코스 CRUD
# 코스 = 한 강사가 한 과목으로 묶는 강의 묶음(예: '수학 기초반'). 코스=과목 고정
# (사용자 결정 0718). 강사는 자기 코스만, 운영자는 전체를 감독한다(강의 스코프와 동일
# 규약 — 남의 코스는 404로 존재 미노출). 학생 화면: 과목 → 강사별 코스 → 강의(order_no).
class _CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    subject: str  # 이 코스의 고정 과목 — 담기는 모든 강의가 이 과목이어야 한다
    description: str | None = Field(default=None, max_length=2000)


class _CourseUpdate(BaseModel):
    # 미전송(None)은 변경 안 함. subject는 못 바꾼다 — 코스=과목 고정이라 소속 강의와
    # 어긋나기 때문(바꾸려면 새 코스를 만든다).
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    order_no: int | None = None
    status: str | None = None  # active|hidden


def _get_ops_course(db: Session, course_id: str, principal: Principal) -> Course:
    """코스 로더 — 운영자는 전체, 강사는 자기 코스(instructor_id)만. 남의 코스는 403이
    아니라 404(강의 스코프 _get_ops_lecture와 동일 — 존재 여부를 흘리지 않는다)."""
    c = db.get(Course, course_id)
    if c is None or c.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="코스를 찾을 수 없습니다.")
    if principal.role == "instructor" and c.instructor_id != principal.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="코스를 찾을 수 없습니다.")
    return c


def _course_row(db: Session, c: Course) -> dict:
    lecture_count = (
        db.query(func.count(Lecture.id))
        .filter(Lecture.course_id == c.id, Lecture.status != "deleted")
        .scalar()
        or 0
    )
    return {
        "id": c.id,
        "title": c.title,
        "subject": c.subject,
        "description": c.description,
        "order_no": int(c.order_no or 0),
        "status": c.status,
        "instructor_id": c.instructor_id,
        "lecture_count": int(lecture_count),
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/ops/courses")
def ops_list_courses(
    principal: Principal = Depends(require_lecture_manager), db: Session = Depends(get_db)
):
    q = db.query(Course).filter(Course.status != "deleted")
    if principal.role == "instructor":
        q = q.filter(Course.instructor_id == principal.id)
    rows = q.order_by(Course.subject, Course.order_no, Course.created_at).all()
    return [_course_row(db, c) for c in rows]


@router.post("/ops/courses")
def ops_create_course(
    req: _CourseCreate,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """코스 생성 — 소유자는 생성한 본인(강사 또는 운영자). subject는 여기서 고정된다."""
    if req.subject not in EDU_SUBJECTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="지원하지 않는 과목입니다.")
    # 과목 내 맨 뒤 배정(학생 화면의 코스 나열 순서). 내 코스 기준 max+1.
    max_no = (
        db.query(func.max(Course.order_no))
        .filter(Course.subject == req.subject, Course.status != "deleted")
        .scalar()
        or 0
    )
    c = Course(
        instructor_id=principal.id,
        subject=req.subject,
        title=req.title.strip(),
        description=req.description,
        order_no=int(max_no) + 1,
        status="active",
    )
    db.add(c)
    db.flush()
    audit(
        db, action="course.create", actor_user_id=principal.id,
        target_type="course", target_id=c.id,
        after={"title": c.title, "subject": c.subject},
    )
    db.commit()
    return _course_row(db, c)


@router.put("/ops/courses/{course_id}")
def ops_update_course(
    course_id: str,
    req: _CourseUpdate,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    c = _get_ops_course(db, course_id, principal)
    if req.status is not None and req.status not in ("active", "hidden"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="status는 active|hidden만 가능합니다.")
    before = {"title": c.title, "order_no": c.order_no, "status": c.status}
    if req.title is not None:
        c.title = req.title.strip()
    if req.description is not None:
        c.description = req.description
    if req.order_no is not None:
        c.order_no = int(req.order_no)
    if req.status is not None:
        c.status = req.status
    audit(
        db, action="course.update", actor_user_id=principal.id,
        target_type="course", target_id=c.id,
        before=before, after={"title": c.title, "order_no": c.order_no, "status": c.status},
    )
    db.commit()
    return _course_row(db, c)


@router.delete("/ops/courses/{course_id}")
def ops_delete_course(
    course_id: str,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """코스 소프트 삭제 — 소속 강의는 미분류(course_id=NULL)로 풀어 준다(강의 자체는
    보존). 강의를 함께 지우지 않는 이유: 강의는 시청 이력·문항이 딸린 큰 자산이라
    코스 삭제가 실수여도 콘텐츠가 사라지면 안 된다."""
    c = _get_ops_course(db, course_id, principal)
    freed = (
        db.query(Lecture)
        .filter(Lecture.course_id == c.id, Lecture.status != "deleted")
        .update({Lecture.course_id: None}, synchronize_session=False)
    )
    c.status = "deleted"
    audit(
        db, action="course.delete", actor_user_id=principal.id,
        target_type="course", target_id=c.id,
        after={"lectures_unassigned": int(freed)},
    )
    db.commit()
    return {"ok": True, "lectures_unassigned": int(freed)}


@router.get("/ops/lectures")
def ops_list_lectures(
    principal: Principal = Depends(require_lecture_manager), db: Session = Depends(get_db)
):
    # 운영자 목록도 목차순 — 콘솔에서 보이는 순서가 학생 목차와 일치해야 재배열이 예측 가능하다.
    # 강사는 자기 강의(uploaded_by)만 — 남의 강의는 목록에서부터 존재하지 않는다.
    q = db.query(Lecture).filter(Lecture.status != "deleted")
    if principal.role == "instructor":
        q = q.filter(Lecture.uploaded_by == principal.id)
    rows = q.order_by(Lecture.subject, Lecture.order_no, Lecture.created_at).all()
    return [_lecture_row(db, lec) for lec in rows]


def _copy_upload_to_tmp(upload: UploadFile, tmp_path: Path, limit: int) -> int:
    """업로드를 임시파일로 청크 복사 — 누적 바이트가 limit를 넘으면 임시파일 삭제 + 413.

    전역 미들웨어는 Content-Length 헤더만 보므로(바디 미버퍼링), 헤더를 속인 초과 업로드는
    여기서 실제로 쓴 바이트 기준으로 잘라낸다."""
    total = 0
    try:
        with open(tmp_path, "wb") as f:
            while True:
                chunk = upload.file.read(_UPLOAD_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="업로드 파일이 허용 크기를 초과했습니다.",
                    )
                f.write(chunk)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return total


@router.post("/ops/lectures")
def ops_create_lecture(
    request: Request,
    title: str = Form(...),
    subject: str = Form(...),
    duration_sec: int = Form(...),
    description: str | None = Form(default=None),
    order_no: int | None = Form(default=None),  # 미지정 → 과목 맨 뒤(max+1)
    course_id: str | None = Form(default=None),  # 소속 코스(선택) — 미지정이면 미분류
    file: UploadFile = File(...),
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """강의 업로드(multipart) — 임시파일 청크 기록 → 원자적 이동 → DB commit.

    실패 시 파일을 남기지 않는다. 성공 응답은 실제 파일이 최종 경로에 존재할 때만 나간다."""
    auth_service.rate_limit(
        db, f"lect-upload:{_client_ip(request)}", limit=RATE_UPLOAD_PER_HOUR, window_seconds=3600
    )
    if subject not in EDU_SUBJECTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="지원하지 않는 과목입니다.")
    # 코스 지정 시 — 소유(강사는 자기 코스만: _get_ops_course가 404) + 과목 일치 강제
    # (코스=과목 고정: 수학 코스엔 수학 강의만).
    if course_id:
        course = _get_ops_course(db, course_id, principal)
        if course.subject != subject:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"이 코스는 '{course.subject}' 과목이라 '{subject}' 강의를 담을 수 없어요.",
            )
    if duration_sec <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="영상 길이(duration_sec)가 필요합니다.")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _MEDIA_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="mp4/webm 영상만 업로드할 수 있습니다."
        )
    if (file.content_type or "").lower() not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="영상 Content-Type(video/mp4·video/webm)이 아닙니다."
        )
    if order_no is None:
        # 목차 맨 뒤 자동 배정 — 같은 과목의 현재 최대 order_no + 1
        max_no = (
            db.query(func.max(Lecture.order_no))
            .filter(Lecture.subject == subject, Lecture.status != "deleted")
            .scalar()
            or 0
        )
        order_no = int(max_no) + 1

    lec = Lecture(
        title=title.strip()[:200],
        description=description,
        subject=subject,
        course_id=course_id or None,
        video_ext=ext,
        video_bytes=0,
        duration_sec=duration_sec,
        status="active",
        order_no=order_no,
        uploaded_by=principal.id,
    )
    db.add(lec)
    db.flush()  # id 확정 — 파일명은 {id}{ext}

    media_dir = _media_dir()
    media_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = media_dir / f".upload-{lec.id}.tmp"
    final_path = media_dir / f"{lec.id}{ext}"
    try:
        total = _copy_upload_to_tmp(file, tmp_path, get_settings().MAX_UPLOAD_BYTES)
        if total == 0:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="빈 파일은 업로드할 수 없습니다.")
        os.replace(tmp_path, final_path)  # 같은 디렉터리 내 원자적 이동
    except BaseException:
        db.rollback()  # 강의 행도 함께 폐기 — 파일 없는 유령 강의를 만들지 않는다
        tmp_path.unlink(missing_ok=True)
        raise

    try:
        lec.video_bytes = total
        audit(
            db,
            action="lecture.create",
            actor_user_id=principal.id,
            target_type="lecture",
            target_id=lec.id,
            after={
                "title": lec.title,
                "subject": lec.subject,
                "video_ext": ext,
                "video_bytes": total,
                "duration_sec": duration_sec,
                "order_no": order_no,
            },
        )
        db.commit()
    except BaseException:
        final_path.unlink(missing_ok=True)  # DB 확정 실패 — 고아 파일 제거
        raise
    return _lecture_row(db, lec)


class _LectureUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    subject: str | None = None
    duration_sec: int | None = None
    # (제거됨 0717) check_min_sec/check_max_sec — 무작위 확인 간격. 전부 핀 구조로
    # 간격 개념이 사라졌다. 구버전 콘솔이 보내도 pydantic이 조용히 무시한다.
    order_no: int | None = None  # 과목 내 목차 순서 재배열
    status: str | None = None  # active|hidden
    # 소속 코스 변경. model_fields_set으로 '미전송'과 '명시적 null(미분류로 빼기)'을 구분.
    course_id: str | None = None


@router.put("/ops/lectures/{lecture_id}")
def ops_update_lecture(
    lecture_id: str,
    req: _LectureUpdate,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """메타만 수정 — 영상 파일 교체는 별도 업로드(새 강의)로 처리한다."""
    lec = _get_ops_lecture(db, lecture_id, principal)  # 강사는 자기 강의만(스코프)
    if req.subject is not None and req.subject not in EDU_SUBJECTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="지원하지 않는 과목입니다.")
    if req.status is not None and req.status not in ("active", "hidden"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="status는 active|hidden만 가능합니다.")
    # 코스 변경 — 명시적으로 전송된 경우만(미전송이면 유지). null이면 미분류로 뺀다.
    # 코스를 지정하면 소유 확인 + 과목 일치 강제(변경될 subject 기준). 코스=과목 고정.
    if "course_id" in req.model_fields_set:
        if req.course_id:
            course = _get_ops_course(db, req.course_id, principal)
            eff_subject = req.subject if req.subject is not None else lec.subject
            if course.subject != eff_subject:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=f"이 코스는 '{course.subject}' 과목이라 '{eff_subject}' 강의를 담을 수 없어요.",
                )
            lec.course_id = req.course_id
        else:
            lec.course_id = None

    before = {
        "title": lec.title, "subject": lec.subject, "status": lec.status,
        "duration_sec": lec.duration_sec, "order_no": lec.order_no,
    }
    if req.title is not None:
        lec.title = req.title.strip()[:200]
    if req.description is not None:
        lec.description = req.description
    if req.subject is not None:
        lec.subject = req.subject
    if req.duration_sec is not None:
        if req.duration_sec <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="영상 길이가 올바르지 않습니다.")
        # 길이를 줄이면 그 밖으로 나간 문항은 영영 안 뜨고, 그게 마지막 문항이면 이 강의의
        # 시청 검증이 통째로 조용히 꺼진다. 문항 PUT은 같은 상황을 400으로 막으면서 여기만
        # 통과시키면 비대칭이라, 강사는 설명만 고치려다 영문 모를 400을 맞는다.
        orphaned = (
            db.query(func.count(LectureQuestion.id))
            .filter(
                LectureQuestion.lecture_id == lec.id,
                LectureQuestion.status == "active",
                LectureQuestion.position_sec >= req.duration_sec,
            )
            .scalar()
        )
        if orphaned:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"출제 시점이 새 영상 길이를 벗어나는 공개 문항이 {orphaned}개 있습니다. 문항 시점을 먼저 정리해 주세요.",
            )
        lec.duration_sec = req.duration_sec
    if req.order_no is not None:
        if req.order_no < 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="order_no는 0 이상이어야 합니다.")
        lec.order_no = req.order_no
    if req.status is not None:
        lec.status = req.status

    # duration을 줄이면 이미 예약된 체크포인트가 영상 밖에 남아 그 강의를 영원히 완주 못 하게 된다
    # (게이트가 도달 불가 → watched_max가 duration에 닿아도 status가 done으로 안 넘어감).
    # 영상 밖으로 밀려난 예약만 해제한다 — 다음 하트비트가 새 지점을 다시 잡는다.
    if req.duration_sec is not None:
        (
            db.query(LectureWatchProgress)
            .filter(
                LectureWatchProgress.lecture_id == lec.id,
                LectureWatchProgress.next_checkpoint_sec.isnot(None),
                LectureWatchProgress.next_checkpoint_sec >= lec.duration_sec,
            )
            .update({"next_checkpoint_sec": None}, synchronize_session=False)
        )

    audit(
        db,
        action="lecture.update",
        actor_user_id=principal.id,
        target_type="lecture",
        target_id=lec.id,
        before=before,
        after={
            "title": lec.title, "subject": lec.subject, "status": lec.status,
            "duration_sec": lec.duration_sec, "order_no": lec.order_no,
        },
    )
    db.commit()
    return _lecture_row(db, lec)


@router.delete("/ops/lectures/{lecture_id}")
def ops_delete_lecture(
    lecture_id: str,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """소프트 삭제 + 영상·자료 파일 물리 삭제 — 레코드·시청 이력·문항·자료 행은 보존
    (status=deleted로 노출만 차단)하되, 무거운 영상/자료 파일은 디스크에서 지운다(삭제된
    강의는 status 필터로 재생·조회가 안 되므로 파일 부재가 무해하다).

    자료(material)도 함께 소프트 삭제한다 — 부모 강의가 deleted면 자료 CRUD 경로가 전부
    404가 되어(자료는 부모 강의 존재를 요구) 자료를 개별로 지울 방법이 사라지므로, 여기서
    같이 정리하지 않으면 자료 파일이 영구 고아로 남는다(바로 이 기능이 막으려는 디스크 누수).

    파일 삭제는 commit '성공 후'에 한다 — commit 전에 지우면 commit 실패 시 파일은 없는데
    레코드는 active로 남는 최악이 된다. 파일 부재는 status=deleted + *_bytes=0으로
    나타내고, 원래 크기는 감사 로그 before에 남긴다."""
    lec = _get_ops_lecture(db, lecture_id, principal)  # 강사는 자기 강의만(스코프)
    video_path = _video_path(lec)  # commit 후에는 속성이 만료되므로 경로를 미리 확정
    file_existed = video_path.is_file()

    # 이 강의의 살아있는 자료 — 함께 소프트 삭제하고 file 종류는 파일 경로를 미리 모은다
    materials = (
        db.query(LectureMaterial)
        .filter(
            LectureMaterial.lecture_id == lec.id,
            LectureMaterial.status != "deleted",
        )
        .all()
    )
    material_paths: list[Path] = []
    for m in materials:
        if m.kind == "file" and m.file_ext:
            material_paths.append(_material_path(m))
        m.status = "deleted"
        m.file_bytes = 0

    # 문항 이미지도 함께 물리 삭제 — 부모 강의가 deleted면 문항 CRUD 경로가 전부 404가 되어
    # 이미지를 개별로 지울 방법이 사라진다(자료실 고아 파일 버그와 동형). 문항 행·payload는
    # 보존한다. deleted 문항의 파일은 이미 지워졌지만 unlink가 멱등이라 전수 수집이 단순·안전하다.
    question_rows = (
        db.query(LectureQuestion).filter(LectureQuestion.lecture_id == lec.id).all()
    )
    question_image_paths: list[Path] = [
        _question_image_path(r)
        for qr in question_rows
        for r in _question_image_refs(qr.payload or {})
    ]

    before = {"status": lec.status, "video_bytes": int(lec.video_bytes or 0)}
    lec.status = "deleted"
    lec.video_bytes = 0
    audit(
        db,
        action="lecture.delete",
        actor_user_id=principal.id,
        target_type="lecture",
        target_id=lec.id,
        before=before,
        after={
            "status": "deleted",
            "video_file_removed": file_existed,
            "materials_deleted": len(materials),
            "question_images_removed": len(question_image_paths),
        },
    )
    db.commit()
    video_path.unlink(missing_ok=True)  # 이미 없어도 무해(멱등)
    for p in material_paths:
        p.unlink(missing_ok=True)
    for p in question_image_paths:
        p.unlink(missing_ok=True)
    return {"ok": True}


# ---------------------------------------------------------------- 문항 CRUD
def _get_ops_lecture(db: Session, lecture_id: str, principal: Principal) -> Lecture:
    """강의 제작 도메인 공통 로더 — 운영자는 전체, 강사는 자기 강의(uploaded_by)만.

    강사에게 남의 강의는 403이 아니라 404다 — 존재 여부 자체를 흘리지 않는다
    (id는 UUID지만 로그·링크 유출 시 열거 단서가 되지 않게)."""
    lec = db.get(Lecture, lecture_id)
    if lec is None or lec.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="강의를 찾을 수 없습니다.")
    if principal.role == "instructor" and lec.uploaded_by != principal.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="강의를 찾을 수 없습니다.")
    return lec


def _question_row(q: LectureQuestion) -> dict:
    # 운영자 편집 화면용 — answer_index 포함(학생 노출 경로가 아님)
    payload = q.payload or {}
    options = payload.get("options", [])
    opt_imgs = payload.get("option_images") or {}
    pi = payload.get("prompt_image")
    return {
        "id": q.id,
        "lecture_id": q.lecture_id,
        "position_sec": q.position_sec,
        # 되감기 지점(내용 시작) — null = 미지정(cp-REWIND_SEC 폴백)
        "content_start_sec": q.content_start_sec,
        "prompt": payload.get("prompt"),
        "options": options,
        "explain": payload.get("explain"),
        "answer_index": q.answer_index,
        # 콘솔 편집용 유효 정답 목록 — NULL(단일 정답 행)도 [answer_index]로 채워 내려
        # 콘솔이 항상 이 필드 하나로 체크박스를 그린다(운영자 화면은 정답 노출이 정상 — 학생 경로 아님)
        "answer_indexes": [int(i) for i in (q.answer_indexes or [q.answer_index])],
        "source": q.source,
        "status": q.status,
        "order_no": q.order_no,
        # 자기검증(2번째 LLM) 판정 — LLM 자동 생성 문항에만 있다(수기 문항은 None).
        # solver_passed = 블라인드(셔플 다수결)로 풀렸는지, transcript_solver_passed =
        # 자막을 주면 풀리는지(False면 불량 의심). suggested_placement = captcha|bank|discard.
        "solver_passed": payload.get("solver_passed"),
        "transcript_solver_passed": payload.get("transcript_solver_passed"),
        "suggested_placement": payload.get("suggested_placement"),
        "solver_meta": payload.get("solver_meta"),
        # 은행 배치 이력 — {bank_id, at} 또는 None. 콘솔 '은행 배치됨' 배지·중복 방지 근거.
        "bank_placed": payload.get("bank_placed"),
        # 이미지 문항 — 내부 경로·id 원문 대신 서빙 엔드포인트 URL만 노출(자료실 download_url과 동일 원칙)
        "prompt_image_url": (
            _question_image_url(q.lecture_id, q.id, pi)
            if isinstance(pi, dict) and pi.get("id")
            else None
        ),
        # 보기와 같은 길이의 리스트(이미지 없는 보기는 None) — 콘솔이 인덱스로 바로 그린다
        "option_image_urls": [
            (
                _question_image_url(q.lecture_id, q.id, opt_imgs[str(i)])
                if isinstance(opt_imgs.get(str(i)), dict) and opt_imgs[str(i)].get("id")
                else None
            )
            for i in range(len(options))
        ],
    }


class _QuestionCreate(BaseModel):
    # 출제 시점(핀) — active면 1 이상·영상 안이어야 한다. draft는 0 허용(시점 미배치 —
    # LLM 생성 문항이 검수를 기다리는 상태. 활성화할 때 시점 지정이 강제된다).
    position_sec: int = 0
    # 되감기 지점(내용 시작 시점) — 오답 상한 도달 시 여기로 되감는다. None = 미지정
    # (cp-REWIND_SEC 폴백). 지정 시 0 <= 값 < position_sec 강제(cp 이상 '되감기'는
    # 재시청 없는 무한 재도전이 된다).
    content_start_sec: int | None = None
    # (제거됨 0717) pinned — 이제 모든 문항이 핀이다. 구버전 콘솔이 보내도 조용히 무시된다.
    # (제거됨 0717) window_sec — 구간 출제. 되감기(cp-REWIND_SEC) 기준과 내용 시점이
    # 어긋나는 버그로 고정만 남겼다(lecture_service '구간 출제: 제거됨' 주석). 역시 무시.
    prompt: str
    options: list[str]
    answer_index: int
    # 다답형 정답 목록 — 보내면 이것이 정본(집합 정확 일치 채점, 부분 정답 없음)이고
    # answer_index는 첫 값으로 함께 채워진다(구버전 읽기 경로 하위호환). 안 보내면 단일 정답.
    answer_indexes: list[int] | None = None
    explain: str | None = None
    status: str = "active"  # draft|active


def _effective_answer_indexes(
    answer_indexes: list[int] | None, answer_index: int
) -> list[int]:
    """저장·검증에 쓰는 유효 정답 목록 — answer_indexes가 오면 그것, 없으면 [answer_index]."""
    if answer_indexes is not None:
        return [int(i) for i in answer_indexes]
    return [int(answer_index)]


def _validate_question_body(
    prompt: str,
    options: list[str],
    answer_indexes: list[int],
    option_images: dict | None = None,
) -> None:
    if not prompt.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="문제(prompt)가 비어 있습니다.")
    if not (2 <= len(options) <= 6):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="보기는 2~6개여야 합니다.")
    # 보기 텍스트는 원칙 필수 — 이미지가 붙은 보기만 텍스트 생략 허용(그림 보기 문항:
    # "방금 화면에 나온 도형은?"에 텍스트 라벨을 강제하면 안 본 사람도 상식으로 찍는다).
    # 생성 시점엔 이미지가 없으므로(첨부는 별도 엔드포인트) 사실상 종전과 동일하게 강제된다.
    imgs = option_images or {}
    if any(not str(o).strip() and str(i) not in imgs for i, o in enumerate(options)):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="이미지가 없는 보기는 텍스트가 비어 있으면 안 됩니다.",
        )
    if not answer_indexes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="정답을 최소 1개 지정해야 합니다.")
    if len(set(answer_indexes)) != len(answer_indexes):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="정답 목록에 같은 보기가 중복돼 있습니다.")
    if any(not (0 <= i < len(options)) for i in answer_indexes):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="정답 인덱스가 보기 범위를 벗어납니다.")


def _validate_question_timing(
    position_sec: int, duration_sec: int, *, active: bool
) -> None:
    """출제 시점(핀)이 실제로 도달 가능한지 — 뜰 수 없는 문항은 조용히 죽는 대신 거절한다.

    ★ position이 영상 밖이면 그 문항은 영영 안 뜨는 데서 끝나지 않고, 그게 유일한
    문항이면 체크포인트 자체가 안 잡혀 시청 검증이 통째로, 아무 신호 없이 꺼진다
    (100초 강의에 900 오타 하나면 충분 — 적대적 검토에서 실증). 목록에는 멀쩡한
    active 문항으로 보이므로 알아챌 방법이 없다.

    ★ 두 검사 모두 active일 때만 강제한다 — draft는 '시점 미배치·후보' 상태다.
    범위 검사까지 draft에 걸면, 운영자가 영상 길이를 줄인 뒤(orphan 검사는 active만
    센다) 밖에 남은 draft가 좌초한다: 프롬프트만 고치는 PUT도 영문 모를 400을 맞고,
    그 draft는 저장 불가능한 상태로 갇힌다(skeptic 실증). 뜰 수 없는 시점은 공개
    (활성화) 시점에 반드시 걸러진다 — 그게 이 함수가 지키는 유일한 불변식이다.
    (0초 핀: watched < pin 판정이라 활성화돼도 영영 안 뜨므로 공개 전에 걸러야 한다.)
    """
    if active and duration_sec and position_sec >= duration_sec:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="출제 시점이 영상 길이를 벗어났습니다. 영상 안의 시점을 지정해 주세요.",
        )
    if active and position_sec < 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="공개(active) 문항은 출제 시점이 1초 이상이어야 합니다(0초는 아직 아무것도 보지 않은 지점이라 뜰 수 없어요). 시점을 지정한 뒤 공개해 주세요.",
        )


def _validate_content_start(content_start_sec: int | None, position_sec: int) -> None:
    """되감기 지점(내용 시작)은 반드시 출제 시점보다 앞 — cp 이상 '되감기'는 watched가
    그대로 cp 이상이라 게이트가 즉시 재발급되고, 재시청 없는 무한 재도전(보기 전수
    대입)이 부활한다. 서비스의 방어적 클램프(cp-1)와 별개로 입력 단계에서 거절한다."""
    if content_start_sec is None:
        return
    if content_start_sec < 0 or content_start_sec >= position_sec:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="내용 시작(되감기) 시점은 출제 시점보다 앞이어야 합니다. 문항이 다루는 내용이 시작되는 시점을 지정해 주세요.",
        )

def _reject_duplicate_pin(
    db: Session, lecture_id: str, position_sec: int, exclude_id: str | None = None
) -> None:
    """같은 시점에 핀 문항 둘 — 하나만 뜨고 나머지는 죽는다. 조용히 버리지 말고 거절한다.

    한 시점에는 하나만 출제되고(random.choice), 통과하면 그 시점은 다시 안 잡히므로
    나머지는 영구 사문이 된다. 강사 목록에는 active로 멀쩡히 보여 알 수 없다
    (적대적 검토에서 실증). 같은 대목을 여러 문항으로 묻고 싶으면 시점을 몇 초씩
    달리 두면 된다(각각 별도 체크포인트로 순서대로 뜬다).
    """
    dup = (
        db.query(func.count(LectureQuestion.id))
        .filter(
            LectureQuestion.lecture_id == lecture_id,
            LectureQuestion.status == "active",
            LectureQuestion.position_sec == position_sec,
            *([LectureQuestion.id != exclude_id] if exclude_id else []),
        )
        .scalar()
    )
    if dup:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="같은 시점에 출제되는 공개 문항이 이미 있습니다. 한 시점에는 하나만 출제되니 다른 시점을 지정해 주세요.",
        )


def _reconcile_progress(db: Session, lecture_id: str) -> None:
    """문항 구성이 바뀐 뒤 학생들의 예약을 정합화한다 — 문항 생성·수정·삭제 후 호출.

    해제된 예약은 다음 하트비트가 새 구성으로 다시 잡는다(record_progress의 재예약 경로).
    이미 지나온 지점은 건드리지 않는다 — 지난 구간을 소급해 다시 묻지는 않는다.

    ① 낼 문제가 없어진 예약: 그대로 두면 학생이 그 지점에 닿아도 출제할 문항이 없어
       게이트는 4xx만 내고, 진행은 cp+GRACE에서 클램프돼 강의를 영영 못 끝낸다
       (강사가 마지막 문항을 지우거나 시점을 뒤로 옮기면 실제로 발생).
    ② 핀 시점을 지나쳐 버릴 예약: 예약이 핀 시점보다 뒤에 있으면 학생은 지정 시점을
       그냥 통과해 핀이 무의미해진다.
    """
    pins = lecture_service.question_pins(db, lecture_id)
    cp_col = LectureWatchProgress.next_checkpoint_sec

    # ① 그 지점에 낼 수 있는 문항이 없는 예약 — 핀 문항은 정확히 자기 시점에서만 나온다.
    #    어느 핀과도 일치하지 않으면 그 예약으로는 게이트를 열 수 없다.
    unservable = db.query(LectureWatchProgress).filter(
        LectureWatchProgress.lecture_id == lecture_id, cp_col.isnot(None)
    )
    if pins:
        unservable = unservable.filter(not_(cp_col.in_(pins)))
    unservable.update({"next_checkpoint_sec": None}, synchronize_session=False)

    # ② 아직 안 닿은 핀을 통째로 건너뛰는 예약 — 핀에 정확히 걸린 예약은 유효하니 둔다.
    #    '안 닿았다'는 watched만으로 판정하지 않는다: 되감긴 학생은 이미 통과한 핀
    #    아래로 내려가 있어(watched < 통과한 핀 < cp), watched 기준만 쓰면 유효한 재도전
    #    예약(cp)이 해제되고 재예약이 통과한 핀을 다시 잡는다 — 운영자가 아무 문항이나
    #    수정해도 재현되는 소급 재출제(skeptic 실증). 통과 이벤트가 있는 핀은 건너뛴
    #    것이 아니라 지나온 것이므로 해제 사유가 아니다.
    for pin in pins:
        passed_this_pin = (
            db.query(LectureCheckpointEvent.id)
            .filter(
                LectureCheckpointEvent.student_id == LectureWatchProgress.student_id,
                LectureCheckpointEvent.lecture_id == lecture_id,
                LectureCheckpointEvent.result == "passed",
                LectureCheckpointEvent.position_sec == pin,
            )
            .exists()
        )
        (
            db.query(LectureWatchProgress)
            .filter(
                LectureWatchProgress.lecture_id == lecture_id,
                cp_col.isnot(None),
                cp_col > pin,
                LectureWatchProgress.watched_max_sec < pin,
                not_(passed_this_pin),
            )
            .update({"next_checkpoint_sec": None}, synchronize_session=False)
        )


@router.get("/ops/lectures/{lecture_id}/questions")
def ops_list_questions(
    lecture_id: str,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    _get_ops_lecture(db, lecture_id, principal)
    rows = (
        db.query(LectureQuestion)
        .filter(
            LectureQuestion.lecture_id == lecture_id,
            LectureQuestion.status != "deleted",
        )
        .order_by(LectureQuestion.position_sec, LectureQuestion.order_no)
        .all()
    )
    return [_question_row(q) for q in rows]


@router.post("/ops/lectures/{lecture_id}/questions")
def ops_create_question(
    lecture_id: str,
    req: _QuestionCreate,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    lec = _get_ops_lecture(db, lecture_id, principal)
    ans_ids = _effective_answer_indexes(req.answer_indexes, req.answer_index)
    _validate_question_body(req.prompt, req.options, ans_ids)
    if req.status not in ("draft", "active"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="status는 draft|active만 가능합니다.")
    position = max(0, req.position_sec)
    _validate_question_timing(
        position, int(lec.duration_sec or 0), active=req.status == "active"
    )
    _validate_content_start(req.content_start_sec, position)
    if req.status == "active":
        _reject_duplicate_pin(db, lec.id, position)
    q = LectureQuestion(
        lecture_id=lec.id,
        position_sec=position,
        content_start_sec=req.content_start_sec,
        payload={
            "prompt": req.prompt.strip(),
            "options": [str(o).strip() for o in req.options],
            "explain": (req.explain or "").strip(),
        },
        # answer_indexes를 받았을 때만 목록을 저장(NULL=단일 정답 하위호환 규약).
        # answer_index는 항상 첫 값으로 채워 구버전 읽기 경로가 깨지지 않는다.
        answer_index=ans_ids[0],
        answer_indexes=ans_ids if req.answer_indexes is not None else None,
        source="manual",
        status=req.status,
        order_no=0,
    )
    db.add(q)
    db.flush()
    _reconcile_progress(db, lec.id)
    audit(
        db,
        action="lecture.question.create",
        actor_user_id=principal.id,
        target_type="lecture_question",
        target_id=q.id,
        after={
            "lecture_id": lec.id,
            "position_sec": q.position_sec,
            "content_start_sec": q.content_start_sec,
            "status": q.status,
        },
    )
    db.commit()
    return _question_row(q)


class _QuestionUpdate(BaseModel):
    position_sec: int | None = None
    # None에 두 의미가 있어 model_fields_set으로 구분한다: 미전송 = 변경 없음,
    # 명시적 null = 지정 해제(폴백 되감기로 복귀). 콘솔은 저장 시 항상 명시로 보낸다.
    content_start_sec: int | None = None
    prompt: str | None = None
    options: list[str] | None = None
    answer_index: int | None = None
    # 보내면 다답 정답 목록으로 교체(answer_index도 첫 값으로 동기화). 안 보내고
    # answer_index만 보내면 단일 정답으로 전환(스테일 목록이 남지 않게 목록을 지운다).
    answer_indexes: list[int] | None = None
    explain: str | None = None
    status: str | None = None  # draft|active


@router.put("/ops/lectures/{lecture_id}/questions/{question_id}")
def ops_update_question(
    lecture_id: str,
    question_id: str,
    req: _QuestionUpdate,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    # FOR UPDATE(_get_ops_question) — 이 PUT도 payload(JSON)를 통째로 읽고-고쳐-재할당하므로
    # 이미지 첨부/삭제와 같은 잠금 아래 있어야 한다. 잠금 없이는 동시 첨부가 커밋한 참조를
    # 이 핸들러의 스냅샷이 덮어써 파일이 어떤 삭제 연쇄로도 못 닿는 영구 고아가 된다.
    q = _get_ops_question(db, lecture_id, question_id, principal)
    if req.status is not None and req.status not in ("draft", "active"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="status는 draft|active만 가능합니다.")

    payload = dict(q.payload or {})
    new_prompt = req.prompt if req.prompt is not None else payload.get("prompt", "")
    new_options = req.options if req.options is not None else payload.get("options", [])
    # 유효 정답 목록 산출 — 우선순위: answer_indexes(다답 교체) > answer_index(단일 전환:
    # 스테일 목록이 남아 채점과 어긋나지 않게 목록을 지운다) > 기존 값 유지.
    if req.answer_indexes is not None:
        new_answer_indexes: list[int] | None = [int(i) for i in req.answer_indexes]
    elif req.answer_index is not None:
        new_answer_indexes = None
    else:
        new_answer_indexes = list(q.answer_indexes) if q.answer_indexes else None
    if req.answer_index is not None and req.answer_indexes is None:
        new_answer = int(req.answer_index)
    elif new_answer_indexes:
        new_answer = new_answer_indexes[0]
    else:
        new_answer = int(q.answer_index)
    # 검증 대상 — answer_indexes를 명시로 보냈으면 빈 목록도 그대로 태워 400으로 거절한다
    # ([] or [단일값] 폴백이면 '정답 0개' 요청이 기존 정답으로 조용히 대체돼 검증을 우회한다)
    eff_ids = (
        new_answer_indexes if req.answer_indexes is not None else (new_answer_indexes or [new_answer])
    )

    # 보기 축소 시 범위 밖 보기의 이미지 참조를 함께 정리한다 — 참조만 지우고 파일을 두면
    # 고아 파일(자료실에서 겪은 디스크 누수)이 되므로 commit '성공 후' 물리 삭제한다.
    removed_image_paths: list[Path] = []
    opt_imgs = dict(payload.get("option_images") or {})
    if req.options is not None and opt_imgs:
        for k in list(opt_imgs):
            if not str(k).isdigit() or int(k) >= len(new_options):
                removed_image_paths.append(_question_image_path(opt_imgs.pop(k)))
        if opt_imgs:
            payload["option_images"] = opt_imgs
        else:
            payload.pop("option_images", None)
    _validate_question_body(
        str(new_prompt), list(new_options), eff_ids, option_images=opt_imgs
    )

    before = {
        "position_sec": q.position_sec,
        "content_start_sec": q.content_start_sec,
        "status": q.status,
        "answer_index": q.answer_index,
        "answer_indexes": q.answer_indexes,
    }
    payload["prompt"] = str(new_prompt).strip()
    payload["options"] = [str(o).strip() for o in new_options]
    if req.explain is not None:
        payload["explain"] = req.explain.strip()
    q.payload = payload
    q.answer_index = int(new_answer)
    q.answer_indexes = new_answer_indexes
    if req.position_sec is not None:
        q.position_sec = max(0, req.position_sec)
    if "content_start_sec" in req.model_fields_set:
        q.content_start_sec = req.content_start_sec  # 명시적 null = 지정 해제(폴백 복귀)
    if req.status is not None:
        q.status = req.status
    lec = db.get(Lecture, lecture_id)
    # 검증은 갱신 '후' 최종 상태로 — position만 옮겨 기존 content_start와 어긋나는
    # 조합(내용 시작 >= 출제 시점)도 여기서 걸린다.
    _validate_question_timing(
        int(q.position_sec),
        int(lec.duration_sec or 0) if lec else 0,
        active=q.status == "active",
    )
    _validate_content_start(q.content_start_sec, int(q.position_sec))
    if q.status == "active":
        _reject_duplicate_pin(db, lecture_id, int(q.position_sec), exclude_id=q.id)
    # 시점·고정 여부·활성 상태 중 무엇이 바뀌었든 예약 정합화 — 어떤 조합이 바뀌었는지
    # 일일이 따지면 초안→활성 같은 경로가 빠진다(실제로 빠뜨렸다). 현재 구성으로 다시 맞춘다.
    db.flush()
    _reconcile_progress(db, lecture_id)

    audit(
        db,
        action="lecture.question.update",
        actor_user_id=principal.id,
        target_type="lecture_question",
        target_id=q.id,
        before=before,
        after={
            "position_sec": q.position_sec,
            "content_start_sec": q.content_start_sec,
            "status": q.status,
            "answer_index": q.answer_index,
            "answer_indexes": q.answer_indexes,
        },
    )
    db.commit()
    for p in removed_image_paths:
        p.unlink(missing_ok=True)  # commit 성공 후 — 실패 시 참조·파일 정합 유지(멱등)
    return _question_row(q)


@router.delete("/ops/lectures/{lecture_id}/questions/{question_id}")
def ops_delete_question(
    lecture_id: str,
    question_id: str,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    # FOR UPDATE(_get_ops_question) — payload에서 이미지 경로를 수집한 뒤 상태를 바꾸므로,
    # 잠금 없이 동시 첨부와 겹치면 stale payload에서 수집해 새 파일을 놓친다(고아).
    q = _get_ops_question(db, lecture_id, question_id, principal)
    # 문항 이미지는 물리 삭제(강의·자료와 동일 원칙) — 레코드·payload(참조 포함)는 이력으로
    # 보존한다. deleted 문항은 서빙·출제 경로가 전부 status 필터로 닫혀 파일 부재가 무해하다.
    image_paths = [_question_image_path(r) for r in _question_image_refs(q.payload or {})]
    q.status = "deleted"
    # 지운 문항에 기대고 있던 예약을 걷는다 — 마지막 문항을 지우면 학생이 게이트에 닿아도
    # 낼 문제가 없어 4xx만 나고 진행은 클램프에 갇힌다(문항 0개 = 검증 없음이 정직한 상태).
    db.flush()
    _reconcile_progress(db, lecture_id)
    audit(
        db,
        action="lecture.question.delete",
        actor_user_id=principal.id,
        target_type="lecture_question",
        target_id=q.id,
        after={"status": "deleted", "image_files_removed": len(image_paths)},
    )
    db.commit()
    for p in image_paths:
        p.unlink(missing_ok=True)  # commit 성공 후 — 이미 없어도 무해(멱등)
    return {"ok": True}


@router.post("/ops/lectures/{lecture_id}/questions/{question_id}/to-bank")
def ops_place_question_to_bank(
    lecture_id: str,
    question_id: str,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """강의 문항 → 전체학습 문제은행 배치 — 자기검증 '은행 적합' 판정의 실행 단계.

    왜 이 기능인가: 자기검증(2번째 LLM)이 '상식으로 풀리는' 문항을 걸러내면 그건
    시청 검증(캡차)엔 부적합하지만 버릴 필요는 없다 — 전체학습 지식 문제로 재활용한다.
    (설계 전체 그림: docs/lecture-question-pipeline.md)

    형식 변환이 이 엔드포인트의 존재 이유다. 강의 문항과 은행 문항은 스키마가 다르다:
      강의: options=["가","나"] · answer_index=0        (인덱스 기반)
      은행: options=[{id:"o1",text:"가"}] · answer="o1" (옵션 id 기반, type="single")
    변환을 잘못하면 학생 게임 화면에서 문항이 깨지므로 여기 한 곳에서만 변환한다.

    정직성 규약:
    - DB 은행이 비어 있으면(파일 폴백 가동 중) 409 — 이 상태에서 1행을 넣으면 다음
      재기동 때 로더가 'DB에 문항 있음'으로 판단해 그 1행이 은행 전체가 돼 버린다
      (파일 은행 1,000+문항 증발). 은행 시드(로더) 선행이 필수라는 사실을 숨기지 않는다.
    - 배치 직후 런타임 은행을 갱신(subject_banks.refresh_from_db)해 재기동 없이 반영
      한다 — 갱신 실패면 응답에 runtime_visible=false로 정직하게 노출.
    한계(문서에도 기록): 다답형·이미지 문항은 은행 single 형식이 못 담아 400.
    배치된 문항은 은행 리스트 말미(order_no=말미)라 기존 주간 챕터를 흔들지 않는다 —
    챕터 구조 재편은 별도 단계(전체학습 재편)."""
    from app.models import Question
    from app.services import subject_banks

    q = _get_ops_question(db, lecture_id, question_id, principal)
    lec = db.get(Lecture, lecture_id)
    payload = q.payload or {}

    if (payload.get("bank_placed") or {}).get("bank_id"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="이미 은행에 배치된 문항입니다."
        )
    answers = q.answer_indexes or [q.answer_index]
    if len(answers) != 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="다답형 문항은 은행(단일 정답형)으로 보낼 수 없어요. 단일 정답으로 수정 후 시도해 주세요.",
        )
    if payload.get("prompt_image") or payload.get("option_images"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="이미지가 붙은 문항은 은행 형식이 지원하지 않아 보낼 수 없어요.",
        )
    options = payload.get("options") or []
    if not (2 <= len(options) <= 6) or not all(isinstance(o, str) and o.strip() for o in options):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="보기 형식이 올바르지 않습니다.")
    if db.query(Question).count() == 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="문제은행이 아직 DB에 적재되지 않았어요(파일 폴백 상태). 은행 로더로 기존 문항을 먼저 적재해야 배치할 수 있어요.",
        )

    ans_i = int(answers[0])
    slug = f"lec-{lec.id[:8]}-{q.id[:8]}"  # 출처 추적 가능한 유일 슬러그(PK 유니크가 최종 방어)
    max_order = (
        db.query(func.max(Question.order_no)).filter(Question.subject == lec.subject).scalar()
        or 0
    )
    bank_payload = {
        "id": slug,
        "type": "single",
        # topic=강의 제목 — 오답노트·챕터 제목 파생이 topic을 쓴다(출처가 화면에 보인다)
        "topic": lec.title,
        "stage": 1,
        "prompt": payload.get("prompt") or "",
        "hint": "",
        # 위젯은 emoji 없으면 text로 렌더한다(catchap-widget.js: o.emoji || o.text)
        "options": [{"id": f"o{i + 1}", "text": t.strip()} for i, t in enumerate(options)],
        "answer": f"o{ans_i + 1}",
        "explain": payload.get("explain") or "",
        "playable": True,
    }
    db.add(
        Question(
            id=slug, subject=lec.subject, type="single",
            order_no=max_order + 1, playable=True, payload=bank_payload,
        )
    )
    # 원 문항에 배치 이력 표식 — 중복 배치 방지 + 콘솔 '은행 배치됨' 배지의 근거
    q.payload = {**payload, "bank_placed": {"bank_id": slug, "at": datetime.now().isoformat(timespec="seconds")}}
    # ★은행으로 옮긴 문항은 강의 캡차 출제에서 뺀다(active→draft 강등). 은행 배치는
    # 자기검증 verdict='bank'(봇도 상식으로 풀림) 문항의 경로라, 활성 캡차로 남겨두면
    # 봇이 그냥 통과하는 약한 시청 검증이 된다(skeptic 0718). 캡차 출제는 active만
    # 대상이므로(_lecture_challenge) draft로 내리면 캡차 풀에서 빠지되, 문항 목록엔
    # 남아 '은행 배치됨' 배지를 유지한다. 완전 삭제가 아닌 이유: 은행에 payload를
    # 복사했어도 원 문항은 '이 강의에서 이 개념을 냈다'는 이력이라 보존한다.
    demoted = q.status == "active"
    if demoted:
        q.status = "draft"
    db.flush()
    audit(
        db,
        action="lecture.question.to_bank",
        actor_user_id=principal.id,
        target_type="lecture_question",
        target_id=q.id,
        after={"bank_id": slug, "subject": lec.subject, "order_no": max_order + 1,
               "demoted_from_active": demoted},
    )
    # 활성 문항 하나가 줄었으면(강등) 그 시점 예약을 정합 — 마지막 활성 핀을 은행으로
    # 보내면 학생이 게이트에 닿아도 낼 문제가 없어 갇히므로(문항 삭제와 동일 처치).
    if demoted:
        _reconcile_progress(db, lecture_id)
    db.commit()
    # 런타임 은행 갱신 — 재기동 없이 오늘의퀴즈·은행 풀에 즉시 반영(요청 세션 주입)
    runtime_visible = subject_banks.refresh_from_db(db)
    return {"ok": True, "bank_id": slug, "runtime_visible": runtime_visible, "demoted_from_active": demoted}


# ---------------------------------------------------------------- 문항 이미지 첨부
def _get_ops_question(
    db: Session, lecture_id: str, question_id: str, principal: Principal
) -> LectureQuestion:
    _get_ops_lecture(db, lecture_id, principal)
    # FOR UPDATE — 같은 문항의 이미지 첨부/삭제가 동시에 오면 payload(JSON) 재할당이
    # last-write-wins로 먼저 붙인 참조를 덮어 그 파일이 영구 고아가 된다. 행 잠금으로
    # 문항 단위 직렬화(코인 지갑 lost update와 동일 처치). SQLite(테스트)에선 no-op.
    q = db.get(LectureQuestion, question_id, with_for_update=True)
    if q is None or q.lecture_id != lecture_id or q.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="문항을 찾을 수 없습니다.")
    return q


def _resolve_image_slot(
    payload: dict, slot: str, option_index: int | None
) -> tuple[str, str | None]:
    """이미지 슬롯 지정 검증 → (slot, option_images 키). prompt는 키 None.

    업로드 선행(고아 파일 창) 대신 '기존 문항의 슬롯'에 직접 첨부하는 방식이라,
    이미지 파일은 항상 문항 payload의 참조와 같은 트랜잭션에서 태어나고 죽는다."""
    if slot not in ("prompt", "option"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="slot은 prompt|option만 가능합니다.")
    if slot == "prompt":
        return slot, None
    options = payload.get("options", [])
    if option_index is None or not (0 <= option_index < len(options)):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="option_index가 보기 범위를 벗어납니다."
        )
    return slot, str(option_index)


@router.post("/ops/lectures/{lecture_id}/questions/{question_id}/images")
def ops_attach_question_image(
    lecture_id: str,
    question_id: str,
    request: Request,
    slot: str = Form(...),  # prompt|option — 문항의 어느 자리에 붙는 이미지인가
    option_index: int | None = Form(default=None),  # slot=option일 때 보기 인덱스
    file: UploadFile = File(...),
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """문항 이미지 첨부(multipart) — 영상·자료와 동일 패턴: 임시파일 청크 복사(누적 바이트
    재검사) → 원자적 이동 → payload 참조 갱신 → DB commit. 실패 시 파일·참조를 남기지 않는다.

    같은 슬롯에 이미 이미지가 있으면 교체 — 새 파일은 새 id로 저장하고, 이전 파일은
    commit '성공 후' 물리 삭제한다(commit 실패 시 옛 참조·파일 정합 유지)."""
    auth_service.rate_limit(
        db,
        f"lect-qimg-upload:{_client_ip(request)}",
        limit=RATE_QUESTION_IMAGE_UPLOAD_PER_HOUR,
        window_seconds=3600,
    )
    q = _get_ops_question(db, lecture_id, question_id, principal)
    payload = dict(q.payload or {})
    slot, opt_key = _resolve_image_slot(payload, slot, option_index)

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _QUESTION_IMAGE_EXTS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="이미지 파일만 업로드할 수 있습니다(png/jpg/jpeg/gif/webp — svg 금지).",
        )
    if (file.content_type or "").lower() not in _QUESTION_IMAGE_CONTENT_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="이미지 Content-Type(image/png 등)이 아닙니다."
        )

    ref = {"id": new_uuid(), "ext": ext}  # 서버 발급 id — 교체 시에도 항상 새 id(URL 불변성)
    qdir = _question_images_dir()
    qdir.mkdir(parents=True, exist_ok=True)
    tmp_path = qdir / f".upload-{ref['id']}.tmp"
    final_path = _question_image_path(ref)
    try:
        total = _copy_upload_to_tmp(file, tmp_path, get_settings().MAX_QUESTION_IMAGE_BYTES)
        if total == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="빈 파일은 업로드할 수 없습니다.")
        os.replace(tmp_path, final_path)  # 같은 디렉터리 내 원자적 이동
    except BaseException:
        tmp_path.unlink(missing_ok=True)  # replace 실패 포함 — 임시파일을 남기지 않는다(영상·자료와 동일)
        raise

    old_ref = None
    if slot == "prompt":
        old_ref = payload.get("prompt_image")
        payload["prompt_image"] = ref
    else:
        opt_imgs = dict(payload.get("option_images") or {})
        old_ref = opt_imgs.get(opt_key)
        opt_imgs[opt_key] = ref
        payload["option_images"] = opt_imgs
    try:
        q.payload = payload  # JSON 컬럼은 재할당으로만 변경 감지된다(in-place 수정 금지)
        audit(
            db,
            action="lecture.question.image.create",
            actor_user_id=principal.id,
            target_type="lecture_question",
            target_id=q.id,
            after={
                "lecture_id": lecture_id,
                "slot": slot,
                "option_index": option_index if slot == "option" else None,
                "image_id": ref["id"],
                "ext": ext,
                "bytes": total,
                "replaced": bool(old_ref),
            },
        )
        db.commit()
    except BaseException:
        db.rollback()  # 참조도 함께 폐기 — 파일 없는 유령 참조를 만들지 않는다
        final_path.unlink(missing_ok=True)
        raise
    if isinstance(old_ref, dict) and old_ref.get("id"):
        _question_image_path(old_ref).unlink(missing_ok=True)  # 교체된 옛 파일 정리(멱등)
    return _question_row(q)


@router.delete("/ops/lectures/{lecture_id}/questions/{question_id}/images")
def ops_delete_question_image(
    lecture_id: str,
    question_id: str,
    slot: str,
    option_index: int | None = None,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """문항 이미지 제거 — payload 참조 삭제 + commit '성공 후' 파일 물리 삭제.

    텍스트가 빈 보기의 이미지는 지울 수 없다(이미지마저 빼면 내용 없는 보기가 남는다) —
    먼저 보기 텍스트를 채우고 지우게 400으로 안내한다."""
    q = _get_ops_question(db, lecture_id, question_id, principal)
    payload = dict(q.payload or {})
    slot, opt_key = _resolve_image_slot(payload, slot, option_index)

    if slot == "prompt":
        old_ref = payload.pop("prompt_image", None)
    else:
        opt_imgs = dict(payload.get("option_images") or {})
        old_ref = opt_imgs.pop(opt_key, None)
        if old_ref is not None:
            option_text = str(payload.get("options", [])[option_index] or "")
            if not option_text.strip():
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail="텍스트가 빈 보기의 이미지는 삭제할 수 없습니다. 먼저 보기 텍스트를 채워 주세요.",
                )
        if opt_imgs:
            payload["option_images"] = opt_imgs
        else:
            payload.pop("option_images", None)
    if not (isinstance(old_ref, dict) and old_ref.get("id")):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="해당 슬롯에 이미지가 없습니다.")

    q.payload = payload
    audit(
        db,
        action="lecture.question.image.delete",
        actor_user_id=principal.id,
        target_type="lecture_question",
        target_id=q.id,
        after={
            "lecture_id": lecture_id,
            "slot": slot,
            "option_index": option_index if slot == "option" else None,
            "image_id": old_ref["id"],
        },
    )
    db.commit()
    _question_image_path(old_ref).unlink(missing_ok=True)  # commit 성공 후(멱등)
    return _question_row(q)


class _GenerateReq(BaseModel):
    n: int = 5


@router.post("/ops/lectures/{lecture_id}/questions/generate")
def ops_generate_questions(
    lecture_id: str,
    req: _GenerateReq,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """AI 문항 자동 생성 — STT 전사(키 설정 시) → LLM 출제, source=llm·status=draft 저장.

    키는 요청 시점마다 해석한다(운영 콘솔 입력(DB) → .env 폴백) — 콘솔에서 키를 넣으면
    재기동 없이 바로 켜진다. 정직성 규약:
    - LLM 키 없음 → 503(설정 페이지 안내). stub 문항 생성 금지.
    - STT 키가 '설정돼 있는데' 전사 실패 → 502로 원인 노출(메타데이터 폴백으로 조용히
      강등하지 않는다 — 키 오류·용량 초과를 운영자가 알아야 고친다).
    - STT 키 미설정 → 메타(제목·설명) 기반 생성 + 응답에 transcript_used=false 명시.
    전사가 있으면 LLM이 출제 시점(position)과 되감기 지점(content_start)까지 제안하고,
    영상 범위를 벗어나는 제안은 버려 '시점 미배치' draft로 남긴다(운영자 검수)."""
    from app.clients.ai_client import (
        AiGenerationError,
        AiNotConfiguredError,
        generate_lecture_questions,
        verify_questions,
    )
    from app.clients.stt_client import SttError, transcribe_video
    from app.services import settings_service

    lec = _get_ops_lecture(db, lecture_id, principal)
    llm_key = settings_service.resolve_anthropic_key(db)
    if not llm_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM API 키가 설정되지 않아 문제 자동 생성을 사용할 수 없습니다. 운영 콘솔 '설정'에서 키를 입력해 주세요.",
        )

    transcript: list[dict] | None = None
    stt_key = settings_service.resolve_openai_key(db)
    if stt_key:
        try:
            transcript = transcribe_video(_video_path(lec), api_key=stt_key)
        except SttError as e:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, detail=f"강의 음성 전사(STT)에 실패했습니다: {e}"
            )

    try:
        items = generate_lecture_questions(
            lecture_title=lec.title,
            description=lec.description,
            subject=lec.subject,
            n=req.n,
            api_key=llm_key,
            transcript=transcript,
        )
    except AiNotConfiguredError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM API 키가 설정되지 않아 문제 자동 생성을 사용할 수 없습니다. 운영 콘솔 '설정'에서 키를 입력해 주세요.",
        )
    except AiGenerationError as e:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"문항 자동 생성에 실패했습니다: {e}"
        )

    # 자기검증(2번째 LLM) — 공개 맥락(공격자가 보는 제목·과목·설명)을 준 블라인드 풀이를
    # '보기 셔플 3회 다수결'로(우연 정답·위치 편향 완화), 자막이 있으면 자막-포함 풀이도
    # 1회 돌려 3분류한다: captcha(강의 의존·정상)/bank(상식 — 캡차 부적합)/discard(자막을
    # 줘도 못 풂 = 불량 의심). '참고 신호'라 실패해도 생성은 살린다(verify_error로 정직 노출).
    verdicts: list[dict] | None = None
    verify_error: str | None = None
    try:
        verdicts = verify_questions(
            items,
            api_key=llm_key,
            context={"title": lec.title, "subject": lec.subject, "description": lec.description},
            transcript=transcript,
        )
    except (AiNotConfiguredError, AiGenerationError) as e:
        verify_error = str(e)

    verified_at = datetime.now().isoformat(timespec="seconds")
    duration = int(lec.duration_sec or 0)
    created: list[LectureQuestion] = []
    for idx, item in enumerate(items):
        # LLM 시점 제안 검증 — 영상 안(1 <= pos < duration)일 때만 채택, 아니면 미배치(0).
        # content_start는 pos보다 앞일 때만(생성 검증과 동일 규칙 — cp 이상 되감기 금지).
        pos = int(item.get("position_sec") or 0)
        if not (1 <= pos < duration):
            pos = 0
        cs = item.get("content_start_sec")
        cs = int(cs) if isinstance(cs, int) and pos >= 1 and 0 <= cs < pos else None
        # 자기검증 결과 → 배치 제안(강사가 최종 결정). None=미판정.
        v = verdicts[idx] if verdicts is not None and idx < len(verdicts) else None
        q = LectureQuestion(
            lecture_id=lec.id,
            position_sec=pos,  # 전사 기반 제안(검수 대상) 또는 0=미배치
            content_start_sec=cs,
            payload={
                "prompt": item["prompt"],
                "options": item["options"],
                "explain": item.get("explain", ""),
                # 자기검증(봇 저항) 판정 — 강사 검수 화면이 읽어 배치를 돕는다.
                # solver_passed = 블라인드로 풀렸는지(하위호환 명칭 유지).
                "solver_passed": None if v is None else v["blind_passed"],
                "transcript_solver_passed": None if v is None else v["transcript_passed"],
                "suggested_placement": None if v is None else v["verdict"],
                # 판정 감사용 메타 — 어느 모델이 언제 몇 회 다수결로 판정했나
                "solver_meta": (
                    {"model": get_settings().LLM_MODEL, "verified_at": verified_at, "trials": 3}
                    if v is not None
                    else None
                ),
            },
            answer_index=item["answer_index"],
            source="llm",
            status="draft",
        )
        db.add(q)
        created.append(q)
    db.flush()
    audit(
        db,
        action="lecture.question.generate",
        actor_user_id=principal.id,
        target_type="lecture",
        target_id=lec.id,
        after={
            "count": len(created),
            "model": get_settings().LLM_MODEL,
            "transcript_used": transcript is not None,
            "self_verified": verdicts is not None,
        },
    )
    db.commit()
    counts = {"bank": 0, "captcha": 0, "discard": 0}
    for v in verdicts or []:
        counts[v["verdict"]] += 1
    return {
        "created": len(created),
        # 전사 사용 여부를 정직하게 노출 — STT 미설정이면 콘솔이 '메타 기반 생성'임을 안내
        "transcript_used": transcript is not None,
        # 자기검증 요약 — captcha(강의 의존)/bank(상식)/discard(불량 의심). 미판정이면 verify_error.
        "self_verified": verdicts is not None,
        "bank_candidates": counts["bank"] if verdicts is not None else None,
        "captcha_candidates": counts["captcha"] if verdicts is not None else None,
        "discard_candidates": counts["discard"] if verdicts is not None else None,
        "verify_error": verify_error,
        "questions": [_question_row(q) for q in created],
    }


# ---------------------------------------------------------------- 자료실(강의 자료) CRUD
def _material_row(m: LectureMaterial) -> dict:
    # 운영자 편집 화면용 — url(외부 링크 원문/다운로드 경로 키) 포함. 파일시스템 경로는
    # DB에도 응답에도 존재하지 않는다({id}{ext} 유도 원칙).
    return {
        "id": m.id,
        "lecture_id": m.lecture_id,
        "title": m.title,
        "kind": m.kind,
        "url": m.url,
        "file_ext": m.file_ext,
        "file_bytes": int(m.file_bytes or 0),
        "order_no": int(m.order_no or 0),
        "status": m.status,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _next_material_order(db: Session, lecture_id: str) -> int:
    max_no = (
        db.query(func.max(LectureMaterial.order_no))
        .filter(
            LectureMaterial.lecture_id == lecture_id,
            LectureMaterial.status != "deleted",
        )
        .scalar()
        or 0
    )
    return int(max_no) + 1


@router.get("/ops/lectures/{lecture_id}/materials")
def ops_list_materials(
    lecture_id: str,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    _get_ops_lecture(db, lecture_id, principal)
    rows = (
        db.query(LectureMaterial)
        .filter(
            LectureMaterial.lecture_id == lecture_id,
            LectureMaterial.status != "deleted",
        )
        .order_by(LectureMaterial.order_no, LectureMaterial.created_at)
        .all()
    )
    return [_material_row(m) for m in rows]


def _parse_order_no(raw, db: Session, lecture_id: str) -> int:
    """order_no 입력 정규화 — 미지정이면 맨 뒤(max+1), 지정 시 0 이상 정수만."""
    if raw is None or raw == "":
        return _next_material_order(db, lecture_id)
    try:
        order_no = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="order_no는 정수여야 합니다.")
    if order_no < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="order_no는 0 이상이어야 합니다.")
    return order_no


async def _create_link_material(
    request: Request, lec: Lecture, principal: Principal, db: Session
) -> dict:
    """kind=link — JSON(title+url). 외부 URL 원문을 그대로 저장한다(http/https만)."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="link 자료는 JSON 본문(title·url)이 필요합니다."
        )
    if not isinstance(body, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="JSON 객체 본문이 필요합니다.")
    title = str(body.get("title") or "").strip()
    url = str(body.get("url") or "").strip()
    if not title:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="제목(title)이 필요합니다.")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="url은 http(s):// 로 시작하는 외부 링크여야 합니다."
        )
    if len(url) > 500:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="url이 너무 깁니다(500자 이하).")

    mat = LectureMaterial(
        lecture_id=lec.id,
        title=title[:200],
        kind="link",
        url=url,
        file_ext=None,
        file_bytes=0,
        order_no=_parse_order_no(body.get("order_no"), db, lec.id),
        status="active",
    )
    db.add(mat)
    db.flush()
    audit(
        db,
        action="lecture.material.create",
        actor_user_id=principal.id,
        target_type="lecture_material",
        target_id=mat.id,
        after={"lecture_id": lec.id, "title": mat.title, "kind": "link", "url": url},
    )
    db.commit()
    return _material_row(mat)


async def _create_file_material(
    request: Request, lec: Lecture, principal: Principal, db: Session
) -> dict:
    """kind=file — multipart(title+file). 영상 업로드와 동일 패턴: 임시파일 청크 복사(누적
    바이트 재검사) → 원자적 이동 → DB commit. 실패 시 파일·행을 남기지 않는다."""
    auth_service.rate_limit(
        db,
        f"lect-mat-upload:{_client_ip(request)}",
        limit=RATE_MATERIAL_UPLOAD_PER_HOUR,
        window_seconds=3600,
    )
    try:
        form = await request.form()
    except Exception:
        # 깨진/잘린 multipart 본문(프록시가 헤더를 건드렸거나 업로드가 중단된 경우) —
        # 파서 예외를 500이 아니라 400으로 정직하게 돌려준다. 이 try는 form() 한 줄만
        # 감싸므로 아래 로직의 HTTPException(400)들을 삼키지 않는다.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="multipart 본문을 해석할 수 없습니다."
        )
    title = str(form.get("title") or "").strip()
    upload = form.get("file")
    if not title:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="제목(title)이 필요합니다.")
    if upload is None or isinstance(upload, str):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="file 필드(업로드 파일)가 필요합니다.")
    ext = os.path.splitext(upload.filename or "")[1].lower()
    if ext not in _MATERIAL_EXTS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="문서·이미지·압축 파일만 업로드할 수 있습니다(pdf/zip/png/jpg/hwp/docx/pptx 등).",
        )
    order_no = _parse_order_no(form.get("order_no"), db, lec.id)

    mat = LectureMaterial(
        lecture_id=lec.id,
        title=title[:200],
        kind="file",
        url="",  # id 확정(flush) 후 다운로드 경로 키를 채운다
        file_ext=ext,
        file_bytes=0,
        order_no=order_no,
        status="active",
    )
    db.add(mat)
    db.flush()  # id 확정 — 파일명은 materials/{id}{ext}
    mat.url = f"/api/v1/lectures/{lec.id}/materials/{mat.id}/download"

    mdir = _materials_dir()
    mdir.mkdir(parents=True, exist_ok=True)
    tmp_path = mdir / f".upload-{mat.id}.tmp"
    final_path = mdir / f"{mat.id}{ext}"
    try:
        total = _copy_upload_to_tmp(upload, tmp_path, get_settings().MAX_MATERIAL_UPLOAD_BYTES)
        if total == 0:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="빈 파일은 업로드할 수 없습니다.")
        os.replace(tmp_path, final_path)  # 같은 디렉터리 내 원자적 이동
    except BaseException:
        db.rollback()  # 자료 행도 함께 폐기 — 파일 없는 유령 자료를 만들지 않는다
        tmp_path.unlink(missing_ok=True)
        raise

    try:
        mat.file_bytes = total
        audit(
            db,
            action="lecture.material.create",
            actor_user_id=principal.id,
            target_type="lecture_material",
            target_id=mat.id,
            after={
                "lecture_id": lec.id,
                "title": mat.title,
                "kind": "file",
                "file_ext": ext,
                "file_bytes": total,
            },
        )
        db.commit()
    except BaseException:
        final_path.unlink(missing_ok=True)  # DB 확정 실패 — 고아 파일 제거
        raise
    return _material_row(mat)


@router.post("/ops/lectures/{lecture_id}/materials")
async def ops_create_material(
    lecture_id: str,
    request: Request,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """자료 생성 — kind=file은 multipart(title+file), kind=link는 JSON(title+url).

    FastAPI 시그니처는 본문 형식을 하나로 고정하므로(폼·JSON 동시 선언 불가) Content-Type으로
    직접 분기한다. 전역 본문 상한 예외(main.py)도 같은 기준(multipart일 때만 50MB)이다."""
    lec = _get_ops_lecture(db, lecture_id, principal)
    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith("multipart/form-data"):
        return await _create_file_material(request, lec, principal, db)
    return await _create_link_material(request, lec, principal, db)


class _MaterialUpdate(BaseModel):
    title: str | None = None
    order_no: int | None = None


@router.put("/ops/lectures/{lecture_id}/materials/{material_id}")
def ops_update_material(
    lecture_id: str,
    material_id: str,
    req: _MaterialUpdate,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """메타만 수정(title·order_no) — 파일 교체·URL 변경은 삭제 후 재등록으로 처리한다."""
    _get_ops_lecture(db, lecture_id, principal)
    mat = db.get(LectureMaterial, material_id)
    if mat is None or mat.lecture_id != lecture_id or mat.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="자료를 찾을 수 없습니다.")

    before = {"title": mat.title, "order_no": mat.order_no}
    if req.title is not None:
        if not req.title.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="제목(title)이 비어 있습니다.")
        mat.title = req.title.strip()[:200]
    if req.order_no is not None:
        if req.order_no < 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="order_no는 0 이상이어야 합니다.")
        mat.order_no = req.order_no

    audit(
        db,
        action="lecture.material.update",
        actor_user_id=principal.id,
        target_type="lecture_material",
        target_id=mat.id,
        before=before,
        after={"title": mat.title, "order_no": mat.order_no},
    )
    db.commit()
    return _material_row(mat)


@router.delete("/ops/lectures/{lecture_id}/materials/{material_id}")
def ops_delete_material(
    lecture_id: str,
    material_id: str,
    principal: Principal = Depends(require_lecture_manager),
    db: Session = Depends(get_db),
):
    """소프트 삭제 + file 종류는 파일 물리 삭제 — 레코드·이력은 보존(status=deleted로
    노출만 차단). link 종류는 지울 파일이 없다.

    파일 삭제는 commit '성공 후'(강의 삭제와 동일 원칙 — commit 실패 시 파일·레코드 정합 유지).
    파일 부재는 status=deleted + file_bytes=0으로 나타내고 원래 크기는 감사 before에 남긴다."""
    _get_ops_lecture(db, lecture_id, principal)
    mat = db.get(LectureMaterial, material_id)
    if mat is None or mat.lecture_id != lecture_id or mat.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="자료를 찾을 수 없습니다.")
    file_path = _material_path(mat) if (mat.kind == "file" and mat.file_ext) else None
    file_existed = file_path.is_file() if file_path is not None else False
    before = {"status": mat.status, "file_bytes": int(mat.file_bytes or 0)}
    mat.status = "deleted"
    if mat.kind == "file":
        mat.file_bytes = 0
    audit(
        db,
        action="lecture.material.delete",
        actor_user_id=principal.id,
        target_type="lecture_material",
        target_id=mat.id,
        before=before,
        after={"status": "deleted", "file_removed": file_existed},
    )
    db.commit()
    if file_path is not None:
        file_path.unlink(missing_ok=True)  # 이미 없어도 무해(멱등)
    return {"ok": True}
