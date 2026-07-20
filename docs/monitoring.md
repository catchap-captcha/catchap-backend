# 서버 모니터링 — 자원·GPU·LLM + 임계 경보

운영 콘솔 **시스템 → 모니터링**(`/ops/monitoring`, 운영자 전용)에서 각 VM의 CPU/메모리/디스크/GPU와
LLM API 사용량·추정 비용, 그리고 자원 추이 그래프·임계 경보를 본다.

## 왜 이렇게 설계했나 (팀 학습용)
- **푸시 기반(push) 최신 1행/서버**: 각 서버가 자기 지표를 주기적으로 백엔드로 밀어넣고(`POST
  /internal/metrics`), 대시보드는 DB에서 **읽기만** 한다. 화면을 자주·여럿이 봐도 측정 부하가 안
  늘어 확장성이 좋다(pull 방식 node_exporter의 대안 — CloudWatch agent·Telegraf와 같은 결).
- **백엔드는 self-collect**: 대시보드 호출 때마다 psutil로 자기 자신을 즉시 측정(에이전트 불요).
  다른 서버(DB·GPU STT·프론트)만 에이전트가 필요하다.
- **시계열 표본은 단기 보존(6h)**: 현황판+단기 추이가 목적. 장기 보관은 별도 TSDB로 확장.
- 공부 키워드: push metrics, psutil, nvidia-smi, spaced retention, threshold alerting.

## 구성 요소
- 모델: `ServerMetric`(최신 1행/서버, upsert) · `ServerMetricSample`(추이, append-only, 6h 보존).
- 서비스: `app/services/host_metrics.py`(psutil + nvidia-smi).
- 엔드포인트: `POST /internal/metrics`(에이전트 인제스트) · `GET /ops/monitoring`(대시보드).
- 에이전트: `scripts/metrics_agent.py`(각 VM에서 실행).
- 임계(경보) 기준: `monitoring.CRIT` = CPU 90·메모리 85·디스크 90·GPU 90·VRAM 90(%) + 수집 중단(stale>120s).
  서버에 둔 이유: 화면 강조뿐 아니라 향후 알림(웹훅·메일) 훅이 같은 기준을 재사용하게.

## 에이전트 배포 (각 VM — 배포 승인 후)
백엔드 서버는 self-collect라 **불필요**. DB·GPU STT·프론트 VM에만 배포한다.

```bash
# 1) 의존 설치 (VM에 앱 코드 없어도 됨 — 이 둘만 있으면 동작)
pip3 install psutil requests

# 2) 스크립트 배치 (예: /opt/catchap/metrics_agent.py)
sudo mkdir -p /opt/catchap
sudo cp metrics_agent.py /opt/catchap/

# 3) 백엔드 .env(.production)에 인제스트 토큰 설정 후 값을 공유
#    METRICS_INGEST_TOKEN=<길고 무작위한 시크릿>   (비면 인제스트 403 — 배포 시 필수)

# 4) 1회 테스트
METRICS_URL=https://api.catchap5.com/api/v1/internal/metrics \
METRICS_TOKEN=<위 시크릿> SERVER_KEY=gpu-stt SERVER_LABEL="GPU STT 워커" \
python3 /opt/catchap/metrics_agent.py     # 성공 시 'sent gpu-stt ...' 출력
```

SERVER_KEY 규약(대시보드 EXPECTED_SERVERS와 맞춘다): `db` · `gpu-stt` · `frontend`
(원하면 더 추가 — 대시보드가 자동 노출). GPU 서버는 `nvidia-smi`가 있으면 GPU util·VRAM도 자동 전송.

### systemd 유닛 (30초 주기 상주)
`/etc/systemd/system/catchap-metrics.service`:
```ini
[Unit]
Description=CatChap metrics agent
After=network-online.target

[Service]
Environment=METRICS_URL=https://api.catchap5.com/api/v1/internal/metrics
Environment=METRICS_TOKEN=<시크릿>
Environment=SERVER_KEY=gpu-stt
Environment=SERVER_LABEL=GPU STT 워커
ExecStart=/usr/bin/python3 /opt/catchap/metrics_agent.py --loop 30
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now catchap-metrics
sudo systemctl status catchap-metrics    # active(running) 확인
```
에이전트가 멈추면 대시보드에 그 서버가 **"오래됨(수집 중단?)"** 경보로 뜬다(가짜로 최신인 척 안 함).

## 실시간성·부하
- 화면은 **10초 폴링**(웹소켓 스트림 아님). 백엔드는 매 호출 실측(≈실시간), 다른 서버는 에이전트
  push 주기만큼 신선. "N초 전"·"오래됨" 배지로 신선도를 정직하게 표시.
- 부하: 운영자 소수·10초 간격이라 무시할 수준. 더 빠르게 하려면 (1) 폴링 간격↓ (2) self-collect의
  `cpu_percent`를 논블로킹으로 + 캐싱 (3) 백엔드도 에이전트 push로 돌려 측정 부하 0에 수렴.

## 남은 것 (백로그)
- 실제 알림 발송(임계 초과 시 웹훅/메일 — 기준은 `CRIT` 재사용).
- 추이 장기 보관(TSDB)·이상탐지.
