"""로드밸런서 뒤에서 ★사용자 IP 를 보는 설정이 살아 있는지 지킨다.

★왜 이 시험이 있나
    이 앱의 IP 기준 횟수 제한은 `request.client.host` 를 쓴다.
    로드밸런서·인그레스 뒤에서는 그 값이 ★앞단(노드·LB)의 IP 가 되므로,
    uvicorn 을 `--proxy-headers` 로 띄우지 않으면
    ★모든 사용자가 하나의 IP 로 묶여 제한이 「전체 한 덩어리」가 된다.

    실제로 그 상태였다 — login_throttle 에 `pwresetip:192.168.57.1`(노드 IP)로 쌓이고 있었다.
    한 사람이 시간당 40건을 쓰면 ★그 시간 동안 다른 사람 전원이 막힌다.

★이 설정은 코드가 아니라 ★Dockerfile 에 있어서 일반 시험으로는 안 잡힌다.
    그래서 Dockerfile 을 직접 읽어 지킨다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"

# ★신뢰할 앞단만. `*` 로 열면 아무나 헤더를 위조해 제한을 우회할 수 있다.
#   10.0.0.0/16    VPC (LB·노드)
#   192.168.0.0/16 파드 네트워크 (인그레스가 hostNetwork 라 게이트웨이로 들어온다)
EXPECTED_TRUSTED = "10.0.0.0/16,192.168.0.0/16"


def _cmd_args() -> list[str]:
    src = DOCKERFILE.read_text(encoding="utf-8")
    joined = re.sub(r"\\n\s*", " ", src)          # Dockerfile 줄 이음을 편다
    m = re.search(r"^CMD\s+(\[.*\])\s*$", joined, re.M)
    assert m, "Dockerfile 에서 CMD(JSON 배열 형태)를 찾지 못했습니다"
    return json.loads(m.group(1))


def test_CMD_가_JSON_배열이고_uvicorn_을_띄운다():
    args = _cmd_args()
    assert args[0] == "uvicorn"
    assert args[1] == "app.main:app"


def test_프록시_헤더가_켜져_있다():
    """★없으면 모든 사용자가 앞단 IP 하나로 묶인다."""
    assert "--proxy-headers" in _cmd_args()


def test_신뢰할_앞단이_지정돼_있다():
    args = _cmd_args()
    assert "--forwarded-allow-ips" in args, "신뢰 대역을 지정하지 않았습니다"
    assert args[args.index("--forwarded-allow-ips") + 1] == EXPECTED_TRUSTED


def test_아무나_신뢰하지_않는다():
    """★`*` 는 헤더 위조를 허용한다 — 횟수 제한이 무력화된다."""
    args = _cmd_args()
    i = args.index("--forwarded-allow-ips")
    assert args[i + 1] != "*", "forwarded-allow-ips 를 * 로 열면 안 됩니다"


@pytest.mark.parametrize(
    "peer,신뢰",
    [
        ("192.168.57.1", True),    # ★실측한 앞단 (인그레스 노드 게이트웨이)
        ("10.0.6.202", True),      # ★실측한 앞단 (워커 노드 VPC IP)
        ("10.0.2.10", True),       # LB
        ("211.179.80.173", False), # ★바깥 사용자 — 신뢰하면 안 된다
        ("1.2.3.4", False),
        ("127.0.0.1", False),
    ],
)
def test_지정한_대역이_의도대로_갈린다(peer, 신뢰):
    """설정 문자열이 uvicorn 에서 ★실제로 어떻게 해석되는지 확인한다.

    ★"CIDR 를 적었으니 되겠지" 가 아니라 uvicorn 의 판정기에 직접 물어본다.
    """
    from uvicorn.middleware.proxy_headers import _TrustedHosts

    assert (peer in _TrustedHosts(EXPECTED_TRUSTED)) is 신뢰
