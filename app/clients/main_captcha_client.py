"""메인 캡차(봇 판별) 토큰 서버검증 클라이언트.

브라우저가 "캡차 통과했다"고 말하는 것은 믿지 않는다. 프런트가 넘긴 토큰을 이 모듈이
캡차 서버에 직접 물어보고, 캡차가 확인해준 것만 통과로 인정한다. 사이트 시크릿은
서버에만 있으므로 클라이언트가 이 응답을 위조할 수 없다.

캡차 쪽 계약(김민서): POST /api/verify-token
  헤더 X-Captcha-Site-Secret, 본문 {token, purpose, session_id, lecture_id}
  → {success: bool, lecture_id, challenge_id}
토큰은 1회용이며(consumed_at + FOR UPDATE) 발급 시 강의ID에 바인딩된다 — 다른 강의에서
받은 토큰을 이 강의에 쓸 수 없다.

가짜 성공 금지: 설정이 비었거나 호출이 실패하면 False 를 돌려준다. 승급 게이트는
fail-closed 다 — 통과를 확인하지 못했으면 통과가 아니다. (캡차 안에서 AI 를 부르는 쪽은
fail-open 이지만, 그건 '점수를 못 얻었다'와 '사람임을 확인했다'가 다른 문제이기 때문이다.)
"""

import logging

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

_TIMEOUT_SEC = 3.0


class MainCaptchaNotConfiguredError(RuntimeError):
    """캡차 호스트/시크릿 미설정. 승급을 켜두고 설정을 빼먹은 상태를 조용히 넘기지 않는다."""


def verify_token(
    *,
    token: str,
    session_id: str,
    lecture_id: str | None = None,
    purpose: str = "lecture",
) -> bool:
    """캡차 서버에 토큰을 검증한다. 통과 확인이면 True, 그 외 전부 False.

    `purpose` 는 발급 때 값과 **같아야** 한다 — 캡차가 대조하고 다르면 유효한 토큰도
    거부한다. 강의 승급은 'lecture', 로그인 스텝업은 'login' 이다.

    `lecture_id` 는 줄 거면 발급 때도 줬어야 한다(캡차가 일치를 확인한다). 로그인에는
    강의가 없으므로 None 이고, 그 경우 아예 보내지 않는다 — None 을 실어 보내면
    발급 때 없던 값과 대조하다 불일치로 떨어진다.
    """
    settings = get_settings()
    base = (getattr(settings, "MAIN_CAPTCHA_URL", "") or "").strip().rstrip("/")
    secret = (getattr(settings, "MAIN_CAPTCHA_SITE_SECRET", "") or "").strip()
    if not base or not secret:
        raise MainCaptchaNotConfiguredError(
            "MAIN_CAPTCHA_URL / MAIN_CAPTCHA_SITE_SECRET 가 설정되지 않았습니다."
        )
    if not token:
        return False

    try:
        response = httpx.post(
            f"{base}/api/verify-token",
            headers={"X-Captcha-Site-Secret": secret},
            json={
                "token": token,
                # 발급 때 값과 일치해야 한다(캡차가 대조한다).
                "purpose": purpose,
                "session_id": session_id,
                # 로그인처럼 강의가 없으면 키 자체를 빼야 한다(위 docstring 참조).
                **({"lecture_id": lecture_id} if lecture_id else {}),
            },
            timeout=_TIMEOUT_SEC,
        )
    except httpx.HTTPError as error:
        # 네트워크 실패는 통과가 아니다. 다만 조용히 넘기면 안 되므로 남긴다 —
        # 캡차 서버가 죽은 채로 승급이 계속 실패하면 학생이 진도를 못 나간다.
        log.warning("main captcha verify-token 호출 실패: %s", error)
        return False

    if response.status_code != 200:
        log.warning("main captcha verify-token HTTP %s", response.status_code)
        return False

    try:
        body = response.json()
    except ValueError:
        log.warning("main captcha verify-token 응답이 JSON 이 아님")
        return False

    if not body.get("success"):
        # 만료·이미 소비됨·강의ID 불일치가 여기로 온다(캡차가 error 로 알려준다).
        log.info("main captcha verify-token 거부: %s", body.get("error"))
        return False

    # 캡차가 돌려준 강의ID가 우리가 물어본 것과 같아야 한다. 캡차도 검증하지만
    # 한 겹 더 확인한다 — 승급 리셋은 되돌릴 수 없는 부작용이다.
    returned = body.get("lecture_id")
    if returned and returned != lecture_id:
        log.warning("verify-token 강의ID 불일치: 요청 %s / 응답 %s", lecture_id, returned)
        return False
    return True
