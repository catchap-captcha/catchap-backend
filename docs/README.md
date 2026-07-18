# CatChap 백엔드 문서 인덱스

> 팀원(과 팀원이 쓰는 GPT/Claude)이 "무엇을 어디서 읽어야 하는지" 찾는 지도입니다.
> 코드는 "무엇을" 하는지 보여주고, 이 문서들은 **"왜"** 그렇게 했는지 설명합니다.

## 먼저 읽으세요 (큰 맥락)

- **[product-direction.md](product-direction.md)** — ★ CatChap이 왜 지금 이 모습인가.
  아동 캡차 → 인강 시청검증으로의 제품 전환, 은퇴한 역할들, 데이터·배포 원칙.
  **저장소를 처음 보면 이것부터.**

## 기능별 설계 노트 (왜 이 코드·이 기술인가)

- **[lecture-question-pipeline.md](lecture-question-pipeline.md)** — 강의 확인 문항
  자동 생성 + 자기검증(2-LLM adversarial filtering). 왜 LLM을 2개 쓰는지,
  셔플 다수결·공개맥락·3분류를 왜 넣었는지, 한계와 다음 방향.
- **[onboarding-security-design.md](onboarding-security-design.md)** — 가입·코드 보안 설계.
- **[BEHAVIOR_DATA.md](BEHAVIOR_DATA.md)** — 행동 데이터 수집·의미.

## 레퍼런스 (무엇이 있는가)

- **[api-spec.md](api-spec.md)** — API 명세.
- **[db-schema.md](db-schema.md)** / **[schema.sql](schema.sql)** — DB 스키마
  (주의: 클라우드 덤프 기반이라 최신 마이그레이션이 안 반영됐을 수 있음 — 실제는
  `alembic/versions/`가 정본).
- **[backend-implementation-checklist.md](backend-implementation-checklist.md)** — 구현 체크리스트.
- **[DEV_ACCOUNTS.md](DEV_ACCOUNTS.md)** — 개발용 계정.

## "왜"를 찾는 3단계

1. **가장 큰 맥락** → `product-direction.md`
2. **기능 단위 맥락** → 해당 기능의 설계 노트(위)
3. **줄 단위 맥락** → 코드 주석 + `git log`(커밋 메시지에 "왜"를 적어 둠)

## 문서 작성 규칙 (팀 합의, 2026-07-18)

새 기능·구조 변경을 만들 때는 **"왜 이렇게 했는지 / 왜 이 기술인지 / 다음 방향은
무엇인지"를 코드 주석 또는 이 docs에 남긴다.** 팀원과 그들의 AI 도구가 코드만
보고는 알 수 없는 "의도"를 학습할 수 있게 하기 위함이다.
