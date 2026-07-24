"""메인 캡차(드래그) 공개 엔드포인트 — ms 캡차 런타임 자체 이식(app.services.drag_captcha_service).

프론트 위젯(ForestCaptcha)이 호출한다: 챌린지 발급 → 에셋(이미지·조각) 서빙 → 검증.
검증 성공 시 1회용 captcha_token 발급 → 로그인/회원가입 스텝업에서 소비(auth_service).
1st-party 전용이라 site_key 헤더는 요구하지 않는다(외부 판매 캡차는 captcha_api.py로 별개).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.services import drag_captcha_service as svc

router = APIRouter(prefix="/captcha/drag", tags=["captcha-drag"])


def _require_enabled() -> None:
    # 플래그 OFF면 엔드포인트를 비활성(404)으로 둔다 — 활성화 전(테이블·데이터 미배치)에는
    # 존재하지 않는 것처럼 동작해 500·불필요 노출을 막는다. 활성화는 플래그 ON 한 번으로.
    if not get_settings().DRAG_CAPTCHA_ENABLED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


class ChallengeCreate(BaseModel):
    purpose: Literal["signup", "login", "recovery"] = "login"
    session_id: str = Field(min_length=8, max_length=128)


class BehaviorEvent(BaseModel):
    type: str = Field(max_length=32)
    object_id: str | None = Field(default=None, max_length=64)
    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    timestamp_ms: int = Field(ge=0)


class VerifyRequest(BaseModel):
    selected_object_ids: list[str] = Field(max_length=12)
    session_id: str = Field(min_length=8, max_length=128)
    duration_ms: int = Field(ge=100, le=180000)
    events: list[BehaviorEvent] = Field(default_factory=list, max_length=600)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/challenges", status_code=status.HTTP_201_CREATED)
def create_challenge(payload: ChallengeCreate, request: Request):
    _require_enabled()
    try:
        return svc.create_challenge(payload.purpose, payload.session_id, _client_ip(request))
    except svc.CaptchaError as e:
        raise HTTPException(e.status_code, e.detail)


@router.get("/assets/{challenge_id}/{asset_id}")
def challenge_asset(challenge_id: str, asset_id: str):
    _require_enabled()
    try:
        return FileResponse(svc.asset_path(challenge_id, asset_id))
    except FileNotFoundError:
        raise HTTPException(404, "Asset not found")


@router.post("/challenges/{challenge_id}/verify")
def verify(challenge_id: str, payload: VerifyRequest, request: Request):
    _require_enabled()
    try:
        return svc.verify(challenge_id, payload.selected_object_ids, payload.session_id,
                          payload.duration_ms, payload.events, _client_ip(request))
    except svc.CaptchaError as e:
        raise HTTPException(e.status_code, e.detail)
