"""Gmail SMTP 발송 — 계정 미설정 시 콘솔 dry-run 모드.

계정/비밀번호는 .env에서만 읽는다 (하드코딩 금지).
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import EmailLog

logger = logging.getLogger("catchap.email")
settings = get_settings()

TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_template(template_name: str, **kwargs: str) -> str:
    """템플릿 변수 kwargs에 'name'이 올 수 있어 파일명 파라미터는 template_name 사용"""
    html = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
    for key, value in kwargs.items():
        html = html.replace("{{ " + key + " }}", value)
    return html


def send_email(db: Session, to_email: str, subject: str, html: str, user_id: str | None = None) -> bool:
    # 헤더 인젝션 방지: 제목의 CR/LF 제거 (향후 사용자 입력이 subject로 올 경우 대비)
    subject = subject.replace("\r", " ").replace("\n", " ")
    log = EmailLog(user_id=user_id, to_email=to_email, subject=subject)

    if not settings.smtp_enabled:
        # dry-run: SMTP 미설정 시 발송 흔적만 남긴다.
        # 본문(인증코드/재설정 토큰 포함)은 개발 환경에서만 콘솔에 노출한다.
        logger.warning("[EMAIL DRY-RUN] to=%s subject=%s", to_email, subject)
        if not settings.is_production:
            # 개발용: 인증코드/재설정코드가 HTML 하단(약 1000번째 글자)에 있어
            # 잘라내면 코드가 안 보인다 → 본문 전체를 출력한다.
            print(f"\n===== EMAIL DRY-RUN =====\nTO: {to_email}\nSUBJECT: {subject}\n{html}\n=========================\n")
        else:
            logger.error("SMTP 미설정 상태로 프로덕션에서 메일 발송 시도 — 발송되지 않음 to=%s", to_email)
        log.status = "dry_run"
        db.add(log)
        db.commit()
        return True

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.MAIL_FROM_NAME} <{settings.MAIL_FROM or settings.SMTP_USER}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_APP_PASSWORD)
            server.sendmail(msg["From"], [to_email], msg.as_string())
        log.status = "sent"
        db.add(log)
        db.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("SMTP send failed: %s", exc)
        log.status = "failed"
        log.error_message = str(exc)
        db.add(log)
        db.commit()
        return False
