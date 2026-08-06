"""운영 콘솔 배포 상태 — 파싱·권한·못 읽을 때의 정직성.

★이 시험이 지키려는 것 세 가지
  ① 이미지 문자열에서 태그를 제대로 뽑는가 (레지스트리 포트가 붙어도)
  ② 못 읽었을 때 ★빈 목록으로 「배포 0개」처럼 보이지 않는가
  ③ 왜 못 읽었는지를 사람이 읽을 문장으로 돌려주는가 (권한 문제와 연결 문제를 구분)
"""

import json
import ssl
import urllib.error
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from app.clients import k8s_client
from app.clients.k8s_client import KubernetesUnavailable
from app.core.permissions import Principal, require_ops
from app.main import app


# ─────────────────────────── 태그 뽑기 ───────────────────────────

@pytest.mark.parametrize(
    "image,expected",
    [
        ("kc-sfacspace05.kr-central-2.kcr.dev/catchap-backend-repo/catchap-backend:ae8203c", "ae8203c"),
        ("nginx:1.27", "1.27"),
        # ⚠️레지스트리에 포트가 붙으면 콜론이 두 번 나온다 — 앞엣것에 속으면 안 된다
        ("registry.local:5000/team/app:v2", "v2"),
        ("registry.local:5000/team/app", "(태그 없음)"),
        ("busybox", "(태그 없음)"),
    ],
)
def test_split_tag(image, expected):
    assert k8s_client._split_tag(image) == expected


# ─────────────────────────── 목록 파싱 ───────────────────────────

def _fake_body(items):
    return {"items": items}


def _dep(name, image, desired, ready, conds=None):
    return {
        "metadata": {"name": name},
        "spec": {"replicas": desired, "template": {"spec": {"containers": [{"image": image}]}}},
        "status": {"readyReplicas": ready, "conditions": conds or []},
    }


class _Resp:
    def __init__(self, payload):
        self._b = BytesIO(json.dumps(payload).encode())

    def read(self):
        return self._b.read()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener_for(payload):
    def _open(req, timeout=None, context=None):
        return _Resp(payload)
    return _open


@pytest.fixture()
def in_cluster(monkeypatch, tmp_path):
    """쿠버네티스 안에서 도는 것처럼 만든다 — 토큰·CA·네임스페이스 파일."""
    (tmp_path / "token").write_text("tok", encoding="utf-8")
    (tmp_path / "namespace").write_text("catchap", encoding="utf-8")
    (tmp_path / "ca.crt").write_text("", encoding="utf-8")
    monkeypatch.setattr(k8s_client, "TOKEN_PATH", str(tmp_path / "token"))
    monkeypatch.setattr(k8s_client, "NAMESPACE_PATH", str(tmp_path / "namespace"))
    monkeypatch.setattr(k8s_client, "CA_PATH", str(tmp_path / "ca.crt"))
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    monkeypatch.setattr(ssl, "create_default_context", lambda **k: None)


def test_list_deployments_parses(in_cluster):
    payload = _fake_body([
        _dep("frontend", "reg/x/catchap-frontend:c48cdec", 2, 2),
        _dep("backend-api", "reg/x/catchap-backend:ae8203c", 2, 1),
    ])
    out = k8s_client.list_deployments("catchap", opener=_opener_for(payload))
    assert [d.name for d in out] == ["backend-api", "frontend"]      # 이름순
    assert out[0].tag == "ae8203c"
    assert out[0].replicas_ready == 1 and out[0].replicas_desired == 2
    assert out[0].healthy is False                                   # 1/2 는 정상이 아니다
    assert out[1].healthy is True


def test_snapshot_counts(in_cluster):
    payload = _fake_body([
        _dep("a", "r/a:1", 2, 2),
        _dep("b", "r/b:2", 2, 0),
        _dep("c", "r/c:3", 1, 1),
    ])
    snap = k8s_client.snapshot("catchap", opener=_opener_for(payload))
    assert snap["summary"] == {"total": 3, "healthy": 2, "unhealthy": 1}
    assert snap["namespace"] == "catchap"
    assert snap["collected_at"]


def test_replicas_zero_is_not_healthy(in_cluster):
    """★0/0 을 「정상」으로 세면 내려간 배포를 놓친다."""
    snap = k8s_client.snapshot("catchap", opener=_opener_for(_fake_body([_dep("z", "r/z:1", 0, 0)])))
    assert snap["summary"]["healthy"] == 0
    assert snap["deployments"][0]["healthy"] is False


def test_bad_conditions_are_reported(in_cluster):
    conds = [
        {"type": "Available", "status": "False", "message": "파드가 준비되지 않았습니다"},
        {"type": "Progressing", "status": "True", "message": "정상"},
    ]
    out = k8s_client.list_deployments("catchap", opener=_opener_for(_fake_body([_dep("x", "r/x:1", 1, 0, conds)])))
    # ★True 인 조건은 안 담는다 — 문제만 보여야 눈에 띈다
    assert out[0].conditions == ["Available: 파드가 준비되지 않았습니다"]


# ─────────────────────── 못 읽을 때의 정직성 ───────────────────────

def test_token_missing_is_honest_error(monkeypatch, tmp_path):
    """토큰이 없으면 ★무엇이 없는지 말한다 — 빈 목록이 아니다.

    ⚠️클러스터 안인 척은 해 둔다(HOST 를 넣는다). 안 그러면 ★더 앞선 검사에 걸려
      토큰 검사까지 가지도 못한다 — 그러면 이 시험이 아무것도 안 지킨다.
    """
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setattr(k8s_client, "TOKEN_PATH", str(tmp_path / "없는파일"))
    with pytest.raises(KubernetesUnavailable, match="읽을 수 없습니다"):
        k8s_client.list_deployments("catchap")


def test_no_service_host_is_checked_first(monkeypatch, tmp_path):
    """★「여기가 클러스터인가」를 맨 먼저 본다.

    이 검사가 뒤로 밀리면, 클러스터가 아닌 곳에서 CA 파일이 없다는
    FileNotFoundError 가 먼저 터져 ★진짜 원인이 가려진다(0807 에 실제로 겪음).
    """
    (tmp_path / "token").write_text("tok", encoding="utf-8")
    monkeypatch.setattr(k8s_client, "TOKEN_PATH", str(tmp_path / "token"))
    monkeypatch.setattr(k8s_client, "CA_PATH", str(tmp_path / "없는CA"))   # ← 있어도 안 건드려야 한다
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    with pytest.raises(KubernetesUnavailable, match="KUBERNETES_SERVICE_HOST"):
        k8s_client.list_deployments("catchap")


def test_403_says_it_is_a_permission_problem(in_cluster):
    """★403 은 「권한이 없다」다. 연결 실패와 섞어 말하면 엉뚱한 데를 고치게 된다."""
    def _open(req, timeout=None, context=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
    with pytest.raises(KubernetesUnavailable, match="권한"):
        k8s_client.list_deployments("catchap", opener=_open)


def test_connection_failure_says_so(in_cluster):
    def _open(req, timeout=None, context=None):
        raise urllib.error.URLError("연결 거부")
    with pytest.raises(KubernetesUnavailable, match="못 붙었습니다"):
        k8s_client.list_deployments("catchap", opener=_open)


# ─────────────────────────── 엔드포인트 ───────────────────────────

@pytest.fixture()
def ops_client():
    principal = Principal(kind="user", id="ops-1", role="ops")
    app.dependency_overrides[require_ops] = lambda: principal
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_endpoint_returns_snapshot(ops_client, monkeypatch):
    monkeypatch.setattr(
        k8s_client, "snapshot",
        lambda *a, **k: {"namespace": "catchap", "collected_at": "x",
                         "deployments": [], "summary": {"total": 0, "healthy": 0, "unhealthy": 0}},
    )
    r = ops_client.get("/api/v1/ops/deployments")
    assert r.status_code == 200
    assert r.json()["namespace"] == "catchap"


def test_endpoint_503_when_unreadable(ops_client, monkeypatch):
    """★못 읽으면 200 + 빈 목록이 아니라 503 이다.

    빈 목록으로 돌려주면 「배포가 하나도 없다」와 구분이 안 되어,
    진짜로 서비스가 내려갔을 때 화면이 조용하다.
    """
    def _boom(*a, **k):
        raise KubernetesUnavailable("권한(Role/RoleBinding)이 없습니다")
    monkeypatch.setattr(k8s_client, "snapshot", _boom)
    r = ops_client.get("/api/v1/ops/deployments")
    assert r.status_code == 503
    assert "권한" in r.json()["detail"]


def test_endpoint_needs_ops():
    """★로그인 없이 부르면 막힌다."""
    app.dependency_overrides.clear()
    r = TestClient(app).get("/api/v1/ops/deployments")
    assert r.status_code in (401, 403)
