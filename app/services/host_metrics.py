"""호스트 자원 측정 — psutil로 CPU/메모리/디스크, nvidia-smi로 GPU(있으면).

백엔드 자신을 측정할 때(요청 시 self-collect)와, 각 VM의 에이전트(scripts/metrics_agent.py)가
같은 형식으로 밀어넣을 때 공용으로 쓰는 순수 측정 함수. 반환은 서버 저장 스키마와 1:1 dict.
GPU가 없거나 nvidia-smi가 없으면 gpu_present=False로 정직하게 돌려준다(가짜 0 안 만든다).
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime


def _gpu_snapshot() -> dict:
    """nvidia-smi가 있으면 첫 GPU의 util·VRAM을 읽는다. 없으면 gpu_present=False."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {"gpu_present": False}
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        line = (out.stdout or "").strip().splitlines()[0]
        name, util, used, total = [p.strip() for p in line.split(",")]
        return {
            "gpu_present": True,
            "gpu_name": name[:80],
            "gpu_util_pct": float(util),
            "gpu_mem_used_mb": int(float(used)),
            "gpu_mem_total_mb": int(float(total)),
        }
    except Exception:
        # 있지만 읽기 실패 — 정직하게 없음 처리(가짜 수치보다 낫다)
        return {"gpu_present": False}


def collect(server_key: str, label: str, host: str | None = None) -> dict:
    """이 호스트의 현재 자원 스냅샷을 측정해 server_metrics upsert 형식 dict로 반환."""
    import psutil

    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    try:
        load1 = psutil.getloadavg()[0]  # 윈도우 미지원 → 예외 시 None
    except (AttributeError, OSError):
        load1 = None
    snap = {
        "server_key": server_key,
        "label": label,
        "host": host,
        "cpu_pct": float(psutil.cpu_percent(interval=0.2)),
        "cpu_cores": int(psutil.cpu_count() or 0),
        "load1": load1,
        "mem_pct": float(vm.percent),
        "mem_used_mb": int(vm.used / 1024 / 1024),
        "mem_total_mb": int(vm.total / 1024 / 1024),
        "disk_pct": float(disk.percent),
        "disk_used_gb": round(disk.used / 1024 / 1024 / 1024, 1),
        "disk_total_gb": round(disk.total / 1024 / 1024 / 1024, 1),
        "collected_at": datetime.now(),
        **_gpu_snapshot(),
    }
    return snap
