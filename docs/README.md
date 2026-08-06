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
- **[ai-model-selection.md](ai-model-selection.md)** — 운영자 AI 모델 선택(#26).
  생성·검증 **2슬롯**에 어떤 모델을 쓸지 고르는 런타임 설정. 왜 표시용 카탈로그와
  분리했는지, 왜 슬롯 포인터를 settings에 뒀는지(같은 모델 두 슬롯), **자동 스왑**의
  판단 규칙(가용성 실패=스왑, 거절=스왑 안 함), 토큰 누적을 원자적으로 하는 이유.
- **[question-formats-and-behavior-data.md](question-formats-and-behavior-data.md)** —
  전체학습 은행과 강의 캡차의 문항 형식이 왜 다른지, 왜 통일하지 않는지. 행동 데이터
  관점에서 드래그가 왜 우월한지, 보기 셔플 정책. ("강의 캡차 = 4지선다?"의 정확한 답.)
- **[course-exam-design.md](course-exam-design.md)** — 코스 수료 시험(1단계 구현 완료).
  왜 완전학습(mastery)인지, 기출의 비영리 교육용 전제(출처 표시 의무·유료화 리스크),
  완벽 통과 정책 재설계(오답 1건 영구 박탈 폐지 → '한 회차에 다 맞힘' + 재도전 경로),
  데이터 모델·API·기존 코드 재사용 지도.
- **[my-records-recentering.md](my-records-recentering.md)** — '나의 기록'을 게임 시절
  통계 대시보드 → 학습 루프 성취 리포트로 재중심화. 코스 수료 현황(수료/진행/잠김),
  **왜 '행동 우선(action-first)' 정렬**인지(실무 표준), 게임 라벨 정리, 스케일(더 보기).
- **[question-bank-scale-design.md](question-bank-scale-design.md)** — 문제은행 규모
  확장 설계(구현 전). 만 개 규모에서 무한 스트림이 왜 무너지는지, 학생×문항 상태
  기계 + 간격 반복(SRS)·"오늘의 큐"·세트 단위·20주 챕터의 한계와 코스 기반 재편.
- **[onboarding-security-design.md](onboarding-security-design.md)** — 가입·코드 보안 설계.
- **[social-login-design.md](social-login-design.md)** — 카카오·네이버·구글 소셜 로그인.
  왜 신규 가입이 2단계인지(연령 게이트 우회 방지), 검증되지 않은 이메일을 자동 연결하지
  않는 이유, state·redirect_uri 허용목록·토큰 미저장 같은 보안 장치와 운영 준비 목록.
- **[BEHAVIOR_DATA.md](BEHAVIOR_DATA.md)** — 행동 데이터 수집·의미.
- **[monitoring.md](monitoring.md)** — 서버 모니터링(자원·GPU·LLM + 임계 경보). 왜 push
  기반·최신 1행/서버인지, 에이전트 배포(systemd)·실시간성·부하, 임계(CRIT) 기준.
- **[privacy-anonymization-runbook.md](privacy-anonymization-runbook.md)** — ★운영자 제외
  전원 익명화·탈퇴 런북. 행동데이터 절대 보존(4테이블 불변식), 백업→복원→배포(head)→실행
  순서, 프로덕션이 코드보다 뒤처져 있어 익명화 전 마이그레이션 선행이 필요한 이유.

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
