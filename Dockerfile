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
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
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
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
