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
    ):
        if not client_id.strip():
            raise SocialAuthError(f"{self.label} 로그인이 아직 설정되지 않았어요.")
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
    def authorize_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
        if self.scope:
            params["scope"] = self.scope
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
    # 이메일은 선택 동의 항목이다 — 미동의로 와도 로그인은 성립해야 하므로 scope로 강제하지
    # 않는다(강제하면 이메일을 안 주는 사용자가 아예 못 들어온다).
    scope = "profile_nickname account_email"

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


class NaverProvider(SocialProvider):
    key = "naver"
    label = "네이버"
    authorize_endpoint = "https://nid.naver.com/oauth2.0/authorize"
    token_endpoint = "https://nid.naver.com/oauth2.0/token"
    profile_endpoint = "https://openapi.naver.com/v1/nid/me"
    # 네이버 프로필 응답에는 이메일 검증 필드가 없다. 네이버 계정은 가입 시 이메일 본인확인을
    # 거치므로 검증된 것으로 본다(자동 연결 허용). 정책을 조이려면 이 값을 False로 두면
    # 되고, 그러면 네이버 사용자는 항상 신규 가입 또는 로그인 후 수동 연결을 거친다.
    email_verified_default = True

    def exchange_code(self, code: str, redirect_uri: str, state: str) -> str:
        # 네이버 토큰 발급은 state를 함께 요구한다(다른 provider와 다른 유일한 지점).
        body = self._request(
            "POST",
            self.token_endpoint,
            data={
                "grant_type": "authorization_code",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "state": state,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        )
        token = str(body.get("access_token") or "")
        if not token:
            raise SocialAuthError("네이버 인증 토큰을 받지 못했어요.")
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

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        # prompt=select_account — 한 기기에서 계정을 바꿔 로그인할 수 있어야 한다.
        # access_type=online — refresh token을 받지 않는다(우리는 저장하지 않으므로 불필요).
        return (
            super().authorize_url(redirect_uri, state)
            + "&"
            + urlencode({"access_type": "online", "prompt": "select_account"})
        )

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
    """provider 어댑터 생성 — 지원하지 않거나 키가 없으면 SocialAuthError."""
    cls = _CLASSES.get(provider)
    if cls is None:
        raise SocialAuthError("지원하지 않는 소셜 로그인이에요.")
    client_id, client_secret = provider_credentials(settings, provider)
    return cls(client_id, client_secret, client=client)
