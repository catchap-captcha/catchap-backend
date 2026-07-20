# 개인정보 익명화·탈퇴 런북 — 운영자 제외 전원 파기 (행동데이터 보존)

목적: 운영자(role='ops')를 **제외한 전원**(전 학생 + 전 비운영자 사용자)의 식별 PII를 파기하고
계정을 탈퇴(status='disabled') 처리한다. **★행동데이터(learning_attempts·behavior_summaries·
behavior_traces·lecture_checkpoint_events)는 절대 삭제하지 않는다** — student_id 키로 익명 집계에
남겨 서비스 지표·연구용으로 계속 쓴다(재식별 불가).

근거: PIPA '목적 달성 후 지체없는 파기'(사용자 결정 2026-07-13 익명화 방식). 로직은
`app/services/privacy_service.py`(식별 PII만 마스킹/파기, 로그인은 disabled로 차단, 멱등).

## 무엇을 파기하나
- **학생(student_profiles)**: real_name·age·birth_date·guardian_email·gender·nickname·avatar 파기,
  student_login_id/password 무력화, class_id 제거, status='disabled'.
- **비운영자 사용자(users where role<>'ops')**: name·email 파기(`del_...@deleted.invalid`),
  password 무력화, status='disabled'.
- **운영자(role='ops')**: 손대지 않음(그대로 보존).
- **행동/학습 데이터**: 손대지 않음 — 실행 커맨드가 4개 테이블 카운트를 전후로 검사해 삭제 0을 강제.

## 명령
```bash
python manage_privacy.py all-except-ops            # 드라이런(대상 수만 출력, 변경 없음)
python manage_privacy.py all-except-ops --execute  # 실행 + 행동 불변식 검사 + 운영자 보존 확인
```
멱등: 이미 파기된 계정은 건너뛴다(재실행 안전).

## ★프로덕션 적용 전 반드시 (순서 중요)
프로덕션 스키마가 코드보다 **뒤처져 있으면** 익명화가 실패한다(예: `student_profiles.birth_date`
컬럼 없음 → ORM 쿼리 오류). 2026-07-21 기준 **프로덕션 head=`student_email_01`로 코드보다 ~18개
마이그레이션 뒤짐**. 따라서 실제 적용 순서는:

1. **로컬 백업**(읽기 전용):
   ```bash
   ssh -i <KeyPair> ubuntu@210.109.52.114 \
     "sudo mysqldump --single-transaction --routines --triggers --databases catchap_dev_db | gzip -c" \
     > backups/catchap_prod_$(date +%Y%m%d_%H%M%S).sql.gz
   ```
   (DB VM은 native MySQL·root 소켓 인증. gzip 무결성 `gunzip -t` 확인.)
2. **로컬 복원 → 드라이런·실행으로 리허설**(아래 '검증 절차'). 행동 불변식 OK 확인.
3. **배포**(코드 + 마이그레이션 head 동시) — 배포 규칙상 명시 승인 필요([[catchap-no-deploy-rule]]).
   `alembic upgrade head`가 스키마를 맞춘 뒤라야 익명화가 돈다. (마이그레이션은 행동데이터를 삭제하지 않음을 리허설에서 확인함.)
4. **프로덕션에서 실행**: `python manage_privacy.py all-except-ops --execute` (컨테이너 안).
   행동 불변식·운영자 보존 출력 확인.

## 로컬 검증 절차 (프로덕션 적용 전 리허설 — 2026-07-21 실행·통과)
```bash
MYSQL=/path/to/mysql
# 1) 백업을 격리 DB로 복원 (root 필요 — catchap_user는 CREATE 권한 없음)
$MYSQL -u root -p<root> -e "DROP DATABASE IF EXISTS catchap_dev_db"
gunzip -c backups/<dump>.sql.gz | $MYSQL -u root -p<root>
$MYSQL -u root -p<root> -e "GRANT ALL ON catchap_dev_db.* TO 'catchap_user'@'localhost'; FLUSH PRIVILEGES"
# 2) 배포 시뮬레이션 — 스키마를 head로 (프로덕션이 뒤처져 있으므로 필수)
export DATABASE_URL="mysql+pymysql://catchap_user:<pw>@localhost:3306/catchap_dev_db?charset=utf8mb4"
python -m alembic upgrade head
# 3) 익명화 실행 + 불변식
python manage_privacy.py all-except-ops --execute
```
**2026-07-21 리허설 결과(프로덕션 스냅샷)**: 학생 47·비운영자 사용자 11 익명화·탈퇴, 운영자 5 보존.
행동 불변식 전부 OK — learning_attempts 21,619 / behavior_summaries 21,632 / behavior_traces 21,538 /
lecture_checkpoint_events 1 (전부 삭제 0). 학생 real_name 0·전원 disabled, 비운영자 전원 del_ 이메일,
운영자 이메일 그대로.

## 되돌리기
익명화는 **비가역**(원문 PII 파기). 백업(`backups/*.sql.gz`)이 유일한 복구 수단이므로 실행 전
반드시 백업을 확보하고 무결성을 확인한다. 백업 파일은 실 PII를 담으므로 접근을 제한·보관기한 후 파기.
