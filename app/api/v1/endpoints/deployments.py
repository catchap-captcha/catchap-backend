"""운영 콘솔 — 지금 무엇이 떠 있나 (★읽기 전용).

`GET /api/v1/ops/deployments` 하나뿐이다. 바꾸는 경로는 만들지 않는다.

## 왜 만들었나

배포된 코드가 어느 커밋인지 아는 방법이 「서버에 들어가 `kubectl` 을 치는 것」뿐이었다.
그 방법을 아는 사람이 한 명이라, 장애가 나면 그 한 명을 기다려야 했다.
이미지 태그가 커밋 해시라서, 화면에서 태그만 보면 `git show <해시>` 로 그 코드를 볼 수 있다.

## ⚠️바꾸는 기능은 일부러 없다

배포·롤백은 ArgoCD 화면에서 한다. 여기에 그 버튼을 두면
**운영자 계정 하나가 뚫렸을 때 클러스터를 바꿀 수 있게 된다.**
읽기만 하는 지금 구조에서는, 최악의 경우에도 「무엇이 떠 있나」가 새는 것에서 그친다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.clients import k8s_client
from app.core.permissions import Principal, require_ops

_log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/ops/deployments")
def ops_deployments(principal: Principal = Depends(require_ops)) -> dict:
    """지금 클러스터에 떠 있는 배포 목록.

    ★못 읽으면 503 과 함께 ★왜 못 읽었는지를 그대로 돌려준다.
      빈 목록을 주어 「배포 0개」처럼 보이게 만들지 않는다 — 그러면 진짜로 파드가
      내려간 것과 구분할 수 없다.
    """
    try:
        return k8s_client.snapshot()
    except k8s_client.KubernetesUnavailable as exc:
        _log.warning("배포 상태를 못 읽었습니다: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"배포 상태를 읽지 못했습니다 — {exc}",
        ) from None
