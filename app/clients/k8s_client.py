"""쿠버네티스 API 에서 ★배포 상태만 읽는다 — 운영 콘솔의 「지금 무엇이 떠 있나」용.

## 왜 필요한가

지금까지 「어느 코드가 떠 있는지」를 아는 방법은 서버에 들어가 `kubectl` 을 치는 것뿐이었다.
그 방법을 아는 사람이 한 명이라, 배포 사고가 나면 그 한 명을 기다려야 했다.
운영 콘솔에서 **이미지 태그와 준비 상태**를 볼 수 있으면 누구나 확인할 수 있다.

## ★읽기만 한다

파드에 마운트된 ServiceAccount 토큰을 쓰되, 권한은 `catchap` 네임스페이스의
`deployments` · `pods` 를 **get/list 하는 것뿐**이다(k8s/backend/25-rbac-읽기.yaml).
바꾸는 동사(create·patch·delete)는 아예 안 준다. 여기서 실수해도 클러스터를 못 바꾼다.

⚠️**ArgoCD 토큰을 쓰지 않는 이유** — ArgoCD 는 클러스터 전체를 바꿀 수 있다.
그 토큰을 백엔드에 두면 운영자 계정 하나가 뚫렸을 때 클러스터가 통째로 넘어간다.
필요한 것은 「무엇이 떠 있나」뿐이므로 그만큼만 읽는다.

## 쿠버네티스가 아닌 곳에서는

로컬 개발·시험에는 토큰이 없다. 그때는 `KubernetesUnavailable` 을 올린다.
★빈 목록을 돌려주어 「배포 없음」처럼 보이게 만들지 않는다 — 그러면 진짜로 파드가
0개인 것과 구분이 안 된다.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
TOKEN_PATH = f"{SA_DIR}/token"
CA_PATH = f"{SA_DIR}/ca.crt"
NAMESPACE_PATH = f"{SA_DIR}/namespace"

TIMEOUT_SEC = 6.0


class KubernetesUnavailable(RuntimeError):
    """쿠버네티스 API 를 못 읽었다 — 무엇이 없는지 문장에 담는다."""


@dataclass
class DeploymentStatus:
    """배포 하나의 지금 상태."""

    name: str
    image: str
    tag: str
    replicas_desired: int
    replicas_ready: int
    updated_at: str | None = None
    conditions: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return self.replicas_desired > 0 and self.replicas_ready == self.replicas_desired

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "image": self.image,
            "tag": self.tag,
            "replicas_desired": self.replicas_desired,
            "replicas_ready": self.replicas_ready,
            "healthy": self.healthy,
            "updated_at": self.updated_at,
            "conditions": self.conditions,
        }


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError as exc:
        raise KubernetesUnavailable(
            f"{path} 를 읽을 수 없습니다 — 쿠버네티스 안에서 도는 중이 아니거나 "
            f"ServiceAccount 토큰이 마운트되지 않았습니다 ({exc.strerror})"
        ) from None


def _api_base() -> str:
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS") or os.environ.get(
        "KUBERNETES_SERVICE_PORT", "443"
    )
    if not host:
        raise KubernetesUnavailable(
            "KUBERNETES_SERVICE_HOST 가 없습니다 — 쿠버네티스 안에서 도는 중이 아닙니다"
        )
    return f"https://{host}:{port}"


def _get(path: str, opener=urllib.request.urlopen) -> dict:
    """쿠버네티스 API 를 GET 한다. 실패는 KubernetesUnavailable 로 바꿔 올린다.

    ★검사 순서가 중요하다 — 「쿠버네티스 안인가」를 ★맨 먼저 본다.
      SSL 준비를 먼저 하면, 쿠버네티스가 아닌 곳에서 CA 파일이 없다는
      `FileNotFoundError` 가 먼저 터져 ★진짜 원인(여기가 클러스터가 아니다)이 가려진다.
    """
    base = _api_base()          # ← 여기가 클러스터인가 (가장 싸고 가장 근본적인 검사)
    token = _read(TOKEN_PATH)   # ← 토큰이 있는가
    ctx = ssl.create_default_context(cafile=CA_PATH)
    req = urllib.request.Request(
        base + path,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with opener(req, timeout=TIMEOUT_SEC, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # ★403 은 「권한이 없다」는 뜻이다. 연결이 안 되는 것과 구분해서 말한다 —
        #   그래야 RBAC 을 고쳐야 하는지, 네트워크를 봐야 하는지 알 수 있다.
        if exc.code == 403:
            raise KubernetesUnavailable(
                "쿠버네티스가 403 을 돌려줬습니다 — 권한(Role/RoleBinding)이 없습니다. "
                "k8s/backend/25-rbac-읽기.yaml 이 적용됐는지 확인하세요"
            ) from None
        raise KubernetesUnavailable(
            f"쿠버네티스 API 가 HTTP {exc.code} 를 돌려줬습니다"
        ) from None
    except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
        raise KubernetesUnavailable(
            f"쿠버네티스 API 에 못 붙었습니다 ({exc})"
        ) from None


def current_namespace() -> str:
    return _read(NAMESPACE_PATH)


def _split_tag(image: str) -> str:
    """이미지 문자열에서 태그만 뽑는다.

    ⚠️레지스트리 주소에 포트가 붙으면 콜론이 두 번 나온다
      (host:5000/repo/name:tag). ★마지막 / 뒤에서만 콜론을 찾는다.
    """
    last = image.rsplit("/", 1)[-1]
    return last.split(":", 1)[1] if ":" in last else "(태그 없음)"


def _condition_lines(status: dict) -> list[str]:
    """정상이 아닌 조건만 사람이 읽을 문장으로."""
    out = []
    for c in status.get("conditions") or []:
        if c.get("status") == "True":
            continue
        t = c.get("type", "?")
        msg = (c.get("message") or c.get("reason") or "").strip()
        out.append(f"{t}: {msg}" if msg else t)
    return out


def list_deployments(namespace: str | None = None, opener=urllib.request.urlopen) -> list[DeploymentStatus]:
    """네임스페이스의 배포 목록을 읽어 온다 — 이름순.

    ★못 읽으면 예외를 올린다. 빈 목록으로 「배포 0개」처럼 보이게 만들지 않는다.
    """
    ns = namespace or current_namespace()
    body = _get(f"/apis/apps/v1/namespaces/{ns}/deployments", opener=opener)

    out: list[DeploymentStatus] = []
    for item in body.get("items") or []:
        meta = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        containers = ((spec.get("template") or {}).get("spec") or {}).get("containers") or []
        image = containers[0].get("image", "") if containers else ""

        updated = None
        for c in status.get("conditions") or []:
            if c.get("type") == "Progressing" and c.get("lastUpdateTime"):
                updated = c["lastUpdateTime"]
                break

        out.append(
            DeploymentStatus(
                name=meta.get("name", "?"),
                image=image,
                tag=_split_tag(image) if image else "(이미지 없음)",
                replicas_desired=int(spec.get("replicas") or 0),
                replicas_ready=int(status.get("readyReplicas") or 0),
                updated_at=updated,
                conditions=_condition_lines(status),
            )
        )
    return sorted(out, key=lambda d: d.name)


def snapshot(namespace: str | None = None, opener=urllib.request.urlopen) -> dict:
    """운영 콘솔이 그대로 쓸 수 있는 모양으로."""
    ns = namespace or current_namespace()
    deps = list_deployments(ns, opener=opener)
    return {
        "namespace": ns,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "deployments": [d.as_dict() for d in deps],
        "summary": {
            "total": len(deps),
            "healthy": sum(1 for d in deps if d.healthy),
            "unhealthy": sum(1 for d in deps if not d.healthy),
        },
    }
