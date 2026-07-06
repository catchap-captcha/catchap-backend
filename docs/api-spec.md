# CatChap Backend API Spec

Base URL: `http://localhost:8000`  |  생성 기준: 실행 중 서버의 OpenAPI (paths 105 / operations 116, 2026-07-06)

인증: `Authorization: Bearer <access_token>` (JWT). 권한 태그: PUB=무인증, STU=학생, PAR=학부모, TCH=교사, ORG=기관 관리자, OPS=운영자. 권한 강제는 `app/core/permissions.py`.

> 이 표는 `app.openapi()`에서 자동 생성한 목록입니다. 재생성: 서버 코드에서 `app.openapi()['paths']` 덤프(또는 `/openapi.json`).

| Method | Endpoint | 설명 | 태그 |
| --- | --- | --- | --- |
| POST | `/api/v1/ai/parent-chat` | Parent Chat | misc |
| GET | `/api/v1/ai/parent-chat/intro` | Parent Chat Intro | misc |
| POST | `/api/v1/ai/student-chat` | Student Chat | misc |
| GET | `/api/v1/ai/student-chat/greeting` | Student Chat Greeting | misc |
| POST | `/api/v1/auth/activate-student` | Activate Student | auth |
| POST | `/api/v1/auth/check-student-id` | Check Student Id | auth |
| POST | `/api/v1/auth/email/send` | Send Email Code | auth |
| POST | `/api/v1/auth/email/verify` | Verify Email Code | auth |
| POST | `/api/v1/auth/login` | Login | auth |
| POST | `/api/v1/auth/logout` | Logout | auth |
| GET | `/api/v1/auth/me` | Me | auth |
| POST | `/api/v1/auth/ops-login` | Ops Login | auth |
| POST | `/api/v1/auth/password-reset/confirm` | Password Reset Confirm | auth |
| POST | `/api/v1/auth/password-reset/request` | Password Reset Request | auth |
| POST | `/api/v1/auth/refresh` | Refresh | auth |
| POST | `/api/v1/auth/register/org` | Register Org | auth |
| POST | `/api/v1/auth/register/parent` | Register Parent | auth |
| POST | `/api/v1/auth/register/student` | Register Student | auth |
| POST | `/api/v1/auth/register/teacher` | Register Teacher | auth |
| POST | `/api/v1/auth/student-login` | Student Login | auth |
| POST | `/api/v1/auth/verify-org-code` | Verify Org Code | auth |
| POST | `/api/v1/auth/verify-teacher-code` | Verify Teacher Code | auth |
| GET | `/api/v1/captcha/challenge` | Captcha Challenge (stub) | misc |
| GET | `/api/v1/contents/search` | Search Contents | students |
| GET | `/api/v1/health` | Health | health |
| POST | `/api/v1/inquiries` | Create Inquiry | misc |
| GET | `/api/v1/institutions/regions` | Regions | institutions |
| GET | `/api/v1/institutions/search` | Search Institutions | institutions |
| POST | `/api/v1/learning/attempts` | Save Attempt | students |
| GET | `/api/v1/notifications` | List Notifications | notifications |
| PATCH | `/api/v1/notifications/read-all` | Mark All Read | notifications |
| PATCH | `/api/v1/notifications/{notification_id}/read` | Mark Read | notifications |
| GET | `/api/v1/ops/ai-models` | Ai Models | ops |
| GET | `/api/v1/ops/dashboard` | Dashboard | ops |
| GET | `/api/v1/ops/inquiries` | Inquiries | ops |
| POST | `/api/v1/ops/inquiries/{inquiry_id}/answer` | Answer Inquiry | ops |
| POST | `/api/v1/ops/inquiries/{inquiry_id}/resolve` | Resolve Inquiry | ops |
| GET | `/api/v1/ops/logs` | Logs | ops |
| GET | `/api/v1/ops/orgs` | Orgs | ops |
| GET | `/api/v1/ops/registration-requests` | Registration Requests | ops |
| POST | `/api/v1/ops/registration-requests/{request_id}/approve` | Approve Request | ops |
| POST | `/api/v1/ops/registration-requests/{request_id}/reject` | Reject Request | ops |
| GET | `/api/v1/ops/system` | System | ops |
| GET | `/api/v1/orgs/me` | My Org | orgs |
| PATCH | `/api/v1/orgs/{org_id}` | Update Org | orgs |
| GET | `/api/v1/orgs/{org_id}/admins` | Admins | orgs |
| GET | `/api/v1/orgs/{org_id}/ai-models` | Ai Models | orgs |
| GET | `/api/v1/orgs/{org_id}/analytics` | Analytics | orgs |
| GET | `/api/v1/orgs/{org_id}/billing` | Billing | orgs |
| GET | `/api/v1/orgs/{org_id}/captcha-settings` | Captcha Settings | orgs |
| PUT | `/api/v1/orgs/{org_id}/captcha-settings` | Save Captcha Settings | orgs |
| GET | `/api/v1/orgs/{org_id}/classes` | Classes | orgs |
| GET | `/api/v1/orgs/{org_id}/dashboard` | Dashboard | orgs |
| POST | `/api/v1/orgs/{org_id}/parent-links/{link_id}/revoke` | Revoke Parent Link | orgs |
| GET | `/api/v1/orgs/{org_id}/roster` | Roster | orgs |
| GET | `/api/v1/orgs/{org_id}/security-stats` | Security Stats | orgs |
| GET | `/api/v1/orgs/{org_id}/sidebar` | Sidebar | orgs |
| GET | `/api/v1/orgs/{org_id}/site-status` | Site Status | orgs |
| POST | `/api/v1/orgs/{org_id}/students/register` | Register Students | orgs |
| PATCH | `/api/v1/orgs/{org_id}/students/{student_id}/class` | Assign Student Class | orgs |
| POST | `/api/v1/orgs/{org_id}/students/{student_id}/invite-code` | Issue Invite | orgs |
| GET | `/api/v1/orgs/{org_id}/students/{student_id}/parent-links` | Student Parent Links | orgs |
| POST | `/api/v1/orgs/{org_id}/students/{student_id}/reset-password` | Reset Student Password | orgs |
| GET | `/api/v1/orgs/{org_id}/teachers` | Teachers | orgs |
| POST | `/api/v1/orgs/{org_id}/teachers` | Add Teacher | orgs |
| DELETE | `/api/v1/orgs/{org_id}/teachers/{teacher_id}` | Delete Teacher | orgs |
| PATCH | `/api/v1/orgs/{org_id}/teachers/{teacher_id}` | Update Teacher | orgs |
| GET | `/api/v1/parents/me/children` | Children | parents |
| POST | `/api/v1/parents/me/children/link-invite` | Link Invite (초대코드) | parents |
| POST | `/api/v1/parents/me/children/link-request` | Link Request | parents |
| DELETE | `/api/v1/parents/me/children/{child_id}/link` | Unlink | parents |
| GET | `/api/v1/parents/me/children/{child_id}/report` | Child Report | parents |
| GET | `/api/v1/parents/me/children/{child_id}/settings` | Child Settings | parents |
| PUT | `/api/v1/parents/me/children/{child_id}/settings` | Save Child Settings | parents |
| GET | `/api/v1/parents/me/children/{child_id}/summary` | Child Summary | parents |
| PATCH | `/api/v1/parents/me/profile` | Update Profile | parents |
| GET | `/api/v1/parents/me/reports` | My Reports | parents |
| POST | `/api/v1/reports/{report_id}/download` | Download Report | parents |
| GET | `/api/v1/settings/me` | Get Settings | settings |
| PUT | `/api/v1/settings/me` | Save Settings | settings |
| DELETE | `/api/v1/settings/me/account` | Delete Account | settings |
| POST | `/api/v1/settings/me/change-password` | Change Password | settings |
| GET | `/api/v1/settings/me/export` | Export Data | settings |
| POST | `/api/v1/settings/me/logout-all` | Logout All | settings |
| GET | `/api/v1/shop/catalog` | Shop Catalog | students |
| PUT | `/api/v1/students/me/avatar` | Save Avatar | students |
| GET | `/api/v1/students/me/badges` | Badges | students |
| GET | `/api/v1/students/me/class-ranking` | Class Ranking | students |
| GET | `/api/v1/students/me/concepts/read` | Concept Reads | students |
| POST | `/api/v1/students/me/concepts/read` | Mark Concept Read | students |
| GET | `/api/v1/students/me/daily-quiz` | Daily Quiz | students |
| GET | `/api/v1/students/me/dashboard` | Dashboard | students |
| GET | `/api/v1/students/me/game-state` | Game State | students |
| PATCH | `/api/v1/students/me/password` | Change My Password | students |
| PATCH | `/api/v1/students/me/profile` | Update Profile | students |
| GET | `/api/v1/students/me/progress` | Progress | students |
| GET | `/api/v1/students/me/recommendations` | Recommendations | students |
| GET | `/api/v1/students/me/records` | Records | students |
| GET | `/api/v1/students/me/result` | Result | students |
| POST | `/api/v1/students/me/shop/purchase` | Purchase | students |
| GET | `/api/v1/students/me/wallet` | Wallet | students |
| GET | `/api/v1/students/me/wrong-notes` | Wrong Notes | students |
| GET | `/api/v1/teacher/analytics` | Analytics | teacher |
| GET | `/api/v1/teacher/class/students` | My Class Students | teacher |
| POST | `/api/v1/teacher/class/students` | Add Student By Code | teacher |
| DELETE | `/api/v1/teacher/class/students/{student_id}` | Remove Class Student | teacher |
| GET | `/api/v1/teacher/class/students/{student_id}` | Class Student Detail | teacher |
| PATCH | `/api/v1/teacher/class/students/{student_id}` | Update Class Student | teacher |
| GET | `/api/v1/teacher/classes` | My Classes | teacher |
| GET | `/api/v1/teacher/dashboard` | Dashboard | teacher |
| GET | `/api/v1/teacher/family-messages` | Family Messages | teacher |
| POST | `/api/v1/teacher/family-messages` | Send Family Message | teacher |
| GET | `/api/v1/teacher/profile` | Profile | teacher |
| PATCH | `/api/v1/teacher/profile` | Save Profile | teacher |
| GET | `/api/v1/teacher/students` | All Students | teacher |
| GET | `/health` | Health (루트) | — |
