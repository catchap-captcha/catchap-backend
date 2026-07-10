"""RBAC — API 단계 권한 검사.

- 기관 관리자(org_admin, 교장 격): 자기 기관 전체
- 학년부장(grade_head): 담당 학년 범위 (교사 권한 + 그 학년 반/교사 관리)
- 교사(teacher): 담당 학급 범위
- 학부모: 승인된 연결 자녀만
- 학생: 본인 데이터만
- 운영자(ops): 운영 API(/ops/*)만 — 기관 스코프 데이터(학생 명단·실명 등)에는 접근 불가
principal 은 (kind, id) — kind: 'user' | 'student'
"""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWTError
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db.session import get_db
from app.models import Membership, ParentStudentLink, StudentProfile, User

bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    kind: str  # user | student
    id: str
    role: str  # student | parent | teacher | org_admin | ops
    user: User | None = None
    student: StudentProfile | None = None

    @property
    def organization_id(self) -> str | None:
        if self.student:
            return self.student.organization_id
        if self.user:
            return self.user.organization_id
        return None


def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="인증이 필요합니다.")
    try:
        payload = decode_token(credentials.credentials)
    except PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="토큰이 유효하지 않습니다.")
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="access token이 아닙니다.")

    subject_id: str = payload["sub"]
    role: str = payload.get("role", "")

    if role == "student":
        student = db.get(StudentProfile, subject_id)
        # 탈퇴/비활성(status=disabled) 학생은 기존 토큰이 남아 있어도 접근 차단 (B3)
        if student is None or student.status == "disabled":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="학생을 찾을 수 없습니다.")
        return Principal(kind="student", id=subject_id, role="student", student=student)

    user = db.get(User, subject_id)
    if user is None or user.status == "disabled":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="사용자를 찾을 수 없습니다.")
    return Principal(kind="user", id=subject_id, role=user.role, user=user)


def require_roles(*roles: str):
    def dep(principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="접근 권한이 없습니다.")
        return principal

    return dep


require_student = require_roles("student")
require_parent = require_roles("parent")
# 학년부장은 교사 권한도 포함(자기 학급 조회 등)
require_teacher = require_roles("teacher", "grade_head", "org_admin")
# 학년/반/교사 관리: 학년부장(자기 학년) + 교장. 운영자는 기관 데이터에 접근하지 않는다.
require_grade_head = require_roles("grade_head", "org_admin")
# 기관 전체 관리(교장 전용): 학년부장·운영자는 제외
require_org_admin = require_roles("org_admin")
require_ops = require_roles("ops")


def check_org_scope(principal: Principal, organization_id: str) -> None:
    """자기 기관만 접근 허용.

    운영자(ops)는 기관 스코프 데이터(학생 명단·실명·나이 등)에 접근하지 않는다.
    운영자는 /ops/* 콘솔의 익명·집계 데이터만 보며, 아동 PII는 기관 관리자/학년부장에게만
    노출된다. (require_org_admin/require_grade_head에서 ops를 제외했으므로 ops는 이 함수에
    도달하지 못하지만, 향후 실수로 role 번들에 ops가 다시 추가되더라도 여기서 막는다.)
    """
    if principal.organization_id != organization_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="기관 접근 권한이 없습니다.")


def managed_grade(db: Session, principal: Principal) -> int | None:
    """학년부장이 담당하는 학년(정수). 학년부장이 아니거나 미지정이면 None."""
    if principal.role != "grade_head" or principal.organization_id is None:
        return None
    m = (
        db.query(Membership)
        .filter(
            Membership.user_id == principal.id,
            Membership.organization_id == principal.organization_id,
            Membership.status != "disabled",
        )
        .first()
    )
    return m.managed_grade if m else None


def check_grade_scope(db: Session, principal: Principal, organization_id: str, grade: int | None) -> None:
    """학년 단위 접근 검사.

    - org_admin: 기관 범위만 맞으면 전체 학년 허용 (교장은 전 학년)
    - grade_head: 자기 담당 학년(grade)일 때만 허용
    - 그 외(ops 포함): 거부
    """
    check_org_scope(principal, organization_id)
    if principal.role == "org_admin":
        return
    if principal.role == "grade_head":
        mg = managed_grade(db, principal)
        if mg is not None and grade is not None and mg == grade:
            return
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="담당 학년이 아닙니다.")


def check_parent_child(db: Session, parent_user_id: str, student_id: str) -> ParentStudentLink:
    link = (
        db.query(ParentStudentLink)
        .filter(
            ParentStudentLink.parent_user_id == parent_user_id,
            ParentStudentLink.student_id == student_id,
            ParentStudentLink.status == "approved",
        )
        .first()
    )
    if link is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="연결된 자녀가 아닙니다.")
    return link
