# 카카오클라우드 인프라 요청서 — CatChap (시청검증 인강) · 제출본

현재 VM 5대: 백엔드/프론트/DB/AI 각 2 vCPU·8GB·96GB, GPU STT 워커 16 vCPU·63GB·96GB·Tesla T4.
원칙: 기능에 연결된 서비스만 요청(용도 없는 순수 VM 제외).

---

**1. Load Balancer**
- 이유: 단일 서버 직결이라 배포·장애 시 접속 끊김 → 무중단·수평 확장.
- 사양: L7(Application) **1대**, HTTPS/SSL 종료, 헬스체크

**2. Kubernetes Engine**
- 이유: 수동 배포 → 자동 배포·오토스케일·자가치유·무중단.
- 사양:
  - 컨트롤 플레인 **1식**(관리형)
  - 일반 워커노드 **3대** — 각 **4 vCPU / 16GB RAM / 100GB SSD**
  - GPU 워커노드 **1대** — **Tesla T4 1장, 16 vCPU / 64GB RAM / 100GB SSD**

**3. Container Registry**
- 이유: K8s 배포용 컨테이너 이미지 보관·버전 관리.
- 사양: **100GB**

**4. 관리형 MySQL**
- 이유: 자가운영 DB의 백업·패치·이중화 자동화, 장애 자동 승계.
- 사양: **1식, 4 vCPU / 16GB RAM / 200GB SSD**, **HA(Standby 1대)**, **자동백업 14일 보존**

**5. Object Storage**
- 이유: 강의 영상이 VM 디스크(96GB) 한계 근접 → 분리 저장.
- 사양: 표준 스토리지 **1TB**, 비공개 + 서명 URL

**6. MemStore (Redis)**
- 이유: 컨테이너 다중화 시 세션·캐시 외부 공유.
- 사양: **1식, 2GB, 2노드(Primary 1 + Replica 1)**

**7. NAT Gateway**
- 이유: 공인 IP 노출 최소화(사설망 아웃바운드).
- 사양: **1대**, 사설 서브넷 10.0.1.0/24 아웃바운드

**8. Secrets Manager + KMS**
- 이유: 평문 시크릿 암호화·자동 회전·감사.
- 사양: **KMS 키 2개, 시크릿 50개**, 자동 회전

**9. DDoS Defender**
- 이유: 공개 엔드포인트 대량 트래픽 공격 방어.
- 사양: **1식**(LB·프론트 공개 엔드포인트 대상)

**10. IDS (침입 탐지)**
- 이유: 비정상 접근·공격 시도 탐지.
- 사양: **1식**

**11. Monitoring / Managed Prometheus / Alert Center**
- 이유: 인프라 지표 실시간 확인 + 임계 경보.
- 사양: **1식**(관리형)

**12. Certificate Manager**
- 이유: HTTPS SSL 인증서 자동 발급·갱신.
- 사양: 인증서 **1개** (catchap5.com, `*.catchap5.com` 와일드카드)

**13. DNS**
- 이유: catchap5.com 도메인 레코드 관리.
- 사양: 존 **1개** (catchap5.com)
