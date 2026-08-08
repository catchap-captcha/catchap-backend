# CatChap 백엔드 (FastAPI) — 프로덕션 이미지
# 빌드 컨텍스트 = 이 워크트리(worktree-widget-orig 브랜치): forest_captcha·app/static 자산·최신 마이그레이션 포함.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Seoul

WORKDIR /app

# 타임존을 한국(KST)으로 고정 — 앱이 datetime.now()(naive 로컬)로 created_at·감사로그·
# '오늘/이번 주' 집계를 잡는데, 컨테이너 기본(UTC)이면 9시간 어긋난다. tzdata 설치 후 KST 고정.
# ffmpeg — STT 워커로 보내기 전 강의 영상에서 오디오만 뽑는 데 쓴다(stt_client._extract_audio).
# 영상을 그대로 보내면 워커 디스크·전송이 통째로 낭비된다(faster-whisper는 오디오만 쓴다).
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ffmpeg \
    && ln -sf /usr/share/zoneinfo/Asia/Seoul /etc/localtime \
    && echo "Asia/Seoul" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 1) 의존성 먼저 설치(레이어 캐시) — requirements 변경 시에만 재설치
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 2) 앱 코드 + 정적 자산(오디오 m4a·국기 svg·문항 이미지·위젯 js·숲캡차) + 마이그레이션
#    captcha_api.py가 Path(__file__).parents[3]/"static"로 자산을 읽으므로 app/static 통째로 필요.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# 설정(.env)은 이미지에 굽지 않는다 — 런타임에 환경변수(compose env_file)로 주입.
# pydantic-settings가 OS 환경변수를 우선 읽으므로 .env 없이도 동작한다.

EXPOSE 8000

# 컨테이너 자체 헬스체크 — /health(루트) 200 확인
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).status==200 else 1)"

# 프로덕션 구동 — 워커 2개(각자 DB 엔진). 스키마 마이그레이션은 배포 절차에서 별도 실행(DEPLOY.md).
# ★--proxy-headers — 로드밸런서/인그레스 뒤에서 ★사용자 IP 를 그대로 보게 한다.
#   이게 없으면 request.client.host 가 ★앞단(노드·LB)의 IP 가 되고,
#   IP 기준 횟수 제한(로그인·재설정·캡차·업로드·소셜콜백 등 ★7개 모듈)이
#   사용자별이 아니라 ★전체 한 덩어리로 돌아간다.
#   실제로 그랬다 — login_throttle 에 pwresetip:192.168.57.1(노드 IP) 하나로 쌓이고 있었다.
#
# ★--forwarded-allow-ips 는 반드시 좁힌다. 이 헤더는 ★누구나 보낼 수 있으므로
#   신뢰할 앞단만 지정해야 한다. 안 그러면 IP 를 위조해 횟수 제한을 우회할 수 있다.
#     10.0.0.0/16     VPC (LB·노드)
#     192.168.0.0/16  파드 네트워크 (인그레스가 hostNetwork 라 게이트웨이로 들어온다)
#   실측한 앞단 주소 — 192.168.57.1 · 10.0.6.202
#   ⚠️`*` 로 열지 말 것. uvicorn 0.52 의 _TrustedHosts 는 CIDR 를 지원한다(확인함).
#
# 전제 — ingress-nginx 에 use-forwarded-headers=true · proxy-real-ip-cidr=10.0.0.0/16
#        그리고 LB 리스너에 X-Forwarded-For 삽입이 켜져 있어야 한다(2-a·2-b 둘 다).
#
# ★스킴은 문제되지 않는다 — 이 앱은 요청에서 절대 URL 을 만들지 않는다.
#   리다이렉트는 전부 설정값(FRONTEND_URL·SOCIAL_REDIRECT_URIS·PAYMENT_*)에서 온다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "10.0.0.0/16,192.168.0.0/16"]
