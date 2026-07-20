# CatChap STT 워커 (faster-whisper / GPU)

강의 오디오를 타임스탬프 자막으로 전사하는 **자체 호스팅 STT 서비스**. OpenAI Whisper
API(유료·25MB 한계·오디오 외부 전송)를 대체 — Tesla T4 GPU에서 무료로, 대용량도 전사한다.

## 왜 만들었나
- 우리에겐 놀고 있는 **GPU 서버(Tesla T4, 16GB)**가 있다(61.109.239.231).
- 로컬 스파이크(`spike-whisper/`)에서 faster-whisper large-v3가 잘 되는 걸 검증했다.
- OpenAI는 분당 $0.006 유료 + 25MB 한계 + 오디오를 외부로 보냄. 자체 호스팅은 **과금 0 ·
  대용량 OK · 오디오가 사내에만** 머문다.

## 구조
```
앱 백엔드 ──(영상/오디오 POST, X-Worker-Token)──▶ 이 워커(:8100) ──▶ T4 GPU 전사
                                              ◀──({segments:[{start,end,text}]})──
```
워커는 오직 '오디오→자막' 한 가지만 한다(사용자·DB·시험을 모름). 반환 형태는 기존
`app/clients/stt_client.py`의 OpenAI 경로와 동일해, 백엔드는 소스에 상관없이 같은 코드로 받는다.

## 엔드포인트
- `GET /health` — 기동 확인(모델 미로드).
- `POST /transcribe` (multipart `file`, query `language=ko`, header `X-Worker-Token`) →
  `{ "segments": [{start, end, text}], "duration", "language" }`. 무음/전사 실패는 422.

## 빌드·실행 (GPU 서버, A안=Docker)
전제: 호스트에 NVIDIA 드라이버(있음) + Docker + **nvidia-container-toolkit** 설치.
```bash
# 1) nvidia-container-toolkit (GPU를 컨테이너에 연결) — 호스트에 없으면 1회만
#    (설치법: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)

# 2) 이미지 빌드
docker build -t catchap-stt-worker .

# 3) 실행 — T4 연결 + 공유 토큰 + 모델 캐시 볼륨(재시작 시 재다운로드 방지)
docker run -d --name stt-worker --gpus all \
  -p 127.0.0.1:8100:8100 \
  -e STT_WORKER_TOKEN="<백엔드와 공유할 시크릿>" \
  -v /opt/whisper-models:/root/.cache/huggingface \
  --restart unless-stopped \
  catchap-stt-worker

# 4) 헬스체크
curl -s localhost:8100/health
```
CPU만 있는 곳에서 테스트하려면 `-e STT_DEVICE=cpu -e STT_COMPUTE=int8 -e STT_MODEL=tiny`.

## 백엔드 연결
앱 `.env`(또는 콘솔 설정)에:
```
STT_WORKER_URL=http://<GPU서버 사설IP>:8100
STT_WORKER_TOKEN=<위와 동일 시크릿>
```
설정되면 백엔드는 OpenAI 대신 이 워커로 전사한다(강사 제공 자막 우선·전사 캐시 로직은 그대로).
`STT_WORKER_URL`이 비면 기존 OpenAI 경로로 폴백(하위호환).

## 보안
- 워커는 **백엔드에서만** 접근(공유 토큰 + 방화벽/보안그룹으로 백엔드 IP만 허용). 외부 노출 금지.
- 오디오는 임시 파일로 받고 전사 후 즉시 삭제(원본 미보관).
