"""프로메테우스 조회 클라이언트 — 클러스터 지표를 PromQL로 읽는다.

왜 이게 생겼나: 옛 환경은 VM 5대라 각 VM의 에이전트가 자기 지표를 밀어넣으면 됐다.
새 환경은 쿠버네티스라 ★파드가 수시로 생겼다 사라지고 노드를 옮겨 다닌다 — 에이전트를
심을 대상이 고정되지 않는다. 대신 클러스터 안에 프로메테우스가 이미 모든 노드·파드를
걷고 있으므로, 백엔드가 ★그것에 물어보는 쪽이 맞다.

가짜 성공 금지 규약(stt_client·ai_client와 동일): 주소가 없으면 호출 전에
PrometheusNotConfiguredError, 호출·파싱 실패는 PrometheusError로 정직하게 전파한다.
★"못 읽었는데 0을 돌려주는" 일은 하지 않는다 — 0%는 "한가하다"로 읽혀서, 실제로는
수집이 끊긴 상태를 정상으로 위장한다. 못 읽으면 그 서버 카드는 '오래됨'으로 남아야 한다.

기존 의존성 httpx로 직접 호출한다(신규 SDK 불필요 — 다른 클라이언트와 같은 이유).
인증이 없는 이유: 클러스터 내부 ClusterIP라 밖에서 닿지 않는다.
"""

import httpx

_TIMEOUT_SEC = 10.0  # 대시보드 요청을 붙잡지 않을 만큼 짧게. 배경 수집도 같은 값


class PrometheusNotConfiguredError(Exception):
    """PROMETHEUS_URL 미설정 — 클러스터 지표를 쓸 수 없다(호출 전에 발생)."""


class PrometheusError(Exception):
    """프로메테우스 호출·파싱 실패 — 원인을 담아 정직하게 전파한다."""


def instant_query(expr: str, *, base_url: str, timeout: float = _TIMEOUT_SEC) -> list[dict]:
    """PromQL 한 시점 질의 → [{"labels": {...}, "value": float}].

    프로메테우스의 응답 모양(data.result[].metric / .value=[시각, "문자열"])을 이 함수에서
    한 번만 벗겨, 호출부는 라벨 사전과 float만 다루게 한다.

    값이 NaN인 계열은 ★버린다 — float("nan")이 그대로 흘러가면 비교·반올림이 조용히
    이상해지고(NaN >= 90 은 False), 나중에 JSON 직렬화에서 터진다.
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise PrometheusNotConfiguredError("프로메테우스 주소(PROMETHEUS_URL)가 설정되지 않았습니다.")
    try:
        resp = httpx.get(f"{url}/api/v1/query", params={"query": expr}, timeout=timeout)
    except httpx.HTTPError as e:
        raise PrometheusError(f"프로메테우스 호출 실패(네트워크): {e}") from e
    if resp.status_code != 200:
        raise PrometheusError(f"프로메테우스 오류(HTTP {resp.status_code}): {resp.text[:200]}")

    body = resp.json()
    if body.get("status") != "success":
        raise PrometheusError(f"프로메테우스 질의 거절: {str(body.get('error'))[:200]}")
    result = (body.get("data") or {}).get("result")
    if not isinstance(result, list):
        raise PrometheusError("프로메테우스 응답 형식이 예상과 다릅니다(data.result 없음).")

    out: list[dict] = []
    for item in result:
        try:
            value = float(item["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if value != value:  # NaN — 위 설명 참조
            continue
        out.append({"labels": item.get("metric") or {}, "value": value})
    return out
