# 운영자 AI 모델 선택 (#26)

> 문항 **생성**과 자기 **검증**에 실제로 호출하는 LLM 모델을, 운영자가 콘솔에서 고른다.
> 관련: [lecture-question-pipeline.md](lecture-question-pipeline.md)(생성·검증 파이프라인),
> [product-direction.md](product-direction.md)(AI 키·배포 원칙).

## 무엇인가

운영 콘솔 **설정** 페이지의 "AI 모델 선택" 섹션. 운영자가:

- 모델을 등록한다(회사·표시 이름·모델 ID·입출력 단가).
- **2슬롯**에 배정한다 — `generate`(문항 생성) / `verify`(자기검증).
- **On/Off**로 사용 여부를 토글하고, **삭제**할 수 있다.
- **자동 스왑**을 켜면 슬롯 모델이 꺼졌거나 호출에 실패할 때 다른 켜진 모델로 대체한다.
- 모델별 **누적 토큰·추정 비용**을 본다.

키(Anthropic·OpenAI)는 같은 페이지 아래에 그대로 둔다("모델 먼저, 키는 맨 나중").

## 왜 이렇게 했나 (설계 결정)

### 왜 "2슬롯 = 생성/검증"인가
파이프라인에는 LLM 호출이 정확히 두 군데다 — 자막에서 문항을 **만드는** 호출과, 만든
문항을 봇으로 풀어 봇저항을 **판정하는**(self-verification) 호출. 그래서 슬롯을 이 두
용도에 1:1로 맞췄다. 실무에서 유용한 조합이 자연스럽게 나온다: **생성=강한 모델**(품질),
**검증=저렴/다른 모델**(비용 절감 + 생성 모델의 습관에 안 물드는 독립 판정).
사용자와 확인한 결정(2026-07-19): "생성 슬롯 + 검증 슬롯"(대안이던 "주/예비 failover"는
용도 구분이 없어 탈락).

### 왜 표시용 카탈로그(`ModelVersion`, `/ops/ai-models`)와 분리했나
기존 `ModelVersion`은 **기관 콘솔에 '보여주기'만** 하는 카탈로그다(실제 호출과 무관).
여기에 슬롯·토큰·On/Off를 얹으면 두 개념이 섞여, 카탈로그를 바꾸면 실제 호출이 바뀌는
사고가 난다. 그래서 새 테이블 `ai_model_configs` + 새 경로 `/ops/ai-runtime`로 완전히
분리했다. **카탈로그 = 표시, 런타임 = 실제 호출.** 둘을 섞지 말 것.

### 왜 슬롯 포인터를 모델 컬럼이 아니라 `system_settings`에 뒀나
슬롯을 모델 행의 `slot` 컬럼으로 두면 **한 모델이 한 슬롯만** 가질 수 있어, "생성·검증을
같은 모델로"라는 흔하고 정당한 선택을 막는다. 그래서 슬롯을 `ai_slot_generate`·
`ai_slot_verify`(모델 id를 가리키는 포인터) + `ai_auto_swap`로 settings에 뒀다. 같은
모델을 두 슬롯에 함께 배정할 수 있다. (settings 저장은 기존 Fernet 창구를 재사용 — 비밀은
아니지만 배선을 늘리지 않으려는 선택. 값은 모델 id/불리언이라 암호화돼도 무해하다.)

### 왜 자동 스왑을 넣었나 / 어떻게 판단하나
운영자가 지정한 슬롯 모델이 점검·중단·rate limit(429)·overloaded(529)로 죽으면 문항 생성
파이프라인이 통째로 멈춘다. 자동 스왑은 그때 다른 켜진 모델로 넘겨 서비스를 잇는다.
`ai_client._post_messages`가 후보 목록을 순서대로 시도하되:
- **네트워크 오류·비200(429/529/400 등) → 다음 후보로 스왑**(가용성 문제).
- **200인데 본문이 비JSON(프록시·CDN이 200으로 HTML 에러 페이지 반환) → 다음 후보로 스왑.**
  (원시 `ValueError`를 누수하면 `AiGenerationError`로 안 감싸져 엔드포인트가 500을 뱉고
  자동 스왑도 무력화된다 — skeptic 적대검토가 잡은 지점. 비200과 동일 처리로 방어.)
- **200을 받았는데 거절(refusal)·빈 응답 → 스왑 안 함**(요청 내용 문제라 다른 모델도 같을
  가능성이 큼 → 토큰 낭비 방지, 정직하게 예외).
슬롯 미설정이거나 후보가 다 실패하면 마지막 오류를 정직하게 전파한다(가짜 성공 금지).

### 왜 토큰 누적을 '원자적 UPDATE'로 하나
추정 비용의 근거인 누적 토큰은 `SET tokens_in = tokens_in + n`으로 증가시킨다. 파이썬에서
읽고-더하고-쓰면 동시 생성 요청 사이에 lost update가 나 토큰이 샌다. 특히 **검증 1회에
solve 호출이 3~4회**라 한 요청 안에서도 같은 모델에 여러 번 기록된다 — 원자적 UPDATE라야
같은 트랜잭션 내 순차 누적도, 요청 간 동시성도 안전하다.

### 왜 슬롯 미설정이 '고장'이 아니라 '폴백'인가
슬롯을 비워 두면 `resolve_candidates`가 빈 목록을 주고, `ai_client`는 `.env`의 `LLM_MODEL`로
한 번 시도한다(하위호환). 즉 이 기능을 **아무것도 설정 안 해도 기존 생성이 그대로 동작**한다.
운영자가 슬롯을 채우는 순간부터 그 모델이 쓰인다. 화면은 이 안전망 모델명을 항상 보여준다.

## 데이터·코드 지도

- **모델** `app/models/ai_model_config.py` — `AiModelConfig`(provider·model_id·name·enabled·
  cost_in/out_usd·tokens_in/out). 슬롯 컬럼은 **없다**(settings 포인터). FK 없는 소프트 참조.
- **마이그레이션** `alembic/versions/ai_model_cfg_01_runtime_models.py`(down_revision
  `course_exam_01`). 멱등(테이블 존재 검사).
- **서비스** `app/services/ai_models_service.py` — `resolve_candidates(db, role)`(슬롯 우선 +
  자동 스왑), `record_usage`(원자적), `estimate_cost_usd`, `get_slot`/`set_slot`/
  `auto_swap_enabled`/`set_auto_swap`.
- **클라이언트 배선** `app/clients/ai_client.py` — `_post_messages(..., models, on_usage)`가
  후보 순회·스왑·usage 콜백. `generate_lecture_questions`/`solve_questions`/`verify_questions`가
  models/on_usage를 관통시킨다. **db를 모른다**(순수 클라이언트) — 후보 목록과 콜백은
  호출자가 주입.
- **엔드포인트** `app/api/v1/endpoints/ops.py` — `GET /ops/ai-runtime`,
  `POST/PATCH/DELETE /ops/ai-runtime/models[/{id}]`, `PUT /ops/ai-runtime/config`. 전부
  `require_ops` + 감사 로그. 삭제 시 그 모델을 가리키던 슬롯을 함께 비운다(죽은 포인터 방지).
- **생성 호출부** `app/api/v1/endpoints/lectures.py::ops_generate_questions` — 요청마다
  `resolve_candidates`로 gen/verify 후보를 만들고, `_on_usage` 콜백으로 토큰을 모델에 누적.
- **프론트** `src/pages/ops/OpsAiRuntimeSection.tsx`(설정 페이지 상단 섹션) + `src/api/ops.ts`
  (`opsAiRuntimeApi`). 성공 표기는 서버 응답으로만(가짜 성공 금지).

## 한계와 다음 방향

- **Anthropic 전용(1단계).** 실제 호출은 Anthropic Messages API만 지원한다. `provider`는
  표시용 라벨이고, 슬롯엔 Anthropic 계열 model_id를 넣는 전제다. OpenAI 등 타사 LLM을
  생성/검증 백엔드로 붙이려면 별도 호출 경로(어댑터)가 필요하다 — **다음 단계**. (STT는
  이미 OpenAI Whisper를 별도로 쓰지만, 그건 전사이지 문항 생성 LLM이 아니다.)
- **추정 비용은 참고용.** 단가는 운영자가 넣는 공시가이고, 토큰은 응답 `usage` 합산이다 —
  실청구액이 아니다. 다음 방향: 슬롯별 월 예산·경보, 실제 청구 API 연동.
- **거절/실패 시 토큰 회계.** 생성이 예외로 끝나면 엔드포인트가 commit을 안 해 그 요청의
  기록이 롤백된다(성공 요청만 확정). 의도된 "best-effort" 회계다.
- **모델 미검증 저장.** model_id 오타는 저장은 되고 첫 호출에서 제공사 오류(400 등)로
  드러난다(자동 스왑이 켜져 있으면 다른 모델로 넘어감). 실시간 model_id 검증은 미구현.
