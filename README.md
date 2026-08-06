# catchap-backend

CatChap 의 **백엔드 API** 입니다. 강의·문항·결제·캡차 API·운영 콘솔을 담당합니다.

```
FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic 2 · MySQL
엔드포인트 18개 · 마이그레이션 73개 · ★시험 436개
```

---

## 빠른 시작

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                               # 값을 채웁니다
alembic upgrade head
uvicorn app.main:app --reload
```

⚠️**`.env` 는 커밋하지 않습니다.** 실제 값은 카카오클라우드 Secrets Manager 에 있습니다.

⚠️**로컬 `.env` 가 프로덕션 DB 를 가리키지 않는지 확인하세요.** 읽기만 해도
운영 데이터를 건드릴 위험이 있습니다.

## 폴더

```
app/api/v1/endpoints/   API 18개
   auth · students · lectures · course_exam · payments · notifications · settings
   captcha_api · drag_captcha · forest_captcha · widget    캡차
   ops · monitoring · alerts · health · institutions · misc  운영·관측

app/models/         DB 테이블 정의 (SQLAlchemy)
app/schemas/        요청·응답 형태 (Pydantic)
app/services/       업무 규칙 — ★로직은 여기에 둡니다
app/repositories/   DB 접근
app/clients/        바깥 서비스 호출 (STT 워커·LLM·결제)
app/core/           설정·인증·공통
alembic/versions/   마이그레이션 73개
tests/              시험 43개 파일 · ★436개
```

## 시험

```bash
pytest -q                      # 전체 436개 · 5~7분
pytest tests/test_payments.py  # 하나만
```

★**CI 가 push 마다 전체를 돌립니다**(`.github/workflows/ci.yml`). 빨간불이면 병합할 수 없습니다.

★**시험은 실제 동작을 봅니다.** 타입체크만 통과했다고 되는 게 아니라, 바뀐 동작을
구동해서 확인합니다. 새 기능에는 시험을 같이 올려 주세요.

## 마이그레이션

```bash
alembic revision -m "무엇을 왜"      # 새로 만들 때
alembic upgrade head                 # 적용
alembic downgrade -1                 # 되돌리기
```

⚠️**`--autogenerate` 를 그대로 믿지 마세요.** `ai_*` 테이블은 alembic 밖에서 관리되어
**`DROP TABLE` 을 만들어 냅니다.** 만든 파일을 반드시 열어 보고 커밋하세요.

## 배포

```
이미지 태그 = ★커밋 해시    catchap-backend:ae8203c
             → git show ae8203c 로 그 코드를 바로 볼 수 있습니다
매니페스트   ★catchap-infra 저장소의 k8s/backend/
```

`main` 에 병합되면 이미지가 만들어지고 쿠버네티스에 반영됩니다.
자세한 것은 `DEPLOY.md` 를 보세요.

## 작업 방법

`main` 에 직접 push 하지 않습니다. 브랜치를 따서 PR 로 올립니다.

```bash
git switch main && git pull
git switch -c fix/<요약>
git commit -m "fix(payments): 환불 금액을 정수 연산으로"
git push -u origin HEAD
gh pr create --fill && gh pr merge --auto --squash
```

```
feature/<요약>  새 기능      fix/<요약>    버그
hotfix/<요약>   급한 수정    chore/<요약>  설정·문서
```

★**사람 이름 브랜치는 만들지 않습니다.** 무슨 작업인지 알 수 없고, 그 사람이 없으면
아무도 이어받지 못합니다. 옛 브랜치는 `catchap-legacy` 에 보관돼 있습니다.

★리뷰 승인은 **0명**입니다. 본인이 열고 본인이 병합할 수 있습니다. **CI 가 유일한 관문**입니다.

## 함께 보는 저장소

```
catchap-frontend      화면
catchap-captcha       캡차 (교육형 위젯·공개 캡차 API)
catchap-behavior-ai   행동 기반 봇 판별 AI
catchap-infra         쿠버네티스 매니페스트·인프라 문서
catchap-legacy        지난 작업 보관 (읽기용)
```
