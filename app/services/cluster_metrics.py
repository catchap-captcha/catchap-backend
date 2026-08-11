"""쿠버네티스 클러스터 지표 → server_metrics 스냅샷.

host_metrics.collect()와 ★같은 dict 모양을 돌려준다. 그래서 호출부(monitoring._upsert)는
값의 출처가 psutil인지 프로메테우스인지 몰라도 되고, 화면·시계열 코드는 하나도 안 바뀐다.

★왜 psutil로 재던 것을 바꾸나 (0805 실측)
  백엔드 파드가 2벌인데 둘 다 server_key="backend" ★한 행에 덮어썼다.
    4mqnc cpu=2.5% / fw94z cpu=1.2%  ← 같은 순간에 서로 다른 값. 요청마다 화면이 튀었다.
  그리고 컨테이너 안의 psutil은 cgroup 제한을 안 봐서 ★파드가 아니라 노드를 재고 있었다.
    파드가 보고한 mem_total 15,993MB = 노드 용량 16,377,056Ki. 정확히 같다.
  즉 "backend 카드"는 실제로는 그 요청을 받은 파드가 앉은 ★노드였다.

★그래서 무엇을 세는가
  노드     실제 하드웨어 자원. CPU·메모리·디스크·load1 (node-exporter)
  서비스   파드를 배포 단위로 묶은 합계. CPU·메모리 (cAdvisor + kube-state-metrics)

★파드 카드의 백분율을 무엇으로 나누나 — 여기가 설계의 핵심이다
  메모리 = 사용 / ★메모리 제한.  제한을 넘으면 커널이 파드를 죽인다(OOMKill).
           즉 이 백분율은 그대로 ★"죽기까지 얼마나 남았나"다. CRIT 85%가 딱 맞는다.
  CPU    = 사용 / ★클러스터 전체 코어.  ⚠️요청(request) 대비로 하지 않는다 —
           요청은 스케줄링용 최소 보장이라 넘겨 쓰는 것이 정상인데, 그걸 백분율로 쓰면
           CRIT 90%가 ★멀쩡한 상태에서 계속 울린다. 파드엔 CPU 제한이 없으므로
           "파드 CPU 위험"이라는 개념 자체가 없고, 위험한 것은 노드 쪽이다(노드 카드가 본다).
"""

import logging

from app.clients.prometheus_client import PrometheusError, instant_query

_log = logging.getLogger(__name__)

NAMESPACE = "catchap"

# 배포 이름 → 화면 이름. 파드 이름이 "<배포>-<복제셋해시>-<파드해시>"라 접두사로 묶는다.
# ★다섯 중 어느 것도 다른 것의 접두사가 아니다
#   (backend-api·frontend·captcha-api·behavior-ai·stt-worker).
# ★2026-08-11: stt-worker 를 넣었다. STT 지표는 그동안 ★클러스터 밖 옛 GPU VM 이
#   에이전트로 밀어 넣고 있었는데(server_key="gpu-stt"), 그 VM 을 내리면서
#   STT 만 화면에서 사라지게 됐다. 이제 클러스터가 직접 모은다.
POD_GROUPS: list[tuple[str, str]] = [
    ("backend-api", "백엔드 API"),
    ("frontend", "프론트"),
    ("captcha-api", "캡차 API"),
    ("behavior-ai", "행동 AI"),
    ("stt-worker", "STT 워커"),
]

# 노드 지표는 node-exporter가 준다. instance는 "10.0.2.128:9100" 형태(노드 이름이 아니다) —
# node_uname_info의 nodename으로 바꿔서 사람이 읽을 수 있는 키를 만든다.
_Q_NODE_NAME = "node_uname_info"
_Q_NODE_CPU = '100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])))'
_Q_NODE_CORES = 'count by (instance) (node_cpu_seconds_total{mode="idle"})'
_Q_NODE_LOAD1 = "node_load1"
_Q_NODE_MEM_TOTAL = "node_memory_MemTotal_bytes"
_Q_NODE_MEM_AVAIL = "node_memory_MemAvailable_bytes"
# 루트 파일시스템만. tmpfs·overlay를 빼지 않으면 컨테이너 계층까지 세어 값이 흐려진다.
_FS = 'mountpoint="/",fstype!~"tmpfs|overlay|squashfs"'
_Q_NODE_FS_SIZE = f"node_filesystem_size_bytes{{{_FS}}}"
_Q_NODE_FS_AVAIL = f"node_filesystem_avail_bytes{{{_FS}}}"

# 파드 지표. container!="" 로 pause 컨테이너(파드당 1개, 자원 0)를 뺀다.
_POD = f'namespace="{NAMESPACE}",container!=""'
_Q_POD_CPU = f"sum by (pod) (rate(container_cpu_usage_seconds_total{{{_POD}}}[5m]))"
_Q_POD_MEM = f"sum by (pod) (container_memory_working_set_bytes{{{_POD}}})"
_Q_POD_MEM_LIMIT = (
    f'sum by (pod) (kube_pod_container_resource_limits{{namespace="{NAMESPACE}",resource="memory"}})'
)

_MB = 1024 * 1024
_GB = 1024 * 1024 * 1024

# 노드 카드의 server_key 앞에 붙인다 — 화면에서 노드를 서비스보다 앞에 놓기 위한 표식.
# ★카카오의 노드 이름 규칙("host-10-0-2-128")으로 판별하면 이름 규칙이 바뀔 때 조용히 깨진다.
NODE_KEY_PREFIX = "node:"
# server_metrics.server_key = String(40). 지금 노드 키는 20자라 여유가 있지만, 노드 이름은
# 우리가 정하는 값이 아니므로(카카오가 붙인다) 넘칠 때 ★DB에서 터지지 않게 여기서 자른다.
_KEY_MAX = 40
_LABEL_MAX = 60


def _by_label(rows: list[dict], label: str) -> dict[str, float]:
    """[{labels, value}] → {라벨값: 수치}. 같은 라벨이 겹치면 나중 것이 이긴다(거의 없음)."""
    return {r["labels"].get(label, ""): r["value"] for r in rows if r["labels"].get(label)}


def _node_snapshots(base_url: str) -> list[dict]:
    """노드별 스냅샷 — node-exporter가 보는 실제 하드웨어."""
    names = {
        r["labels"].get("instance", ""): r["labels"].get("nodename", "")
        for r in instant_query(_Q_NODE_NAME, base_url=base_url)
    }
    cpu = _by_label(instant_query(_Q_NODE_CPU, base_url=base_url), "instance")
    cores = _by_label(instant_query(_Q_NODE_CORES, base_url=base_url), "instance")
    load1 = _by_label(instant_query(_Q_NODE_LOAD1, base_url=base_url), "instance")
    mem_total = _by_label(instant_query(_Q_NODE_MEM_TOTAL, base_url=base_url), "instance")
    mem_avail = _by_label(instant_query(_Q_NODE_MEM_AVAIL, base_url=base_url), "instance")
    fs_size = _by_label(instant_query(_Q_NODE_FS_SIZE, base_url=base_url), "instance")
    fs_avail = _by_label(instant_query(_Q_NODE_FS_AVAIL, base_url=base_url), "instance")

    out: list[dict] = []
    for instance, nodename in names.items():
        if not nodename:
            continue
        total = mem_total.get(instance, 0.0)
        avail = mem_avail.get(instance, 0.0)
        size = fs_size.get(instance, 0.0)
        free = fs_avail.get(instance, 0.0)
        out.append(
            {
                "server_key": f"{NODE_KEY_PREFIX}{nodename}"[:_KEY_MAX],  # 예: node:host-10-0-2-128
                "label": f"노드 ({instance.split(':')[0]})"[:_LABEL_MAX],
                "host": instance.split(":")[0],
                "cpu_pct": round(cpu.get(instance, 0.0), 1),
                "cpu_cores": int(cores.get(instance, 0)),
                "load1": round(load1[instance], 2) if instance in load1 else None,
                "mem_pct": round((1 - avail / total) * 100, 1) if total else 0.0,
                "mem_used_mb": int((total - avail) / _MB),
                "mem_total_mb": int(total / _MB),
                "disk_pct": round((1 - free / size) * 100, 1) if size else 0.0,
                "disk_used_gb": round((size - free) / _GB, 1),
                "disk_total_gb": round(size / _GB, 1),
                "gpu_present": False,
            }
        )
    return out


def _service_snapshots(base_url: str, cluster_cores: float) -> list[dict]:
    """서비스별 스냅샷 — 같은 배포의 파드들을 하나로 묶은 합계.

    ★파드를 각각 카드로 내지 않는 이유: 파드 이름에 해시가 들어 있어 배포할 때마다 바뀐다.
    그대로 두면 server_metrics에 죽은 행이 배포 횟수만큼 쌓인다. 파드 단위로 보고 싶을 때는
    그라파나가 있다 — 이 화면은 "서비스가 건강한가"를 본다.
    """
    cpu = _by_label(instant_query(_Q_POD_CPU, base_url=base_url), "pod")
    mem = _by_label(instant_query(_Q_POD_MEM, base_url=base_url), "pod")
    limit = _by_label(instant_query(_Q_POD_MEM_LIMIT, base_url=base_url), "pod")

    out: list[dict] = []
    for key, label in POD_GROUPS:
        pods = [p for p in mem if p.startswith(f"{key}-")]
        if not pods:
            continue  # 파드가 하나도 없으면 카드를 만들지 않는다 → 화면에 '미수집'으로 남는다
        used_cores = sum(cpu.get(p, 0.0) for p in pods)
        used_bytes = sum(mem.get(p, 0.0) for p in pods)
        limit_bytes = sum(limit.get(p, 0.0) for p in pods)
        out.append(
            {
                "server_key": key,
                "label": f"{label} (파드 {len(pods)})",
                "host": f"{NAMESPACE} 네임스페이스",
                # 클러스터 전체 코어 대비 점유율(위 머리말 설명 참조)
                "cpu_pct": round(used_cores / cluster_cores * 100, 1) if cluster_cores else 0.0,
                "cpu_cores": len(pods),  # 이 자리는 '몇 벌인지'로 쓴다
                "load1": None,
                # ★메모리 제한 대비 = OOMKill까지 얼마나 남았나
                "mem_pct": round(used_bytes / limit_bytes * 100, 1) if limit_bytes else 0.0,
                "mem_used_mb": int(used_bytes / _MB),
                "mem_total_mb": int(limit_bytes / _MB),
                # 파드에는 자기 디스크가 없다(노드 것을 쓴다) — 노드 카드가 본다
                "disk_pct": 0.0,
                "disk_used_gb": 0.0,
                "disk_total_gb": 0.0,
                "gpu_present": False,
            }
        )
    return out


def collect(base_url: str) -> list[dict]:
    """클러스터 전체 스냅샷 — 노드들 + 서비스들. monitoring._upsert에 그대로 넣을 수 있다.

    프로메테우스에 못 닿으면 PrometheusError를 그대로 올린다 — ★0으로 채운 가짜 스냅샷을
    돌려주지 않는다. 호출부는 그걸 잡아 로그만 남기고, 화면의 각 카드는 '오래됨'으로 남는다.
    그게 "수집이 끊겼다"는 사실을 정직하게 보여주는 유일한 방법이다.
    """
    nodes = _node_snapshots(base_url)
    cluster_cores = float(sum(n["cpu_cores"] for n in nodes))
    if not cluster_cores:
        # 노드를 하나도 못 읽었는데 파드 백분율을 0으로 낼 수는 없다(0%는 '한가함'으로 읽힌다).
        raise PrometheusError("노드 지표를 하나도 읽지 못했습니다(node-exporter 수집 확인 필요).")
    return nodes + _service_snapshots(base_url, cluster_cores)
