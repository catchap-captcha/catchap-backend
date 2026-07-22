"""인앱 알림 + 이메일 발송 — 오래 걸리는 백그라운드 작업(문항 생성 등) 완료를 사용자에게 알린다.

왜(팀 학습용): 문항 자동 생성은 STT 전사 때문에 수 분 걸리는 비동기 잡이라, 강사가 생성만
눌러두고 콘솔을 떠날 수 있다. 떠난 강사가 완료를 알려면 (1) 다시 들어와 폴링하거나 (2) 알림을
받아야 한다. 이 서비스가 (2)를 담당한다 — 인앱 알림(콘솔 벨)과 이메일을 함께 보낸다.

정직성 규약: 이메일 실패는 send_email이 False를 돌려주고 EmailLog에 남긴다(가짜 성공 없음).
인앱 알림은 이메일과 독립이라, 메일이 실패해도 콘솔 벨에는 뜬다. 호출자(백그라운드 잡)를
오염시키지 않도록, 알림 실패가 잡을 error로 뒤집지 않게 호출부에서 try로 감싼다.
"""
import logging

from sqlalchemy.orm import Session

from app.email.smtp import send_email
from app.models import Notification, User

_log = logging.getLogger(__name__)


def notify_user(
    db: Session,
    user_id: str,
    *,
    type: str,
    title: str,
    message: str,
    category: str = "일반",
    email_html: str | None = None,
    send_mail: bool = True,
) -> Notification:
    """user_id에게 인앱 알림을 만들고(항상), send_mail이면 그 사용자 이메일로도 보낸다.

    반환: 생성된 Notification. 이메일은 부수효과(성공/실패는 EmailLog·로그에 남음).
    commit은 이 함수가 한다(백그라운드 잡이 자기 세션으로 호출)."""
    n = Notification(
        user_id=user_id, type=type, category=category, title=title, message=message
    )
    db.add(n)
    db.commit()

    if send_mail:
        user = db.get(User, user_id)
        if user is not None and user.email:
            body = email_html or f"<p>{message}</p>"
            # send_email은 예외를 자체적으로 잡아 False를 돌려준다(여기서 raise 안 됨).
            ok = send_email(db, user.email, title, body, user_id=user_id)
            if not ok:
                _log.warning("알림 이메일 미발송(SMTP 실패/미설정) user=%s", user_id)
    return n
