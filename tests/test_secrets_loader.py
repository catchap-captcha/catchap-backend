"""Secrets Manager 로더 — 기동을 막는 코드라 실패 경로가 본체다.

이 로더는 앱이 뜨기 전에 돌고, 실패하면 **일부러 예외를 내서 기동을 막는다**.
그래서 "성공하면 값이 들어간다"보다 **"어떤 상황에서 어떻게 죽는가"** 가 더 중요하다.

★이 시험을 붙이게 된 계기 (2026-08-07)
    클러스터 ConfigMap 에는 이미 `SECRETS_BACKEND=kakaocloud` 가 들어 있는데
    `backend-secret` 에는 `SECRETS_ACCESS_KEY`·`SECRETS_SECRET_KEY` 가 **없었다.**
    이 상태로 로더를 배포하면 백엔드 파드가 전부 기동에 실패한다.
    → 그 상황을 시험으로 고정한다(아래 `test_부트스트랩_키가_없으면_기동을_막는다`).

★이름 오타도 같이 잡는다 — ConfigMap 은 `catchap-media-s3-key★s★`(복수)를 요구하는데
    문서·브랜치는 `catchap-media-s3-key`(단수)로 적혀 있었다. 이름이 하나만 틀려도
    로더가 예외를 낸다는 것을 시험으로 남긴다.

★네트워크에 나가지 않는다 — `_http` 를 가짜로 바꿔 응답을 흉내낸다.
"""

from __future__ import annotations

import io
import json

import pytest

from app.core import secrets_loader as sl


# ── 가짜 응답 만들기 ────────────────────────────────────────────────────────

class _Resp(io.BytesIO):
    """urlopen 이 돌려주는 것 흉내. json.load() 와 .headers 둘 다 받는다."""

    def __init__(self, payload, headers=None):
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.headers = headers or {}


def _fake_http(*, catalog, values, token="tok-abc"):
    """_http 를 대신할 함수를 만든다. 호출 순서: 토큰 → 목록 → 값."""
    calls = []

    def http(url, *, method="GET", body=None, headers=None):
        calls.append(url)
        if url.endswith("/auth/tokens"):
            return _Resp({}, {"X-Subject-Token": token})
        # ★목록 주소에는 ?limit=&offset= 이 붙는다 (페이지 넘기기 때문)
        if "/api/v1/secrets" in url and "/versions/" not in url:
            return _Resp({"secrets": catalog,
                          "pagination": {"offset": 0, "limit": 100, "total": len(catalog)}})
        for name, sid, payload in values:
            if f"/secrets/{sid}/versions/" in url:
                return _Resp(payload)
        raise AssertionError(f"예상 못 한 요청: {url}")

    http.calls = calls
    return http


def _env(**kw):
    base = {
        "SECRETS_BACKEND": "kakaocloud",
        "SECRETS_ACCESS_KEY": "ak",
        "SECRETS_SECRET_KEY": "sk",
        "SECRETS_NAMES": "catchap-auth-jwt-secret",
    }
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


# ── ① 꺼져 있을 때 ─────────────────────────────────────────────────────────

def test_기본값이면_아무것도_안_한다(monkeypatch):
    """★설정을 안 주면 이 파일이 없는 것과 똑같이 동작해야 한다."""
    def boom(*a, **k):
        raise AssertionError("네트워크에 나가면 안 된다")
    monkeypatch.setattr(sl, "_http", boom)

    r = sl.load_secrets_into_env({})
    assert r.backend == "none"
    assert r.loaded == []
    assert "미사용" in r.summary()


@pytest.mark.parametrize("value", ["none", "", "off", "false", "0", "NONE", " none "])
def test_끄는_값들은_모두_통한다(monkeypatch, value):
    monkeypatch.setattr(sl, "_http", lambda *a, **k: pytest.fail("나가면 안 된다"))
    assert sl.load_secrets_into_env({"SECRETS_BACKEND": value}).backend == "none"


def test_모르는_backend_값이면_조용히_넘어가지_않는다():
    """★오타를 none 으로 봐주면 비밀값 없이 떠서 엉뚱한 증상이 난다.

    ★"SECRETS_BACKEND" 라는 낱말만 검사하면 안 된다 — 뒤쪽 「비어 있습니다」 오류에도
      그 낱말이 들어 있어서, 이 검사를 통째로 지워도 시험이 통과해 버린다(변이 시험으로 확인).
      ★잘못된 값 자체(`aws`)를 말하는지 봐야 이 분기를 실제로 고정한다.
    """
    with pytest.raises(sl.SecretsLoadError) as e:
        sl.load_secrets_into_env({"SECRETS_BACKEND": "aws"})
    assert "aws" in str(e.value)


# ── ② 켜져 있는데 준비가 안 됐을 때 — ★실제로 겪은 상황 ────────────────────

def test_부트스트랩_키가_없으면_기동을_막는다(monkeypatch):
    """★0807 클러스터의 실제 상태다.

    ConfigMap 에 SECRETS_BACKEND=kakaocloud 는 있는데 backend-secret 에
    SECRETS_ACCESS_KEY·SECRETS_SECRET_KEY 가 없었다. 이때 조용히 넘어가면
    비밀값이 하나도 없는 채로 앱이 떠서 원인을 못 찾는 장애가 된다.
    """
    monkeypatch.setattr(sl, "_http", lambda *a, **k: pytest.fail("나가면 안 된다"))

    with pytest.raises(sl.SecretsLoadError) as e:
        sl.load_secrets_into_env(_env(SECRETS_ACCESS_KEY=None, SECRETS_SECRET_KEY=None))

    msg = str(e.value)
    # ★무엇이 비었는지 이름을 말해 줘야 한다 — 안 그러면 사람이 못 고친다
    assert "SECRETS_ACCESS_KEY" in msg and "SECRETS_SECRET_KEY" in msg


def test_읽을_목록이_없으면_기동을_막는다(monkeypatch):
    monkeypatch.setattr(sl, "_http", lambda *a, **k: pytest.fail("나가면 안 된다"))
    with pytest.raises(sl.SecretsLoadError) as e:
        sl.load_secrets_into_env(_env(SECRETS_NAMES=""))
    assert "SECRETS_NAMES" in str(e.value)


# ── ③ 제대로 도는 경우 ─────────────────────────────────────────────────────

_CATALOG = [
    {"name": "catchap-auth-jwt-secret", "id": "sid-1", "default_version": "v1"},
    {"name": "catchap-portone-keys", "id": "sid-2", "default_version": "v3"},
]


def test_읽어서_환경변수에_넣는다(monkeypatch):
    env = _env(SECRETS_NAMES="catchap-auth-jwt-secret,catchap-portone-keys")
    monkeypatch.setattr(sl, "_http", _fake_http(
        catalog=_CATALOG,
        values=[
            ("catchap-auth-jwt-secret", "sid-1", {"version": {"secret": {"JWT_SECRET_KEY": "j"}}}),
            ("catchap-portone-keys", "sid-2", {"version": {"secret": {
                "PORTONE_CHANNEL_KEY": "c", "PORTONE_API_SECRET": "s"}}}),
        ],
    ))

    r = sl.load_secrets_into_env(env)

    assert env["JWT_SECRET_KEY"] == "j"
    assert env["PORTONE_CHANNEL_KEY"] == "c"
    assert env["PORTONE_API_SECRET"] == "s"
    assert sorted(r.loaded) == ["JWT_SECRET_KEY", "PORTONE_API_SECRET", "PORTONE_CHANNEL_KEY"]
    assert r.secrets_read == ["catchap-auth-jwt-secret", "catchap-portone-keys"]


def test_값이_문자열로_한번_더_감싸_와도_읽는다(monkeypatch):
    env = _env()
    monkeypatch.setattr(sl, "_http", _fake_http(
        catalog=_CATALOG,
        values=[("x", "sid-1", {"version": {"secret": json.dumps({"JWT_SECRET_KEY": "j"})}})],
    ))
    sl.load_secrets_into_env(env)
    assert env["JWT_SECRET_KEY"] == "j"


def test_요약에는_값이_안_들어간다(monkeypatch):
    """★요약문은 로그로 나간다. 값이 섞이면 그대로 새어 나간다."""
    env = _env()
    monkeypatch.setattr(sl, "_http", _fake_http(
        catalog=_CATALOG,
        values=[("x", "sid-1", {"version": {"secret": {"JWT_SECRET_KEY": "비밀값입니다"}}})],
    ))
    r = sl.load_secrets_into_env(env)
    assert "비밀값입니다" not in r.summary()
    assert "JWT_SECRET_KEY" in r.summary()


# ── ④ 이름이 틀렸을 때 — ★단수/복수 사건 ──────────────────────────────────

def test_시크릿_이름이_하나라도_틀리면_기동을_막는다(monkeypatch):
    """★catchap-media-s3-key(단수) vs catchap-media-s3-keys(복수) 사건.

    ConfigMap 은 복수형을 요구하는데 문서·브랜치는 단수로 적혀 있었다.
    이 상태로 만들면 로더가 못 찾는다 — 그때 ★이름을 말해 줘야 한다.
    """
    monkeypatch.setattr(sl, "_http", _fake_http(
        catalog=[{"name": "catchap-media-s3-keys", "id": "sid-9", "default_version": "v1"}],
        values=[],
    ))
    with pytest.raises(sl.SecretsLoadError) as e:
        sl.load_secrets_into_env(_env(SECRETS_NAMES="catchap-media-s3-key"))
    assert "catchap-media-s3-key" in str(e.value)


def test_ID가_마스킹되면_접근_권한_문제라고_알려준다(monkeypatch):
    monkeypatch.setattr(sl, "_http", _fake_http(
        catalog=[{"name": "catchap-auth-jwt-secret", "id": "sid-****", "default_version": "v1"}],
        values=[],
    ))
    with pytest.raises(sl.SecretsLoadError) as e:
        sl.load_secrets_into_env(_env())
    assert "접근 명단" in str(e.value)


# ── ⑤ 시크릿이 프로세스를 못 바꾸게 ────────────────────────────────────────

@pytest.mark.parametrize("bad", ["PATH", "LD_PRELOAD", "PYTHONPATH", "SECRETS_BACKEND"])
def test_금지된_이름은_주입하지_않는다(monkeypatch, bad):
    """★Secrets Manager 를 쓸 수 있는 사람이 앱 실행 방식까지 바꾸면 안 된다."""
    env = _env()
    monkeypatch.setattr(sl, "_http", _fake_http(
        catalog=_CATALOG,
        values=[("x", "sid-1", {"version": {"secret": {bad: "나쁜값", "JWT_SECRET_KEY": "j"}}})],
    ))
    r = sl.load_secrets_into_env(env)
    assert env.get(bad) != "나쁜값"
    assert bad in r.skipped
    assert "JWT_SECRET_KEY" in r.loaded   # 나머지는 정상 주입


@pytest.mark.parametrize("bad", ["lowercase", "1STARTS_WITH_DIGIT", "has-dash", "_UNDERSCORE"])
def test_환경변수_형식이_아니면_주입하지_않는다(monkeypatch, bad):
    env = _env()
    monkeypatch.setattr(sl, "_http", _fake_http(
        catalog=_CATALOG,
        values=[("x", "sid-1", {"version": {"secret": {bad: "v", "JWT_SECRET_KEY": "j"}}})],
    ))
    r = sl.load_secrets_into_env(env)
    assert bad not in env
    assert bad in r.skipped


def test_주입할_것이_하나도_없으면_기동을_막는다(monkeypatch):
    """★읽기는 성공했는데 쓸 게 없으면 설정이 잘못된 것이다. 조용히 넘기지 않는다."""
    monkeypatch.setattr(sl, "_http", _fake_http(
        catalog=_CATALOG,
        values=[("x", "sid-1", {"version": {"secret": {"lowercase_only": "v"}}})],
    ))
    with pytest.raises(sl.SecretsLoadError):
        sl.load_secrets_into_env(_env())


# ── ⑥ 응답이 예상과 다를 때 ────────────────────────────────────────────────

@pytest.mark.parametrize("payload", [
    {"secret": {"A": "1"}},              # version 없음
    {"version": {"A": "1"}},             # secret 없음
    {"version": {"secret": ["A", "B"]}},  # 사전이 아님
])
def test_응답_모양이_다르면_기동을_막는다(monkeypatch, payload):
    monkeypatch.setattr(sl, "_http", _fake_http(catalog=_CATALOG, values=[("x", "sid-1", payload)]))
    with pytest.raises(sl.SecretsLoadError):
        sl.load_secrets_into_env(_env())


def test_IAM_이_토큰을_안_주면_기동을_막는다(monkeypatch):
    def http(url, *, method="GET", body=None, headers=None):
        if url.endswith("/auth/tokens"):
            return _Resp({}, {})       # X-Subject-Token 없음
        raise AssertionError("토큰 없이 다음 요청으로 가면 안 된다")
    monkeypatch.setattr(sl, "_http", http)

    with pytest.raises(sl.SecretsLoadError) as e:
        sl.load_secrets_into_env(_env())
    assert "X-Subject-Token" in str(e.value)


# ── ⑦ 결과가 기동 로그까지 도달하는가 ─────────────────────────────────────
#
# ★로더의 요약문은 원래 화면에 ★한 번도 안 나왔다. 두 가지가 겹쳐서다.
#   ① main.py 는 setup_logging() 을 먼저 부르는데, setup_logging() 자신이
#      get_settings() 를 부른다 → 로더는 dictConfig 가 적용되기 ★전에 끝난다
#   ② 로거 이름이 "app.core.secrets_loader" 라 catchap.* 설정에 안 걸리고
#      root(WARNING)로 떨어져 INFO 가 버려진다
# 제일 위험한 경우 — SECRETS_BACKEND 가 없어서 ★조용히 아무것도 안 한 경우 —
# 를 알려 주는 유일한 신호가 이 요약문이라 반드시 보여야 한다.

def test_결과를_나중에_꺼내_볼_수_있다(monkeypatch):
    monkeypatch.setattr(sl, "_http", lambda *a, **k: pytest.fail("나가면 안 된다"))
    sl.load_secrets_into_env({})
    r = sl.last_result()
    assert r is not None and r.backend == "none"
    assert "미사용" in r.summary()


def test_켜져_있을_때도_결과가_남는다(monkeypatch):
    monkeypatch.setattr(sl, "_http", _fake_http(
        catalog=_CATALOG,
        values=[("x", "sid-1", {"version": {"secret": {"JWT_SECRET_KEY": "j"}}})],
    ))
    sl.load_secrets_into_env(_env())
    r = sl.last_result()
    assert r is not None and r.backend == "kakaocloud"
    assert "JWT_SECRET_KEY" in r.summary()


def test_실패하면_결과가_안_남는다(monkeypatch):
    """★실패는 예외로 알린다 — 낡은 결과가 남아서 성공처럼 보이면 안 된다."""
    monkeypatch.setattr(sl, "_http", lambda *a, **k: pytest.fail("나가면 안 된다"))
    sl.load_secrets_into_env({})                    # 먼저 성공시켜 값을 남겨 두고
    assert sl.last_result().backend == "none"
    with pytest.raises(sl.SecretsLoadError):
        sl.load_secrets_into_env(_env(SECRETS_ACCESS_KEY=None, SECRETS_SECRET_KEY=None))
    assert sl.last_result().backend == "none"       # 예외 뒤에도 옛 결과 그대로(덮어쓰지 않음)


def test_로거_이름이_catchap_아래여야_한다():
    """★catchap.* 가 아니면 root(WARNING)로 떨어져 INFO 가 버려진다."""
    assert sl.logger.name.startswith("catchap.")


# ── ⑧ 목록이 여러 장으로 나뉘어 올 때 ─────────────────────────────────────
#
# ★★2026-08-07 실제로 터질 뻔한 것. 이 API 는 한 번에 ★10건만 준다.
#     {"pagination": {"offset": 0, "limit": 10, "total": 11}, "secrets": [...10건...]}
#   시크릿을 8개에서 11개로 늘린 날 이 선을 넘었고, 첫 장만 읽던 로더가
#   11번째(catchap-portone-keys)를 "없는 것"으로 보고 예외를 던졌다.
#   ★파드가 재시작되는 순간 백엔드 전체가 못 뜨는 상태였다.

def _paged_http(pages, values, token="tok"):
    """secrets 목록을 여러 장으로 나눠서 주는 가짜 _http."""
    calls = []

    def http(url, *, method="GET", body=None, headers=None):
        calls.append(url)
        if url.endswith("/auth/tokens"):
            return _Resp({}, {"X-Subject-Token": token})
        if "/api/v1/secrets?" in url:
            import urllib.parse as up
            q = up.parse_qs(up.urlparse(url).query)
            off = int(q.get("offset", ["0"])[0])
            flat = [s for pg in pages for s in pg]
            page = flat[off:off + 10]          # ★서버가 limit 을 무시하고 10건만 준다
            return _Resp({"secrets": page,
                          "pagination": {"offset": off, "limit": 10, "total": len(flat)}})
        for name, sid, payload in values:
            if f"/secrets/{sid}/versions/" in url:
                return _Resp(payload)
        raise AssertionError(f"예상 못 한 요청: {url}")

    http.calls = calls
    return http


def _many(n):
    return [{"name": f"s-{i:02d}", "id": f"sid-{i:02d}", "default_version": "v1"} for i in range(n)]


def test_목록이_11건이면_두_번째_장까지_읽는다(monkeypatch):
    """★첫 장만 읽으면 11번째를 못 찾는다 — 이것이 실제로 일어난 일이다."""
    catalog = _many(11)
    last = catalog[-1]["name"]                 # s-10 — 두 번째 장에만 있다
    monkeypatch.setattr(sl, "_http", _paged_http(
        [catalog],
        values=[(last, "sid-10", {"version": {"secret": {"JWT_SECRET_KEY": "j"}}})],
    ))
    env = _env(SECRETS_NAMES=last)
    r = sl.load_secrets_into_env(env)          # 첫 장만 읽으면 여기서 예외가 난다
    assert r.secrets_read == [last]
    assert env["JWT_SECRET_KEY"] == "j"


def test_장이_여러_개여도_전부_모은다(monkeypatch):
    catalog = _many(35)
    http = _paged_http([catalog], values=[
        ("s-34", "sid-34", {"version": {"secret": {"JWT_SECRET_KEY": "a"}}}),
        ("s-00", "sid-00", {"version": {"secret": {"SMTP_APP_PASSWORD": "b"}}}),
    ])
    monkeypatch.setattr(sl, "_http", http)
    env = _env(SECRETS_NAMES="s-00,s-34")
    r = sl.load_secrets_into_env(env)
    assert sorted(r.secrets_read) == ["s-00", "s-34"]
    # ★목록 요청이 여러 번 나갔는지 (한 번만 나갔으면 페이지를 안 넘긴 것)
    assert len([u for u in http.calls if "/api/v1/secrets?" in u]) >= 4


def test_못_찾으면_몇_건을_봤는지_말해_준다(monkeypatch):
    """★「없다」만 말하면 페이지가 잘린 것인지 진짜 없는 것인지 구분이 안 된다."""
    monkeypatch.setattr(sl, "_http", _paged_http([_many(11)], values=[]))
    with pytest.raises(sl.SecretsLoadError) as e:
        sl.load_secrets_into_env(_env(SECRETS_NAMES="없는-시크릿"))
    msg = str(e.value)
    assert "없는-시크릿" in msg and "11건" in msg


def test_페이지_정보가_없어도_동작한다(monkeypatch):
    """옛 응답 모양(배열만) 대비."""
    def http(url, *, method="GET", body=None, headers=None):
        if url.endswith("/auth/tokens"):
            return _Resp({}, {"X-Subject-Token": "t"})
        if "/api/v1/secrets?" in url:
            return _Resp([{"name": "only", "id": "sid-1", "default_version": "v1"}])
        return _Resp({"version": {"secret": {"JWT_SECRET_KEY": "j"}}})
    monkeypatch.setattr(sl, "_http", http)
    env = _env(SECRETS_NAMES="only")
    assert sl.load_secrets_into_env(env).secrets_read == ["only"]
