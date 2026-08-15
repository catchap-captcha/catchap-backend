#!/usr/bin/env python3
"""CatChap 서버 메트릭 에이전트 — 각 VM(DB·GPU STT·프론트)에서 실행해 자기 자원을 백엔드로 push.

배포(배포 승인 후):
    pip install psutil requests
    METRICS_URL=https://api.catchap5.com/api/v1/internal/metrics \
    METRICS_TOKEN=<백엔드 .env METRICS_INGEST_TOKEN> \
    SERVER_KEY=gpu-stt SERVER_LABEL="GPU STT 워커" \
    python3 metrics_agent.py            # 1회 전송
    python3 metrics_agent.py --loop 30  # 30초마다 반복(systemd/cron 대안)

GPU 서버는 nvidia-smi가 있으면 자동으로 GPU util·VRAM도 함께 보낸다. 백엔드 서버는 이 에이전트가
필요 없다 — 백엔드는 요청 시 자기 자신을 psutil로 즉시 측정한다(monitoring.ops_monitoring).

의존: 이 스크립트는 백엔드 앱과 독립 실행되므로 host_metrics를 복제하지 않고 psutil을 직접 쓴다
(VM에 앱 코드가 없어도 psutil+requests만으로 돈다).
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime


def gpu() -> dict:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {"gpu_present": False}
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        name, util, used, total = [p.strip() for p in out.stdout.strip().splitlines()[0].split(",")]
        return {"gpu_present": True, "gpu_name": name[:80], "gpu_util_pct": float(util),
                "gpu_mem_used_mb": int(float(used)), "gpu_mem_total_mb": int(float(total))}
    except Exception:
        return {"gpu_present": False}


def snapshot(server_key: str, label: str) -> dict:
    import psutil

    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    try:
        load1 = psutil.getloadavg()[0]
    except (AttributeError, OSError):
        load1 = None
    return {
        "server_key": server_key, "label": label, "host": socket.gethostname(),
        "cpu_pct": float(psutil.cpu_percent(interval=0.3)), "cpu_cores": int(psutil.cpu_count() or 0),
        "load1": load1, "mem_pct": float(vm.percent), "mem_used_mb": int(vm.used / 1048576),
        "mem_total_mb": int(vm.total / 1048576), "disk_pct": float(disk.percent),
        "disk_used_gb": round(disk.used / 1073741824, 1), "disk_total_gb": round(disk.total / 1073741824, 1),
        # ★astimezone() 으로 tz 를 붙인다 — 이 서버가 UTC 여도 받는 쪽이 KST 로 맞춘다.
        #   (0815: 새로 만든 VM 4대가 Etc/UTC 라 9시간 뒤처진 시각을 보내 "수집 중단"으로 보였다)
        "collected_at": datetime.now().astimezone().isoformat(), **gpu(),
    }


def send(url: str, token: str, snap: dict) -> None:
    import requests

    r = requests.post(url, json=snap, headers={"X-Metrics-Token": token}, timeout=10)
    r.raise_for_status()
    print(f"[{datetime.now():%H:%M:%S}] sent {snap['server_key']} cpu={snap['cpu_pct']}% "
          f"mem={snap['mem_pct']}%" + (f" gpu={snap.get('gpu_util_pct')}%" if snap.get('gpu_present') else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="초 간격 반복(0=1회)")
    args = ap.parse_args()
    url = os.environ.get("METRICS_URL")
    token = os.environ.get("METRICS_TOKEN")
    key = os.environ.get("SERVER_KEY")
    label = os.environ.get("SERVER_LABEL", key or "server")
    if not (url and token and key):
        print("METRICS_URL, METRICS_TOKEN, SERVER_KEY 환경변수가 필요합니다.", file=sys.stderr)
        return 2
    while True:
        try:
            send(url, token, snapshot(key, label))
        except Exception as e:  # 전송 실패는 로그만 — 다음 주기에 재시도(에이전트는 죽지 않는다)
            print(f"send failed: {e}", file=sys.stderr)
        if args.loop <= 0:
            return 0
        time.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
