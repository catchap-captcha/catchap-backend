from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    captcha_api,
    forest_captcha,
    health,
    institutions,
    misc,
    notifications,
    ops,
    orgs,
    parents,
    settings as settings_ep,
    students,
    teacher,
    widget,
)

# 전 모듈 직접 등록 — import 오류 시 서버가 조용히 404 뜨는 대신 크게 실패해야 한다
# (구 import 가드는 장애를 숨겨 제거함)
api_router = APIRouter()
for _mod in (
    health,
    auth,
    institutions,
    students,
    parents,
    teacher,
    orgs,
    notifications,
    settings_ep,
    misc,
    ops,
    captcha_api,
    forest_captcha,
    widget,
):
    api_router.include_router(_mod.router)
