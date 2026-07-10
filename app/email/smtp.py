"""Gmail SMTP 발송 — 계정 미설정 시 콘솔 dry-run 모드.

계정/비밀번호는 .env에서만 읽는다 (하드코딩 금지).
"""

import logging
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import unescape
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import EmailLog

logger = logging.getLogger("catchap.email")
settings = get_settings()

TEMPLATE_DIR = Path(__file__).parent / "templates"

# 모든 발송 메일 하단에 붙는 발신전용(회신 불가) 안내. send_email 한 곳에서 붙여
# 템플릿·인라인 본문 전부에 일관되게 적용한다.
_NO_REPLY_FOOTER = (
    "<hr style='border:none;border-top:1px solid #eee;margin:24px 0 12px'>"
    "<p style='font-size:12px;color:#999;line-height:1.6;font-family:sans-serif'>"
    "본 메일은 발신 전용 주소에서 발송되었습니다.<br><br>"
    "이 주소로 회신하신 메일은 확인되지 않으며, 별도의 답변을 드릴 수 없습니다.<br>"
    "문의가 필요하신 경우 CatChap 홈페이지의 문의하기를 이용해 주세요."
    "</p>"
)


def _finalize_html(body_html: str) -> str:
    """발신전용 푸터를 붙이고 완전한 HTML 문서 형태로 만든다.

    조각(<div>…)만 오면 <html><body>로 감싸고, 완전한 문서면 </body> 앞에 푸터를 넣는다.
    (일부 스팸필터가 <html> 태그 없는 메일을 감점하는 것을 막기 위함)
    """
    m = re.search(r"</body\s*>", body_html, re.IGNORECASE)
    if m:
        return body_html[: m.start()] + _NO_REPLY_FOOTER + body_html[m.start() :]
    return (
        '<!doctype html><html lang="ko"><body style="margin:0;padding:0">'
        + body_html
        + _NO_REPLY_FOOTER
        + "</body></html>"
    )


def _html_to_text(html: str) -> str:
    """HTML을 대략적인 평문으로 변환 (text/plain 대체 파트용 — HTML 버전과 내용이 유사해야 감점이 없다)."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    # 링크는 '텍스트 (URL)'로 살린다 — 초대 수락처럼 버튼이 본문의 전부인 메일에서
    # 텍스트 전용 클라이언트가 URL을 아예 못 받는 문제 방지
    text = re.sub(
        r'(?is)<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        lambda m: f"{re.sub(r'(?is)<[^>]+>', '', m.group(2)).strip()} ({m.group(1)})",
        text,
    )
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-6]|tr|li)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def render_template(template_name: str, **kwargs: str) -> str:
    """템플릿 변수 kwargs에 'name'이 올 수 있어 파일명 파라미터는 template_name 사용"""
    html = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
    for key, value in kwargs.items():
        html = html.replace("{{ " + key + " }}", value)
    return html


def send_email(db: Session, to_email: str, subject: str, html: str, user_id: str | None = None) -> bool:
    # 헤더 인젝션 방지: 제목의 CR/LF 제거 (향후 사용자 입력이 subject로 올 경우 대비)
    subject = subject.replace("\r", " ").replace("\n", " ")
    # 발신전용 안내 부착 + 완전한 HTML 문서화 (dry-run 콘솔 출력에도 동일하게 보이도록 여기서 처리)
    html = _finalize_html(html)
    log = EmailLog(user_id=user_id, to_email=to_email, subject=subject)

    if not settings.smtp_enabled:
        # dry-run은 개발(ENV=dev)에서만 허용. 프로덕션은 config fail-fast가 부팅을 막지만,
        # 만에 하나 이 경로에 도달하면 '발송된 척(return True)' 하지 않고 실패로 정직히 처리한다.
        if settings.is_production:
            logger.error("SMTP 미설정 상태로 프로덕션에서 메일 발송 시도 — 발송되지 않음 to=%s", to_email)
            log.status = "failed"
            log.error_message = "SMTP not configured in production"
            db.add(log)
            db.commit()
            return False
        # 개발용: 인증코드/재설정코드가 HTML 하단(약 1000번째 글자)에 있어
        # 잘라내면 코드가 안 보인다 → 본문 전체를 콘솔에 출력한다.
        logger.warning("[EMAIL DRY-RUN] to=%s subject=%s", to_email, subject)
        print(f"\n===== EMAIL DRY-RUN =====\nTO: {to_email}\nSUBJECT: {subject}\n{html}\n=========================\n")
        log.status = "dry_run"
        db.add(log)
        db.commit()
        return True

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.MAIL_FROM_NAME} <{settings.MAIL_FROM or settings.SMTP_USER}>"
    msg["To"] = to_email
    # 발신전용: 답장은 no-reply 주소로 향하게 한다 (그 주소는 워크스페이스에서 자동삭제/자동회신 처리)
    if settings.MAIL_REPLY_TO:
        msg["Reply-To"] = settings.MAIL_REPLY_TO
    # text/plain 대체 파트 먼저, HTML 나중 (alternative는 마지막이 우선 표시).
    # text 파트가 없거나 HTML과 크게 다르면 스팸필터가 감점한다.
    msg.attach(MIMEText(_html_to_text(html), "plain", "utf-8"))
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
