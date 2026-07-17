"""옛 UTC-naive 저장 컬럼을 로컬(KST)로 보정 — 시각 규약 통일의 데이터 쪽 절반.

앱 코드가 `datetime.now()`(KST naive) 하나로 통일되면서(0717), 예전에 UTC-naive로
저장된 만료·소비 시각들은 **읽는 쪽과 9시간 어긋난다**. 코드만 바꾸고 데이터를 두면:
  - 만료 판정이 9시간 일찍 발동(더 엄격 — 보안은 안전하나 사용자에겐 손해)
  - `organizations.code_expires_at`은 표시가 -9h 틀림. 예전엔 `utc_to_local()`이
    읽는 쪽에서 보정했는데 그 보정을 코드 통일과 함께 걷어냈기 때문. **365일짜리라
    코드를 rotate 하기 전까지 최장 1년 틀린 값이 노출된다.**
→ 그래서 코드 변경과 이 마이그레이션은 **반드시 같이 배포**해야 한다.

## 왜 일괄 +9h가 안전한가 (여기 적힌 컬럼에 한해)
아래 컬럼들의 쓰기 경로는 전부 `datetime.now(timezone.utc)` 계열이었다 —
**컨테이너 TZ와 무관(TZ-invariant)**하므로 저장값이 항상 UTC 벽시계다. 2026-07-11
컨테이너 TZ 고정(7f96df9) 전후로 갈리지 않는다.

## 왜 created_at은 안 건드리나
`created_at`은 `datetime.now()`(TZ 의존)라 7f96df9 **전=UTC / 후=KST로 실제로 갈린다.**
일괄 보정하면 후기 행이 미래로 밀린다. 표시 전용이라 방치한다.

## 왜 아래 컬럼들은 빠졌나 (혼재 — 일괄 보정 불가)
  - `users.email_verified_at` · `memberships.joined_at`
      auth_service(UTC) / ops.py의 맨 datetime.now()(KST)가 같은 컬럼에 섞어 씀
  - `consents.granted_at` · `withdrawn_at`
      scratch_access(UTC) / parents.py(KST)가 섞어 씀
  - `invitations.expires_at` · `captcha_store.expires_at`  → 원래부터 KST
  - `captcha_consumed_tokens.expires_at` → 읽는 코드가 없음(UNIQUE 제약이 리플레이 차단)
셋 다 시계와 비교되지 않고 NULL 체크로만 쓰여 기능 영향이 없다(표시 skew만 남음).

Revision ID: tz_kst_01
Revises: sys_settings_01
"""

from alembic import op

revision = "tz_kst_01"
down_revision = "sys_settings_01"
branch_labels = None
depends_on = None

# (테이블, [컬럼...]) — 전부 TZ-invariant UTC로 저장돼 있던 것들
_UTC_COLUMNS = [
    ("refresh_tokens", ["expires_at", "revoked_at"]),
    ("email_verification_codes", ["expires_at", "verified_at", "used_at"]),
    ("organizations", ["code_expires_at"]),
    ("student_join_codes", ["expires_at", "used_at"]),
    ("parent_invite_codes", ["expires_at"]),
]

_SHIFT = "9"  # KST = UTC+9


def _shift(sign: str) -> None:
    for table, cols in _UTC_COLUMNS:
        sets = ", ".join(
            "%s = DATE_ADD(%s, INTERVAL %s%s HOUR)" % (c, c, sign, _SHIFT) for c in cols
        )
        # NULL은 DATE_ADD가 NULL로 두므로 그대로 보존된다(WHERE 불필요).
        op.execute("UPDATE %s SET %s" % (table, sets))


def upgrade() -> None:
    _shift("")


def downgrade() -> None:
    _shift("-")
