"""소셜 로그인(카카오·네이버·구글) OAuth 2.0 HTTP 어댑터.

이 모듈은 **provider별 HTTP 규격만** 담당한다 — 계정 연결·신규 가입 판정·연령 게이트는
services/social_login_service.py가 한다. 결제에서 payment_gateways(HTTP 규격)와
endpoints/payments(주문 상태)를 나눈 것과 같은 규약이다.

보안 규약:
- provider access token은 프로필을 한 번 읽는 데만 쓰고 **저장하지 않는다**. 우리는 소셜
  계정을 '로그인 수단'으로만 쓰지 그 계정의 API를 대신 호출하지 않기 때문이다. 저장하지
  않으면 유출 표면도 갱신(refresh) 부담도 없다.
- client_secret은 서버에서만 쓴다. 프론트에는 authorize URL만 내려간다.
- 이메일 검증 여부(email_verified)를 그대로 올려 보낸다 — 기존 계정 자동 연결을 허용할지
  판단하는 근거라 어댑터가 임의로 True로 만들지 않는다.

가짜 성공 금지: 키가 없으면 build_provider가 호출 전에 SocialAuthError를 던진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlencode

import httpx

# 지원 provider 키 — 라우터 경로(/auth/social/{provider})와 DB 값이 모두 이 문자열이다.
PROVIDERS = ("kakao", "naver", "google")

PROVIDER_LABELS = {"kakao": "카카오", "naver": "네이버", "google": "구글"}


class SocialAuthError(RuntimeError):
    """사용자에게 노출해도 되는 소셜 로그인 실패(비밀정보 미포함)."""

    def __init__(self, message: str, *, provider_code: str | None = None):
        super().__init__(message)
        self.message = message
        self.provider_code = provider_code


@dataclass(frozen=True)
class SocialProfile:
    """provider가 알려 준 사용자 정보의 정규화 결과.

    provider_user_id는 provider 안에서만 유일하므로 우리 쪽 유일키는 항상
    (provider, provider_user_id) 쌍이다. email은 없을 수 있다 — 카카오는 이메일 제공
    동의가 선택이고, 미동의 사용자도 로그인 자체는 성립한다.
    """

    provider: str
    provider_user_id: str
    email: str | None
    email_verified: bool
    nickname: str | None
    birth_date: date | None


def _parse_birth(year: Any, mmdd: Any) -> date | None:
    """provider가 주는 (생년, MMDD|MM-DD)를 date로. 하나라도 없거나 이상하면 None.

    생년월일은 만 14세 게이트의 판정 기준이라 애매하면 채우지 않는다 —
    없으면 가입 단계에서 사용자에게 직접 받는다(추측해서 게이트를 통과시키지 않는다).
    """
    y = str(year or "").strip()
    md = str(mmdd or "").strip().replace("-", "")
    if len(y) != 4 or len(md) != 4 or not y.isdigit() or not md.isdigit():
        return None
    try:
        return date(int(y), int(md[:2]), int(md[2:]))
    except ValueError:
        return None


class SocialProvider:
    """OAuth 2.0 인가 코드 그랜트 공통 골격 — provider별로 URL과 파싱만 다르다."""

    key = ""
    label = ""
    authorize_endpoint = ""
    token_endpoint = ""
    profile_endpoint = ""
    scope = ""
    # ★'다른 계정으로 로그인' — provider 세션이 살아 있으면 동의 화면을 건너뛰고 즉시
    # 돌아온다(OAuth 표준 동작). 우리가 로그아웃해도 provider 세션은 우리 것이 아니라
    # 그대로 남기 때문이다. 계정을 바꾸려면 provider 에게 '다시 물어보라'고 해야 하는데,
    # 그 파라미터 이름이 provider 마다 다르다. 여기에 선언하고 authorize_url 이 붙인다.
    # ⚠️평소 로그인에는 붙이지 않는다 — 매번 다시 로그인하게 되어 간편 로그인이 아니게 된다.
    reauth_params: dict[str, str] = {}
    # provider 고유 고정 파라미터(항상 붙는다). authorize_url 을 오버라이드하면 상위 시그니처와
    # 갈라져 새 인자가 늘 때 조용히 깨진다 — 실제로 reauth 를 넣을 때 구글에서 그랬다.
    extra_authorize_params: dict[str, str] = {}
    # 이 provider의 이메일을 '소유가 검증된 것'으로 볼 수 있는가.
    # 응답에 검증 필드가 있는 provider는 그 값을 그대로 쓰고, 없는 provider만 이 값을 쓴다.
    email_verified_default = False

    def __init__(
        self,
        client_id: str,
        client_secret: str = "",
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
        scope: str | None = None,
    ):
        if not client_id.strip():
            raise SocialAuthError(f"{self.label} 로그인이 아직 설정되지 않았어요.")
        # 콘솔에서 실제로 열어 둔 동의항목만 요청해야 한다 — 없는 항목을 요청하면
        # provider가 로그인을 거절한다(카카오 KOE205). 설정으로 덮어쓸 수 있게 둔다.
        if scope is not None:
            self.scope = scope.strip()
        self._client_id = client_id.strip()
        self._client_secret = (client_secret or "").strip()
        self._client = client
        self._timeout = timeout

    # ---------------------------------------------------------------- 요청 공통
    def _request(self, method: str, url: str, **kwargs) -> dict[str, Any]:
        try:
            if self._client is not None:
                response = self._client.request(method, url, **kwargs)
            else:
                response = httpx.request(method, url, timeout=self._timeout, **kwargs)
        except httpx.HTTPError as exc:
            raise SocialAuthError(
                f"{self.label} 서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요."
            ) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise SocialAuthError(f"{self.label} 응답을 확인할 수 없어요.") from exc
        if not isinstance(body, dict):
            raise SocialAuthError(f"{self.label} 응답 형식이 올바르지 않아요.")
        if response.status_code < 200 or response.status_code >= 300:
            code = body.get("error") or body.get("error_code") or body.get("resultcode")
            raise SocialAuthError(
                f"{self.label} 로그인이 거절됐어요.",
                provider_code=str(code)[:80] if code is not None else None,
            )
        return body

    # ---------------------------------------------------------------- 단계별
    def authorize_url(self, redirect_uri: str, state: str, *, reauth: bool = False) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
        if self.scope:
            params["scope"] = self.scope
        params.update(self.extra_authorize_params)
        if reauth:
            params.update(self.reauth_params)
        return f"{self.authorize_endpoint}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str, state: str) -> str:
        """인가 코드 → access token. 실패는 SocialAuthError."""
        data = {
            "grant_type": "authorization_code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "code": code,
        }
        if self._client_secret:
            data["client_secret"] = self._client_secret
        body = self._request(
            "POST",
            self.token_endpoint,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        )
        token = str(body.get("access_token") or "")
        if not token:
            raise SocialAuthError(f"{self.label} 인증 토큰을 받지 못했어요.")
        return token

    def fetch_profile(self, access_token: str) -> SocialProfile:
        raise NotImplementedError

    def login(self, code: str, redirect_uri: str, state: str) -> SocialProfile:
        return self.fetch_profile(self.exchange_code(code, redirect_uri, state))

    # ---------------------------------------------------------------- 헬퍼
    def _profile_body(self, access_token: str) -> dict[str, Any]:
        return self._request(
            "GET",
            self.profile_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    @staticmethod
    def _clean_email(value: Any) -> str | None:
        email = str(value or "").strip().lower()
        return email or None


class KakaoProvider(SocialProvider):
    key = "kakao"
    label = "카카오"
    authorize_endpoint = "https://kauth.kakao.com/oauth/authorize"
    token_endpoint = "https://kauth.kakao.com/oauth/token"
    profile_endpoint = "https://kapi.kakao.com/v2/user/me"
    reauth_params = {"prompt": "login"}  # 카카오 — 로그인 화면을 다시 띄운다
    # ★기본값은 닉네임만이다. 카카오는 이메일(account_email) 동의항목을 **비즈 앱**에만
    # 열어 주고, 권한 없는 항목을 요청하면 로그인 자체가 거절된다(KOE205). 비즈 앱 전환 후
    # settings.KAKAO_SCOPES 에 "profile_nickname account_email" 로 넣으면 이메일도 받는다.
    scope = "profile_nickname"

    def fetch_profile(self, access_token: str) -> SocialProfile:
        body = self._profile_body(access_token)
        uid = str(body.get("id") or "").strip()
        if not uid:
            raise SocialAuthError("카카오 사용자 정보를 받지 못했어요.")
        account = body.get("kakao_account") if isinstance(body.get("kakao_account"), dict) else {}
        profile = account.get("profile") if isinstance(account.get("profile"), dict) else {}
        # 카카오는 '유효한 주소인가(is_email_valid)'와 '본인 확인됐나(is_email_verified)'를
        # 따로 준다. 둘 다 참일 때만 검증된 것으로 본다 — 기존 계정 자동 연결의 근거라 엄격히.
        verified = bool(account.get("is_email_valid")) and bool(account.get("is_email_verified"))
        return SocialProfile(
            provider=self.key,
            provider_user_id=uid,
            email=self._clean_email(account.get("email")),
            email_verified=verified,
            nickname=(str(profile.get("nickname") or "").strip() or None),
            birth_date=_parse_birth(account.get("birthyear"), account.get("birthday")),
        )


def _naver_reason(code: str, desc: str) -> str:
    """네이버 토큰 오류를 '사용자가 다음에 무엇을 하면 되는지'로 바꾼다.

    왜: 종전엔 전부 "네이버 인증 토큰을 받지 못했어요"였다. 원문 코드를 괄호로 붙여 뒀지만
    `invalid_request: no valid data in session` 같은 문구는 사용자에게 아무 지시도 주지 못한다.
    실제로 운영자가 이 화면에서 막혔다(2026-08-10) — 원인은 '이미 쓴 인가 코드'였고,
    할 일은 '처음부터 다시'였는데 그 말이 화면에 없었다.

    ★인가 코드는 1회용이다. 콜백 화면에서 새로고침하거나 뒤로 가기로 되돌아오면 같은 코드를
    다시 보내게 되고, 네이버는 그것을 'no valid data in session'으로 거절한다. 흔한 경로라
    오류가 아니라 안내로 다룬다.
    """
    low = f"{code} {desc}".lower()
    if "session" in low or "expire" in low or "invalid_request" in code:
        return "인증이 만료됐거나 이미 사용된 요청이에요. 로그인 화면에서 처음부터 다시 시도해 주세요."
    if "client" in code:  # invalid_client · unauthorized_client — 설정 문제(사용자 잘못 아님)
        return "네이버 로그인 설정에 문제가 있어요. 잠시 후 다시 시도하거나 관리자에게 알려 주세요."
    return "네이버 인증 토큰을 받지 못했어요."


class NaverProvider(SocialProvider):
    key = "naver"
    label = "네이버"
    authorize_endpoint = "https://nid.naver.com/oauth2.0/authorize"
    token_endpoint = "https://nid.naver.com/oauth2.0/token"
    profile_endpoint = "https://openapi.naver.com/v1/nid/me"
    reauth_params = {"auth_type": "reprompt"}  # 네이버 — 재인증(동의 화면 재노출)
    # 네이버 프로필 응답에는 이메일 검증 필드가 없다. 네이버 계정은 가입 시 이메일 본인확인을
    # 거치므로 검증된 것으로 본다(자동 연결 허용). 정책을 조이려면 이 값을 False로 두면
    # 되고, 그러면 네이버 사용자는 항상 신규 가입 또는 로그인 후 수동 연결을 거친다.
    email_verified_default = True

    def exchange_code(self, code: str, redirect_uri: str, state: str) -> str:
        """네이버 토큰 발급 — 공식 규격은 **GET + 쿼리스트링**이다.

        다른 provider(POST form)와 유일하게 다른 지점이 둘 있다:
        ① state 를 함께 보내야 한다  ② 문서화된 메서드가 GET 이다.
        redirect_uri 는 네이버 토큰 요청 규격에 없어 보내지 않는다(무시되거나 오류가 된다).
        """
        body = self._request(
            "GET",
            self.token_endpoint,
            params={
                "grant_type": "authorization_code",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "state": state,
            },
        )
        token = str(body.get("access_token") or "")
        if not token:
            # ★네이버는 실패도 HTTP 200 에 error/error_description 으로 싣는다. 이 값을 삼키면
            # "토큰을 못 받았다"만 남아 원인(잘못된 시크릿·만료된 코드·미저장 Callback URL)을
            # 구분할 수 없다 — 원문 코드는 괄호로 남겨 진단할 수 있게 한다.
            code_ = str(body.get("error") or "")[:60]
            desc = str(body.get("error_description") or "")[:120]
            detail = f" ({code_}{': ' + desc if desc else ''})" if code_ else ""
            raise SocialAuthError(f"{_naver_reason(code_, desc)}{detail}", provider_code=code_ or None)
        return token

    def fetch_profile(self, access_token: str) -> SocialProfile:
        body = self._profile_body(access_token)
        # 네이버는 HTTP 200에 resultcode로 실패를 싣는다 — 200만으로 성공을 판단하지 않는다.
        if str(body.get("resultcode") or "") not in ("00", ""):
            raise SocialAuthError(
                "네이버 사용자 정보를 받지 못했어요.", provider_code=str(body.get("resultcode"))[:80]
            )
        res = body.get("response") if isinstance(body.get("response"), dict) else {}
        uid = str(res.get("id") or "").strip()
        if not uid:
            raise SocialAuthError("네이버 사용자 정보를 받지 못했어요.")
        email = self._clean_email(res.get("email"))
        return SocialProfile(
            provider=self.key,
            provider_user_id=uid,
            email=email,
            email_verified=bool(email) and self.email_verified_default,
            nickname=(str(res.get("nickname") or res.get("name") or "").strip() or None),
            birth_date=_parse_birth(res.get("birthyear"), res.get("birthday")),
        )


class GoogleProvider(SocialProvider):
    key = "google"
    label = "구글"
    authorize_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"
    profile_endpoint = "https://openidconnect.googleapis.com/v1/userinfo"
    scope = "openid email profile"
    # prompt=select_account — 구글은 ★항상 계정 선택 화면을 띄운다. 한 기기에서 계정을 바꿔
    #   로그인할 수 있어야 하고, 구글은 다중 로그인이 흔해서 기본값으로 두는 편이 맞다.
    #   (그래서 구글엔 reauth_params 가 따로 필요 없다 — 이미 매번 물어본다)
    # access_type=online — refresh token 을 받지 않는다(우리는 저장하지 않으므로 불필요).
    extra_authorize_params = {"access_type": "online", "prompt": "select_account"}

    def fetch_profile(self, access_token: str) -> SocialProfile:
        body = self._profile_body(access_token)
        uid = str(body.get("sub") or "").strip()
        if not uid:
            raise SocialAuthError("구글 사용자 정보를 받지 못했어요.")
        return SocialProfile(
            provider=self.key,
            provider_user_id=uid,
            email=self._clean_email(body.get("email")),
            email_verified=bool(body.get("email_verified")),
            nickname=(str(body.get("name") or body.get("given_name") or "").strip() or None),
            birth_date=None,  # 구글 userinfo에는 생년월일이 없다(People API 별도 동의 필요)
        )


_CLASSES = {"kakao": KakaoProvider, "naver": NaverProvider, "google": GoogleProvider}


def provider_credentials(settings, provider: str) -> tuple[str, str]:
    return (
        getattr(settings, f"{provider.upper()}_CLIENT_ID", "") or "",
        getattr(settings, f"{provider.upper()}_CLIENT_SECRET", "") or "",
    )


def is_configured(settings, provider: str) -> bool:
    """client_id가 있으면 사용 가능. 프론트가 버튼을 그릴지 판단하는 근거이기도 하다."""
    if provider not in _CLASSES:
        return False
    client_id, _ = provider_credentials(settings, provider)
    return bool(client_id.strip())


def build_provider(settings, provider: str, *, client: httpx.Client | None = None) -> SocialProvider:
    """provider 어댑터 생성 — 지원하지 않거나 키가 없으면 SocialAuthError.

    {PROVIDER}_SCOPES 설정이 있으면 그 값으로 동의항목 요청을 덮어쓴다(콘솔에서 열어 둔
    항목과 어긋나면 로그인이 거절되므로 환경별로 맞출 수 있어야 한다)."""
    cls = _CLASSES.get(provider)
    if cls is None:
        raise SocialAuthError("지원하지 않는 소셜 로그인이에요.")
    client_id, client_secret = provider_credentials(settings, provider)
    scope = (getattr(settings, f"{provider.upper()}_SCOPES", "") or "").strip() or None
    return cls(client_id, client_secret, client=client, scope=scope)
