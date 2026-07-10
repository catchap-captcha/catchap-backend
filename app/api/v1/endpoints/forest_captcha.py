"""메인 캡차(숲속 마을 동물 방향) API — 로그인 게이트 등에서 쓰는 1st-party 캡차.

  POST /captcha/forest/challenge     새 문제(정답 미노출)
  GET  /captcha/forest/{cid}/target  목표 포즈 이미지(불투명 — 방향 index 미노출)
  POST /captcha/forest/verify        검증, 성공 시 단일사용 captcha_token

정답(target_object/target_direction)은 서버에서 생성해 서버에만 저장하고 응답에 넣지 않는다
(services/forest_captcha.py). 자사 로그인 페이지(동일 출처)용이라 공개 CORS(/captcha/v1)와 분리한다.
"""

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db.session import get_db
from app.services import forest_captcha as fc

router = APIRouter(prefix="/captcha/forest", tags=["captcha-forest"])

# 동물 프레임 위치(서버측 — 정답 방향 index를 노출하지 않고 픽셀만 서빙)
_ANIMALS_DIR = Path(__file__).resolve().parents[3] / "static" / "forest" / "animals"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------- 스키마 (정답 필드 없음)
class ChallengeResponse(BaseModel):
    challenge_id: str
    animal: str
    objects: list[str]
    start_direction: int
    expires_in: int


class VerifyRequest(BaseModel):
    challenge_id: str
    selected_object: str
    selected_direction: int = Field(ge=0, le=7)
    # 선택: 풀이 중 포인터 궤적 — 인앱(학생 토큰) 사용 시에만 봇탐지 학습셋에 적재
    behavior: dict | None = None


class VerifyResponse(BaseModel):
    success: bool
    captcha_token: str | None = None
    message: str | None = None


# ---------------------------------------------------------------- 1) 챌린지 생성
@router.post("/challenge", response_model=ChallengeResponse)
def create_challenge(request: Request):
    # 레이트리밋: 봇의 대량 챌린지 생성·정답 브루트포스 완화(IP 슬라이딩 윈도우)
    if fc.service.rate_limited(_client_ip(request)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, detail="요청이 너무 많습니다. 잠시 후 다시 시도하세요."
        )
    rec = fc.service.create_challenge()
    # 표시용 필드만 반환 — target_object/target_direction은 서버에 남는다.
    return ChallengeResponse(
        challenge_id=rec.challenge_id,
        animal=rec.animal,
        objects=fc.OBJECTS,
        start_direction=rec.start_direction,
        expires_in=rec.seconds_left(),
    )


# ---------------------------------------------------------------- 2) 목표 포즈 이미지(불투명)
@router.get("/{challenge_id}/target")
def target_image(challenge_id: str):
    """목표 포즈 이미지를 불투명 리소스로 서빙 — URL·페이로드에 방향 index가 없다.

    animal/direction은 서버 생성값(사용자 입력 아님)이라 경로조작 불가지만, 화이트리스트·
    경로 이탈 가드를 이중으로 둔다. no-store로 중간 캐시가 정답 이미지를 보관하지 못하게 한다.
    """
    rec = fc.service.get_active_challenge(challenge_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="challenge_not_found_or_expired")
    if rec.animal not in fc.ANIMALS or not (0 <= rec.target_direction < fc.DIRECTIONS):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="asset_not_found")
    content = _animal_frame(rec.animal, rec.target_direction)
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="asset_not_found")
    return Response(
        content=content,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@lru_cache(maxsize=None)
def _animal_frame(animal: str, direction: int) -> bytes | None:
    """포즈 PNG 프로세스당 1회 읽기 — 키는 화이트리스트(ANIMALS×8방향)라 유한.

    no-store는 클라이언트/중간 캐시용 지시고, 서버 인메모리 보관은 정답 노출과 무관하다.
    """
    path = (_ANIMALS_DIR / animal / f"dir{direction}.png").resolve()
    base = _ANIMALS_DIR.resolve()
    if base not in path.parents or not path.exists():
        return None
    return path.read_bytes()


# ---------------------------------------------------------------- 3) 검증
@router.post("/verify", response_model=VerifyResponse)
def verify(req: VerifyRequest, request: Request, db: Session = Depends(get_db)):
    ok = fc.service.verify(req.challenge_id, req.selected_object, req.selected_direction)

    # 선택적 행동데이터 적재 — 유효한 학생 토큰이 있을 때만(인앱 사용) 봇탐지 학습셋에 기록.
    # 로그인 게이트(비로그인)에선 귀속할 org/student가 없어 건너뛴다.
    if req.behavior:
        _maybe_record_behavior(db, request, req.behavior, correct=ok)

    if ok:
        return VerifyResponse(success=True, captcha_token=fc.service.issue_token())
    return VerifyResponse(success=False, message="new_challenge_required")


def _maybe_record_behavior(db: Session, request: Request, behavior: dict, correct: bool) -> None:
    """Authorization이 유효한 학생 토큰이면 그 학생/기관에 궤적을 귀속해 기록(source_type=forest)."""
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return
    try:
        payload = decode_token(auth[7:])
    except Exception:
        return
    from app.models import StudentProfile
    from app.services.captcha_service import record_behavior_event

    sid = payload.get("sub")
    st = db.query(StudentProfile).filter(StudentProfile.id == sid).first() if sid else None
    if st is None:
        return
    record_behavior_event(
        db,
        organization_id=st.organization_id,
        student_id=st.id,
        source_type="forest",
        behavior=behavior,
        correct=correct,
    )
    db.commit()
