# CatChap 쿠버네티스 매니페스트

카카오클라우드 `kc-sfacspace05` · 클러스터 `catchap-cluster-prod` · VPC `Team1-VPC`

**★아직 적용하지 않았습니다.** 적용에는 `cluster-admin` 권한이 필요하고(강사님 대기),
이미지도 아직 빌드·push 하지 않았습니다.

---

## 왜 매니페스트인가

지금은 서버에 들어가서 `docker compose up` 을 칩니다. 쿠버네티스는 **명령을 치는 게 아니라
원하는 상태를 적어서 주면** 알아서 그 상태로 맞춥니다. 파드가 죽으면 다시 띄우고,
노드가 빠지면 다른 노드로 옮깁니다.

```
docker-compose.yml 한 파일   →   Deployment + Service + Ingress + ConfigMap + Secret
services.api.image           →   Deployment(image)
services.api.expose          →   Service(port)
env_file: .env.production    →   ConfigMap(평문) + Secret(비밀)
Caddy 리버스 프록시           →   Ingress (+ 로드밸런서가 TLS 종료)
volumes: lecture_media       →   ★없어짐 — 미디어를 Object Storage 로 뺐다
```

---

## 파일 순서 (번호대로 적용)

```
00-namespace.yaml          네임스페이스 catchap
10-configmap-backend.yaml  백엔드 평문 설정 20건
11-configmap-ai.yaml       AI 평문 설정 8건
20-secret-*.example.yaml   ★비밀값 템플릿 — 실제 값은 여기 쓰지 않는다(아래 참조)
30-deployment-backend.yaml
31-deployment-frontend.yaml
32-deployment-ai.yaml
40-services.yaml           Service 3개
50-ingress.yaml            호스트명 분기
```

---

## ★적용 전에 반드시 할 것

### 1. 이미지 빌드·push (아직 안 됨)

```
kc-sfacspace05.kr-central-2.kcr.dev/catchap-backend-repo/backend:<태그>
kc-sfacspace05.kr-central-2.kcr.dev/catchap-front-repo/frontend:<태그>
kc-sfacspace05.kr-central-2.kcr.dev/catchap-batch-repo/ai:<태그>
```

★**push 에는 `프로젝트 멤버` 이상 권한이 필요합니다**(실측 확인 — 프로젝트 리더로는 pull 만).
★프론트는 `VITE_*` 가 **빌드 타임에 번들에 박히므로**, API 주소가 바뀌면 재빌드해야 합니다.

### 2. imagePullSecret 만들기

CR 이 사설이라 인증 없이는 파드가 이미지를 못 받습니다.

```bash
kubectl -n catchap create secret docker-registry cr-pull \
  --docker-server=kc-sfacspace05.kr-central-2.kcr.dev \
  --docker-username='<IAM 액세스 키 ID>' \
  --docker-password='<IAM 보안 액세스 키>'
```

★**IAM 액세스 키**입니다. S3 액세스 키로는 `401` 이 납니다(실측).

### 3. Secret 만들기 (파일에 값을 적지 않는다)

`20-secret-*.example.yaml` 은 **키 이름만** 적힌 템플릿입니다. 실제 값은 이렇게 넣습니다.

```bash
kubectl -n catchap create secret generic catchap-backend-secret \
  --from-literal=DATABASE_URL='mysql+pymysql://…' \
  --from-literal=JWT_SECRET_KEY='…' \
  ...
```

★비밀값을 git 에 올리지 않기 위해서입니다. 나중에 Secrets Manager 연동으로 바꿉니다.

### 4. 인그레스 컨트롤러 배포 (`cluster-admin` 필요)

카카오클라우드 문서: *"현재 Kubernetes Engine 서비스에서는 **Admission Webhook을 지원하지
않습니다.** Admission Webhook 설정된 서비스를 배포하려면 **`hostNetwork: true` 설정이
필요합니다.**"*

→ ingress-nginx 를 `hostNetwork: true` 로 배포하고, **워커1에 고정**합니다(아래 참조).

---

## ★로드밸런서가 단일 AZ — 인그레스를 워커1에 고정하는 이유

```
LoadBalancer01   210.109.55.233   kr-central-2-a   ← 우리 SAN 인증서 붙어 있음
대상 그룹        catchap-tg-prod → 10.0.2.128:80   (워커1)

워커1  10.0.2.128  private_sn1 (2-a)   ✅ LB 대상 가능
워커2  10.0.6.202  private_sn6 (2-b)   ❌ 다른 AZ라 대상 추가 불가
```

문서: *"로드 밸런서와 동일한 VPC 및 **가용 영역**에 있는 인스턴스만 대상 그룹에 추가할 수 있습니다."*

**LB 가 잘못 만들어진 게 아닙니다** — 카카오클라우드는 LB 노드가 원래 단일 AZ이고,
다중 AZ 는 **`고가용성 그룹`으로 노드를 묶어서** 만듭니다. 이전이 끝난 뒤 별도로 합니다.

그래서 **인그레스는 워커1에만** 띄웁니다(`nodeSelector`). **앱 파드는 두 워커에 흩어집니다** —
Service 가 노드를 넘어 분배하므로 파드·워커2 장애는 견딥니다. 남는 약점은 입구가 하나라는 점입니다.

---

## 트래픽 경로

```
인터넷 → LB(210.109.55.233) TLS 종료 → 워커1:80 인그레스
        → 호스트명 분기
            catchap5.com · www.catchap5.com  → catchap-frontend:80
            api.catchap5.com                 → catchap-backend:8000
        → Service 가 두 워커의 파드로 분배

앱 → 관리형 MySQL(3306) · Valkey(6379) · Object Storage · STT 워커(10.0.5.57:8100)
```

★**apex → www 리다이렉트**를 인그레스에 넣었습니다. 지금 Caddy 가 하고 있어서
(`catchap5.com` → 301) 빠뜨리면 apex 로 들어온 사람이 404 를 봅니다.

---

## 컷오버

```
1  이미지 빌드·push
2  Secret·ConfigMap 적용
3  인그레스 컨트롤러 배포 → LB 대상이 Healthy 로 바뀌는지 확인
4  매니페스트 적용
5  ★DNS 바꾸기 전에 LB IP 로 직접 접속 확인
     curl -H "Host: catchap5.com" https://210.109.55.233/ -k
6  DNS TTL 86400 → 300 (가비아 · 컷오버 며칠 전)
7  A 레코드 변경  catchap5.com · www · api → 210.109.55.233
8  안정 확인 후 TTL 원복 · 옛 VPC 정리
```
