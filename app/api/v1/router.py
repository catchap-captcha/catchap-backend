from fastapi import APIRouter

# 은퇴 라우터(제품 전환): parents·teacher·orgs — 학교/학부모 콘솔 은퇴(0717~18)로
# 프론트 소비자가 0이 되어 제거. 종전 코드는 git 이력 참고.
from app.api.v1.endpoints import (
    auth,
    captcha_api,
    course_exam,
    forest_captcha,
    health,
    institutions,
    lectures,
    misc,
    notifications,
    ops,
    settings as settings_ep,
    students,
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
    notifications,
    settings_ep,
    misc,
    ops,
    captcha_api,
    forest_captcha,
    widget,
    lectures,
    course_exam,
):
    api_router.include_router(_mod.router)
