# 코스 결제 연동 — 카카오페이 QR + 토스페이먼츠

## 원칙

- 결제 금액은 프런트 입력이 아니라 `courses.price`와 유효한 `sale_price`로 서버가 결정한다.
- 주문 생성 시 `course_orders.amount`에 금액을 저장하고 승인 응답의 주문번호·금액·상태와 대조한다.
- 카드번호, CVC, 생년월일은 CatChap API와 DB에서 받거나 저장하지 않는다.
- 운영 환경에서 PG 키가 빠져도 모의 결제로 성공 처리하지 않는다.
- 결제 완료와 수강권 활성화, 결제 취소와 수강권 회수는 각각 같은 DB 트랜잭션으로 반영한다.

## 환경변수

```dotenv
PAYMENT_MOCK_ENABLED=false

TOSS_CLIENT_KEY=
TOSS_SECRET_KEY=

KAKAOPAY_CID=
KAKAOPAY_SECRET_KEY=
KAKAOPAY_CID_SECRET=

PAYMENT_SUCCESS_URL=https://www.catchap5.com/student/payment/success
PAYMENT_FAIL_URL=https://www.catchap5.com/student/payment/fail
PAYMENT_CANCEL_URL=https://www.catchap5.com/student/payment/cancel
```

- `TOSS_CLIENT_KEY`만 프런트 결제 SDK 초기화에 사용한다.
- `TOSS_SECRET_KEY`, `KAKAOPAY_CID`, `KAKAOPAY_SECRET_KEY`,
  `KAKAOPAY_CID_SECRET`은 백엔드 전용이며 프런트 응답에 포함하지 않는다.
- `PAYMENT_MOCK_ENABLED`는 로컬 개발 편의용이다. `ENV=production`에서는 값과 무관하게
  서버가 mock을 비활성화한다.

## 공통 준비

1. 강사가 `PUT /api/v1/ops/courses/{course_id}/pricing`으로 가격을 설정한다.
2. 학생이 `GET /api/v1/courses/{course_id}/checkout`으로 금액과 사용 가능한 PG를 조회한다.
3. 학생이 `POST /api/v1/payments/checkout`으로 provider별 pending 주문을 생성한다.

주문 생성 예시:

```json
{
  "course_id": "course-uuid",
  "provider": "kakaopay"
}
```

`provider`는 `toss`, `kakaopay`, 개발 환경의 `mock` 중 하나다.

## 카카오페이 QR

1. `POST /api/v1/payments/kakaopay/ready`에 `order_uid`를 보낸다.
2. 응답의 `next_redirect_pc_url`을 새 창 또는 레이어로 연다.
3. 카카오페이 화면이 PC에서 QR을 표시한다.
4. 사용자가 휴대폰으로 인증하면 카카오페이가 백엔드 `approval_url`에 `pg_token`을 전달한다.
5. 백엔드는 저장한 TID·주문번호·학생 식별자와 `pg_token`으로 approve API를 호출한다.
6. 승인 응답의 TID·주문번호·금액을 검증한 뒤 주문을 paid로, 수강권을 active로 만든다.

ready 호출마다 추측 불가능한 state를 만들고 DB에는 SHA-256 해시만 저장한다. 카카오페이
리다이렉트가 가진 state와 일치해야 승인·취소·실패 콜백을 처리한다.

## 토스페이먼츠

1. 주문 생성 응답의 `toss_client_key`, `customer_key`, `order_uid`, `amount`로 프런트 SDK를 호출한다.
2. 결제 인증 성공 시 프런트가 받은 `paymentKey`, `orderId`, `amount`를
   `POST /api/v1/payments/confirm`에 전달한다.
3. 백엔드는 DB 주문 금액과 먼저 비교한 후 토스 승인 API를 호출한다.
4. 승인 응답의 `paymentKey`, `orderId`, `totalAmount`, `status=DONE`을 다시 검증한다.

토스 웹훅 주소:

```text
POST https://api.catchap5.com/api/v1/payments/webhooks/toss
```

일반 결제 상태 웹훅 본문은 단독으로 신뢰하지 않는다. 웹훅의 `paymentKey`로 토스 API를
재조회하고 서버 주문과 일치하는 결과만 반영한다.

## 조회와 취소

- `GET /api/v1/payments/{order_uid}`: 본인 주문 상태·영수증 조회
- `POST /api/v1/payments/{order_uid}/cancel`: 본인 paid 주문 전액 취소

취소 요청은 PG 취소가 성공한 뒤에만 로컬 주문을 `refunded`로 바꾼다. 같은 코스에 다른
정상 결제가 없다면 `course_enrollments.status`를 `withdrawn`으로 바꿔 수강권을 회수한다.

## 배포

```bash
alembic upgrade head
```

마이그레이션 `course_payment_pg_02`는 코스 가격 필드, 카카오 콜백 state 해시, 임시 redirect
세션, 영수증 URL, 취소 시각·사유와 PG 결제 식별값 조회 인덱스를 추가한다.
