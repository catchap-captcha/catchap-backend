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
# ★어느 노드가 GPU 를 가졌나 — 카드 이름에 역할을 밝히기 위해서다.
#   "노드 (10.0.2.128)" 만 보면 ★운영자는 그게 무슨 서버인지 알 수 없다.
_Q_NODE_GPU_CAP = 'kube_node_status_capacity{resource="nvidia_com_gpu"}'
# ★DCGM 지표에는 노드 이름이 없다 — Hostname 라벨이 DCGM 파드 이름이다(0815 실측).
#   그래서 "DCGM 파드 → 노드" 를 kube_pod_info 로 따로 받아 파이썬에서 잇는다.
#   (PromQL 조인으로도 되지만 라벨 이름이 달라 label_replace 가 겹겹이 필요해 읽기 어렵다)
_Q_DCGM_POD_NODE = 'kube_pod_info{pod=~"dcgm-exporter.*"}'
# ★어느 앱이 어느 서버에 올라가 있나 — 노드 카드에 그걸 적어야 "서버와 앱" 이 이어져 보인다.
#   그전에는 서버 카드와 앱 카드가 따로 놀아서, 보는 사람이 둘을 머리로 이어야 했다.
_Q_POD_NODE = f'kube_pod_info{{namespace="{NAMESPACE}"}}'

_Q_NODE_GPU_UTIL = "DCGM_FI_DEV_GPU_UTIL"
_Q_NODE_GPU_MEM_USED = "DCGM_FI_DEV_FB_USED"
_Q_NODE_GPU_MEM_FREE = "DCGM_FI_DEV_FB_FREE"
# ★영역은 서브넷 대역으로 읽는다. kube_node_labels 가 꺼져 있고(kube-state-metrics 기본),
#   노드 읽기 RBAC 도 없다. 대역은 ★우리가 정한 값이라 노드 이름보다 안정적이다.
#   ⚠️서브넷을 바꾸면 여기도 바꿔야 한다(95-최종상태/09-네트워크-VPC 참고).
_ZONE_BY_PREFIX = {"10.0.1.": "2-a", "10.0.2.": "2-a", "10.0.5.": "2-b", "10.0.6.": "2-b"}


def _zone_of(ip: str) -> str:
    """사설 IP → 가용영역 꼬리표. 모르면 빈 문자열(억지로 붙이지 않는다)."""
    for pre, z in _ZONE_BY_PREFIX.items():
        if ip.startswith(pre):
            return z
    return ""
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

# ★GPU — DCGM exporter(catchap-infra k8s/81-dcgm-exporter.yaml)가 낸다.
#   0815 이전에는 GPU 지표가 프로메테우스에 ★하나도 없었다. nvidia-device-plugin 은
#   GPU 를 "할당"하는 것이지 "재는" 것이 아니라서, STT 워커 카드의 GPU 칸만 비어 있었다.
#
# 🚨★pod 가 아니라 exported_pod 로 물어야 한다.
#   DCGM 이 붙인 pod 라벨이 프로메테우스가 붙이는 pod(스크레이프 대상 = exporter 자신)와
#   충돌해서 ★exported_ 접두사가 붙는다. pod 로 물으면 dcgm-exporter 자신이 나온다.
#
# ⚠️DCGM 이 없는 환경(테스트·GPU 없는 클러스터)에서는 결과가 비어 온다 →
#   gpu_present=False 로 남는다. ★가짜 0 을 만들지 않는다.
_GPUSEL = f'exported_namespace="{NAMESPACE}"'
_Q_POD_GPU_UTIL = f"DCGM_FI_DEV_GPU_UTIL{{{_GPUSEL}}}"
_Q_POD_GPU_MEM_USED = f"DCGM_FI_DEV_FB_USED{{{_GPUSEL}}}"          # 단위 MiB
_Q_POD_GPU_MEM_TOTAL = f"DCGM_FI_DEV_FB_USED{{{_GPUSEL}}} + DCGM_FI_DEV_FB_FREE{{{_GPUSEL}}}"

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


def _node_label(nodename: str, ip: str, gpu_nodes: set[str]) -> str:
    """노드 카드 이름 — 운영자가 보고 무슨 서버인지 알 수 있게.

    ★그전에는 "노드 (10.0.2.128)" 이었다. IP 만으로는 ★무슨 일을 하는 서버인지 알 수 없고,
    쿠버네티스를 모르는 운영자에게 '노드'라는 말 자체가 뜻을 주지 못한다.
    """
    role = "GPU" if nodename in gpu_nodes else "일반"
    zone = _zone_of(ip)
    tail = f" · {zone}" if zone else ""
    return f"서비스 서버 · {role}{tail} ({ip})"


def apps_by_node(base_url: str) -> dict[str, list[str]]:
    """서버(노드) 이름 → 그 위에 올라가 있는 앱들(사람이 읽는 이름).

    ★서버 카드와 앱 카드가 따로 있으면 보는 사람이 둘을 머리로 이어야 한다.
    노드 카드에 이 목록을 적어 "이 서버에서 무엇이 도는가" 를 바로 보이게 한다.

    파드 이름은 "backend-api-677b65d757-vwvz2" 처럼 뒤에 무작위가 붙으므로
    POD_GROUPS 의 키로 앞을 맞춰 가른다. 순서는 POD_GROUPS 그대로 — 화면과 같게.
    """
    order = [d for _, d in POD_GROUPS]
    found: dict[str, set[str]] = {}
    for r in instant_query(_Q_POD_NODE, base_url=base_url):
        pod = r["labels"].get("pod", "")
        node = r["labels"].get("node", "")
        if not node:
            continue
        for key, disp in POD_GROUPS:
            if pod.startswith(f"{key}-"):
                found.setdefault(node, set()).add(disp)
                break
    return {n: [d for d in order if d in got] for n, got in found.items()}


def _node_snapshots(base_url: str) -> list[dict]:
    """노드별 스냅샷 — node-exporter가 보는 실제 하드웨어."""
    names = {
        r["labels"].get("instance", ""): r["labels"].get("nodename", "")
        for r in instant_query(_Q_NODE_NAME, base_url=base_url)
    }
    cpu = _by_label(instant_query(_Q_NODE_CPU, base_url=base_url), "instance")
    cores = _by_label(instant_query(_Q_NODE_CORES, base_url=base_url), "instance")
    load1 = _by_label(instant_query(_Q_NODE_LOAD1, base_url=base_url), "instance")
    # GPU 를 가진 노드 이름 집합. 질의가 비어 와도(지표 없음) 이름만 덜 친절해질 뿐 동작한다.
    gpu_nodes = {
        r["labels"].get("node", "")
        for r in instant_query(_Q_NODE_GPU_CAP, base_url=base_url)
        if float(r.get("value", 0) or 0) > 0
    }
    node_apps = apps_by_node(base_url)

    # ★노드별 GPU 실측 — 이름에 "GPU" 라고 써 놓고 카드에는 "GPU 없음" 이라고 하던 것을 메운다.
    #   DCGM 파드가 어느 노드에 있는지(kube_pod_info)로 이어 붙인다.
    dcgm_node = {
        r["labels"].get("pod", ""): r["labels"].get("node", "")
        for r in instant_query(_Q_DCGM_POD_NODE, base_url=base_url)
    }

    def _gpu_by_node(expr: str) -> dict[str, float]:
        """DCGM 지표를 노드별 합계로. Hostname(=DCGM 파드) → 노드로 바꿔 더한다."""
        acc: dict[str, float] = {}
        for r in instant_query(expr, base_url=base_url):
            node = dcgm_node.get(r["labels"].get("Hostname", ""), "")
            if node:
                acc[node] = acc.get(node, 0.0) + float(r.get("value", 0) or 0)
        return acc

    def _gpu_names_by_node() -> dict[str, str]:
        out: dict[str, str] = {}
        for r in instant_query(_Q_NODE_GPU_UTIL, base_url=base_url):
            node = dcgm_node.get(r["labels"].get("Hostname", ""), "")
            if node and node not in out:
                out[node] = r["labels"].get("modelName", "")
        return out

    n_gpu_util = _gpu_by_node(_Q_NODE_GPU_UTIL)
    n_gpu_used = _gpu_by_node(_Q_NODE_GPU_MEM_USED)
    n_gpu_free = _gpu_by_node(_Q_NODE_GPU_MEM_FREE)
    n_gpu_name = _gpu_names_by_node()
    # 한 노드에 GPU 가 여러 장이면 사용률은 평균, VRAM 은 합계(앱 카드와 같은 규약)
    n_gpu_cnt: dict[str, int] = {}
    for r in instant_query(_Q_NODE_GPU_UTIL, base_url=base_url):
        node = dcgm_node.get(r["labels"].get("Hostname", ""), "")
        if node:
            n_gpu_cnt[node] = n_gpu_cnt.get(node, 0) + 1

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
                # ★사람이 읽는 이름 — "무엇을 하는 서버인가"를 앞에 둔다.
                #   예: "서비스 서버 · GPU · 2-a (10.0.2.210)"
                #   IP 는 남긴다(같은 역할이 여러 대라 구분이 필요하다).
                "label": _node_label(nodename, instance.split(":")[0], gpu_nodes)[:_LABEL_MAX],
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
                # 이 서버 위에서 도는 앱들(표시 이름). 없으면 빈 목록 — 화면이 알아서 감춘다.
                "apps": node_apps.get(nodename, []),
                # ★이름에 "GPU" 라고 써 놓고 여기서 False 를 보내면 카드가 "GPU 없음" 이라고
                #   말한다 — 제목과 내용이 정면으로 어긋난다(0815 화면에서 확인). 실측을 넣는다.
                **(
                    {
                        "gpu_present": True,
                        "gpu_name": (n_gpu_name.get(nodename) or None) and n_gpu_name[nodename][:80],
                        "gpu_util_pct": round(n_gpu_util[nodename] / max(1, n_gpu_cnt.get(nodename, 1)), 1),
                        "gpu_mem_used_mb": int(n_gpu_used.get(nodename, 0.0)),
                        "gpu_mem_total_mb": int(
                            n_gpu_used.get(nodename, 0.0) + n_gpu_free.get(nodename, 0.0)
                        )
                        or None,
                    }
                    if nodename in n_gpu_util
                    else {"gpu_present": False}
                ),
            }
        )
    return out


def _gpu_of(
    pods: list[str],
    util: dict[str, float],
    used: dict[str, float],
    total: dict[str, float],
    names: dict[str, str],
) -> dict:
    """이 서비스의 파드들이 쓰는 GPU 를 하나로 묶는다.

    ★사용률은 평균, VRAM 은 합계다. 파드마다 ★다른 GPU 를 하나씩 잡기 때문에
    사용률을 더하면 200% 같은 값이 나오고, VRAM 은 더해야 "얼마나 남았나"가 맞다.
    ★GPU 를 안 쓰는 서비스는 gpu_present=False 로 둔다 — 0% 로 채우면 화면에서
    "GPU 가 한가하다"로 읽혀 GPU 서비스와 구별이 안 된다.
    """
    g = [p for p in pods if p in util]
    if not g:
        return {"gpu_present": False}
    tot = sum(total.get(p, 0.0) for p in g)
    return {
        "gpu_present": True,
        "gpu_name": (names.get(g[0]) or None) and names[g[0]][:80],
        "gpu_util_pct": round(sum(util.get(p, 0.0) for p in g) / len(g), 1),
        "gpu_mem_used_mb": int(sum(used.get(p, 0.0) for p in g)),
        "gpu_mem_total_mb": int(tot) if tot else None,
    }


def _service_snapshots(base_url: str, cluster_cores: float) -> list[dict]:
    """서비스별 스냅샷 — 같은 배포의 파드들을 하나로 묶은 합계.

    ★파드를 각각 카드로 내지 않는 이유: 파드 이름에 해시가 들어 있어 배포할 때마다 바뀐다.
    그대로 두면 server_metrics에 죽은 행이 배포 횟수만큼 쌓인다. 파드 단위로 보고 싶을 때는
    그라파나가 있다 — 이 화면은 "서비스가 건강한가"를 본다.
    """
    cpu = _by_label(instant_query(_Q_POD_CPU, base_url=base_url), "pod")
    mem = _by_label(instant_query(_Q_POD_MEM, base_url=base_url), "pod")
    limit = _by_label(instant_query(_Q_POD_MEM_LIMIT, base_url=base_url), "pod")

    # ★GPU 는 exported_pod 로 묶는다(위 상수 주석 참조). DCGM 이 없으면 전부 빈 dict 가 된다.
    gpu_rows = instant_query(_Q_POD_GPU_UTIL, base_url=base_url)
    gpu_util = _by_label(gpu_rows, "exported_pod")
    gpu_used = _by_label(instant_query(_Q_POD_GPU_MEM_USED, base_url=base_url), "exported_pod")
    gpu_total = _by_label(instant_query(_Q_POD_GPU_MEM_TOTAL, base_url=base_url), "exported_pod")
    # 모델 이름(Tesla T4 등) — 같은 파드가 여러 GPU 를 쓰면 첫 것만 쓴다(우리는 파드당 1개).
    gpu_names = {
        r["labels"].get("exported_pod", ""): r["labels"].get("modelName", "")
        for r in gpu_rows
        if r["labels"].get("exported_pod")
    }

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
                # ★"(파드 2)" 는 쿠버네티스를 아는 사람에게만 뜻이 있고, 몇 벌 떠 있는지는
                #   화면 부제가 "2벌 실행 중" 으로 이미 보여 준다(cpu_cores). 제목에서는 뺀다.
                "label": label,
                # ★"catchap 네임스페이스" 는 쿠버네티스를 아는 사람에게만 뜻이 있다.
                #   운영자에게 필요한 정보는 "이게 어디서 도는가" 이므로 그 말로 쓴다.
                "host": "쿠버네티스 클러스터 안",
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
                **_gpu_of(pods, gpu_util, gpu_used, gpu_total, gpu_names),
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
