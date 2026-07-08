# 행동 데이터 — 어디에 무엇이 쌓이는가

아동용 캡차 판정 모델(같은 문제를 풀어도 "아이답게" 푸는지 판별)의 학습셋을 만들기 위해,
학생이 문제를 푸는 동안의 **포인터 움직임·풀이 리듬**을 수집한다.
개인 식별 정보(실명·나이·연락처)는 이 파이프라인에 저장하지 않는다.

## 테이블 1 — `behavior_summaries` (요약 지표, 1행 = 문항 1회 상호작용)

| 컬럼 | 타입 | 내용 |
|---|---|---|
| `id` | CHAR(36) PK | 행동 이벤트 id |
| `organization_id` | CHAR(36) | 소속 기관 (인덱스) |
| `student_id` | CHAR(36) NULL | 학생 id. **외부 위젯에서 위조 가능성이 있는 값은 서버가 익명(NULL) 처리** |
| `source_type` | VARCHAR(30) | 수집 경로: `game`(인앱 학생 게임) / `edu-api`(교육형 API·임베드 위젯) |
| `solve_time_ms` | INT | 문항 노출→제출까지 ms |
| `path_length` | FLOAT | 포인터 이동 총 거리(px). **궤적이 있으면 서버가 직접 계산** (자기신고 무시), 캡 100,000 |
| `avg_speed` | FLOAT | 평균 속도(px/ms). 300ms+ 멈춤 구간의 시간은 제외하고 계산, 캡 100 |
| `pause_count` | INT | 300ms 이상 입력이 멈춘 구간 수 |
| `retry_count` | INT | 클라이언트 신고 재시도 수 (참고용 — 위젯은 리렌더 때문에 사실상 0) |
| `drop_distance_norm` | FLOAT | 드래그형 채점의 드롭 지점 거리(0~1 정규화, 서버 진실값) |
| `interaction_result` | VARCHAR(20) | 실기록: `correct`/`incorrect` · 시드(데모) 데이터: `pass`/`fail` — 소비처는 양쪽 다 처리 |
| `risk_level` | VARCHAR(20) | `low`/`review`/`elevated` (기본 low) |
| `occurred_at` | DATETIME | 상호작용 시각 |
| `dataset_status` | VARCHAR(20) | 학습셋 큐레이션: `candidate`(기본)/`included`/`excluded` — 운영 콘솔에서 변경 |

## 테이블 2 — `behavior_traces` (원시 포인터 궤적, summaries 1행당 최대 1행)

| 컬럼 | 타입 | 내용 |
|---|---|---|
| `behavior_id` | CHAR(36) UNIQUE | `behavior_summaries.id` 참조 |
| `points` | JSON | `[[t_ms, x, y], ...]` — t는 상호작용 시작 기준 ms, x/y는 **캡처 영역 기준 0~1 정규화** |
| `point_count` | INT | 점 개수 (목록 화면은 이 값만 조회 — JSON은 상세 API에서만) |
| `duration_ms` | INT | 궤적 길이(시간) |
| `box_w`, `box_h` | INT | 캡처 영역 px 크기 (좌표 복원용, 캡 4,000) |

- 클라이언트는 최대 1,500점(16ms 스로틀)까지 보내고, 서버는 2,000점에서 캡.
- 마우스를 전혀 움직이지 않으면(점 2개 미만) 궤적 없이 summaries만 남는다(`point_count` NULL 표기).

## 수집 경로 (3곳 — 전부 `captcha_service.record_behavior_event()`로 수렴)

1. **인앱 학생 게임** (`source_type=game`): GameScreen `#captcha-mount` 영역에 `pointerTrace` 부착 →
   문항 제출 시 `POST /students/me/game-answer`의 `behavior`로 전송 → `save_attempt` 안에서 기록.
   **생활·수학·과학·역사·영어 5과목 실전 플레이 전부 이 경로를 탄다** (2026-07-08 수학·과학·역사, 이어 영어 추가).
   학생 인증 경로라 `student_id`는 항상 본인 것만 기록된다.
2. **교육형 API / 임베드 위젯** (`source_type=edu-api`): `catchap-widget.js`가 위젯 박스 안 포인터를
   자체 캡처 → `POST /captcha/v1/verify`의 `behavior` → `record_behavior()` (student_id 위조 방어 포함).
   single/multi(복수선택)/drag_drop/trace_path 챌린지 전 유형에서 수집.
3. **폴백 데모** (`source_type=game`): EDU 키 미설정 환경의 데모 완주 시 마지막 `POST /learning/attempts`
   1건에 behavior 탑재. 플레이스홀더 위 움직임이라 노이즈 — 큐레이션에서 제외 권장.

## 서버가 지키는 것 (위조 방어)

- 궤적이 있으면 `path_length`/`avg_speed`/`pause_count`는 **서버가 궤적에서 직접 재계산**하고 자기신고 값을 버린다.
- 지표 캡: PATH 100,000px · SPEED 100px/ms · BOX 4,000px · drop_norm 0~1 클램프, Infinity/NaN 거부.
- 요청 본문 전역 1MB 캡(413).
- 외부 위젯이 보낸 `student_id`는 검증 실패 시 익명 처리.

## 소비처

- **운영 콘솔 `/ops/behavior`** (require_ops): KPI(총 수집·최근 7일·edu-api 수집·학습셋 확정),
  목록 필터(수집 경로/결과/위험도/학생·익명/큐레이션 상태), 궤적 뱃지, 궤적 SVG 뷰어
  (`GET /ops/behavior/records/{id}/trace`), `dataset_status` 변경, CSV 내보내기(원시 궤적·식별정보 제외).
- 과목 컬럼은 없다 — 과목 구분이 필요해지면 `behavior_summaries`에 subject 컬럼 추가가 필요(현재는 미저장).

## 알려진 한계

- 시드(데모) 행 ~221건은 `pass/fail` 표기에 궤적이 없음 — 학습셋에서 제외 대상.
- `avg_speed`는 멈춤 구간의 이동거리는 포함하되 시간은 제외하는 정의라 과대 추정 가능(캡으로 완충).
- game-answer는 학습 피드백으로 채점 직후 정답·해설을 공개한다 — 같은 문항 재제출로 "정답 기록"을
  만들 수 있으나 복습(replay)은 코인·퀴즈상태에 반영되지 않고, 코인은 일일 상한으로 제한된다.
