"""시스템 경보 수신 — Alertmanager가 보낸 경보를 운영자에게 전달한다.

경로 하나:
- POST /internal/alerts : Alertmanager 웹훅. X-Metrics-Token으로 인증(에이전트 인제스트와 같은
  토큰 — 둘 다 '클러스터 안에서 우리가 밀어넣는다'는 같은 신뢰 경계다).

왜(팀 학습용): 지표는 이미 다 모으고 있었지만 임계를 넘어도 ★아무도 알려주지 않았다 —
사람이 화면을 열어야만 알 수 있었다. Alertmanager가 판단은 하는데 받는 곳이 'null'이라
전부 버려지고 있었다. 이 엔드포인트가 그 받는 곳이 된다.

★왜 새 테이블을 만들지 않았나 — 콘솔 벨(Notification)이 이미 있고 조회 API도 있다.
경보를 운영자 알림으로 넣으면 ★콘솔에 그대로 뜨고 이력도 남는다. 새 테이블을 만들면
마이그레이션이 필요한데, 이 저장소는 alembic head가 3개고 프로덕션 DB가 그보다 뒤에 있다
(0806 실측: 적용 판본 captcha_purge_01). 없어도 되는 위험은 만들지 않는다.

★왜 백엔드를 거치나(Alertmanager가 직접 메일 보내면 되는데) — 받는 사람이 ★운영자 명단을
따라가야 하기 때문이다. Alertmanager 설정은 K8s 파일이라 DB를 못 읽는다. 여기를 거치면
운영자가 들어오고 나가는 것이 즉시 반영되고, 사람마다 수신을 끌 수도 있다.

⚠️★그 대가 — 백엔드가 죽으면 이 경로도 같이 죽는다. 그런데 '백엔드가 죽었다'가 제일 알아야
할 경보다. 그래서 ★급함(critical)은 Alertmanager가 ★직접 메일도 보내도록 이중으로 뒀다
(k8s/71-kube-prometheus-stack-values.yaml 의 alertmanager.config).
"""

import logging
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models import Notification, User, UserSetting
from app.services import notify_service

_log = logging.getLogger(__name__)

router = APIRouter()

# 알림 type — 콘솔 벨이 아이콘·필터에 쓴다. Notification.type은 String(30).
ALERT_TYPE = "시스템경보"
ALERT_CATEGORY = "운영"

# 메일까지 보낼 심각도. info는 잡음이 많아 콘솔 벨에만 남긴다(사용자 결정 0806).
EMAIL_SEVERITIES = {"critical", "warning"}

# 심각도 → 사람 말. Alertmanager 라벨 값 그대로 두면 운영자가 읽기 어렵다.
SEVERITY_KO = {"critical": "급함", "warning": "경고", "info": "참고", "none": "안내"}

# 같은 제목이 이 시간 안에 또 오면 건너뛴다.
# ★왜 필요한가 — Alertmanager는 응답이 2xx가 아니면 ★재전송한다. 우리가 느려서 타임아웃이
# 나면 같은 경보가 여러 번 들어와 운영자 6명 × N통이 된다. 반복 주기(1h/12h)로도 막히지만
# 재전송은 그보다 짧으므로 여기서 한 겹 더 막는다.
DEDUP_WINDOW_MIN = 10

# 한 번에 알릴 수 있는 최대 인원 — 명단이 잘못 커졌을 때 메일 폭탄을 막는 안전핀.
# 넘으면 보내지 않고 로그만 남긴다(조용히 일부만 보내면 '받은 줄 알았는데 안 온' 상황이 된다).
MAX_RECIPIENTS = 30


class AlertItem(BaseModel):
    """Alertmanager 웹훅의 alerts[] 한 건. 우리가 쓰는 것만 받는다(나머지는 무시)."""

    status: str = "firing"  # firing | resolved
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: str | None = None  # noqa: N815 — Alertmanager가 이 이름으로 보낸다
    fingerprint: str | None = None


class AlertmanagerPayload(BaseModel):
    status: str = "firing"
    alerts: list[AlertItem] = Field(default_factory=list)
    externalURL: str | None = None  # noqa: N815 — 위와 같은 이유


def _severity(alerts: list[AlertItem]) -> str:
    """묶음의 대표 심각도 — 가장 급한 것을 따른다(급한 하나가 경고 여럿에 묻히지 않게)."""
    order = ["critical", "warning", "info", "none"]
    found = {a.labels.get("severity", "none") for a in alerts}
    for s in order:
        if s in found:
            return s
    return "none"


# 남이 만든(kube-prometheus-stack 기본) 경보 중 ★실제로 자주 뜨는 것만 이름을 옮긴다.
# 141종을 다 옮기지는 않는다 — 대부분은 한 번도 안 뜨고, 아래 _title 이 규칙에 붙은
# summary 를 먼저 쓰기 때문에 이름이 없어도 뜻은 전달된다.
# ★우리가 만든 Catchap* 도 넣는다 — 새 경보는 summary(한글)를 쓰지만, ★0816 전에 저장된
#   기록에는 제목에 규칙 이름만 남아 있어서 표로만 되돌릴 수 있다(localize_stored_title).
_HANGUL = re.compile(r"[가-힣]")
# 저장된 제목 "[급함] CatchapServiceDown — …" 에서 ★맨 앞 ASCII 토큰이 규칙 이름이다.
# (?![가-힣]) — 바로 뒤에 한글이 붙으면 우리가 붙인 이름이므로 손대지 않는다.
_STORED_NAME_RE = re.compile(r"^(\[[^\]]*\]\s*)([A-Za-z][A-Za-z0-9_]*)(?![가-힣])")

ALERT_NAME_KO = {
    "CatchapServiceDown": "서비스가 한 벌도 안 떠 있음",
    "CatchapPodRestartingRepeatedly": "앱이 자꾸 다시 뜨는 중",
    "CPUThrottlingHigh": "CPU 가 한도에 걸려 느려지는 중",
    "KubePodCrashLooping": "앱이 뜨자마자 죽기를 반복",
    "KubePodNotReady": "앱이 준비 상태가 되지 못함",
    "KubeContainerWaiting": "앱이 시작하지 못하고 대기 중",
    "KubeDeploymentReplicasMismatch": "떠 있어야 할 벌 수를 못 채움",
    "KubeNodeNotReady": "서버가 준비 상태가 아님",
    "KubeNodeUnreachable": "서버에 연결되지 않음",
    "KubeletDown": "서버 관리 프로그램이 응답하지 않음",
    "TargetDown": "감시 대상이 응답하지 않음",
    "NodeFilesystemAlmostOutOfSpace": "서버 저장공간이 거의 다 참",
    "NodeFilesystemSpaceFillingUp": "서버 저장공간이 빠르게 차는 중",
    "NodeMemoryHighUtilization": "서버 메모리 사용이 높음",
    "NodeCPUHighUsage": "서버 CPU 사용이 높음",
    "KubePersistentVolumeFillingUp": "디스크(볼륨)가 거의 다 참",
    "Watchdog": "감시 장치 살아 있음 확인(늘 켜져 있는 신호)",
}


def _headline(first: "AlertItem", name: str) -> str:
    """제목에 쓸 한 줄 — ★규칙 이름(CatchapServiceDown)보다 사람이 읽는 말을 먼저 쓴다.

    경보 규칙에는 summary 어노테이션이 붙어 있고, 우리가 만든 Catchap* 는 그것이
    한글이다("★catchap 지표가 10분째 안 들어옵니다"). 그런데 그전에는 이 값을
    ★메일 본문에만 쓰고 화면 제목에는 규칙 이름을 그대로 찍어서,
    운영자에게 "CatchapServiceDown — monitoring-kube-state-metrics-6cb…" 로 보였다.

    summary 가 없거나 영문뿐이면 위 표의 한글 이름으로, 그것도 없으면 규칙 이름 그대로.
    """
    summary = (first.annotations.get("summary") or "").strip()
    # ★주석은 "영문뿐이면 표를 쓴다" 였는데 코드가 그 검사를 안 했다(0816 실측) —
    #   남이 만든 규칙(kube-prometheus-stack)은 summary 가 영문이라
    #   "Processes experience elevated CPU throttling." 이 그대로 제목이 됐다.
    if summary and _HANGUL.search(summary):
        return summary
    return ALERT_NAME_KO.get(name, name)


def localize_stored_title(title: str) -> str:
    """★이미 저장된 경보 제목을 ★보여 줄 때 사람 말로 바꾼다.

    제목은 경보를 ★받을 때 만들어 저장한다(_title). 그래서 이 표를 넣기 전(0816)에
    쌓인 기록은 규칙 이름이 영문 그대로다 — 화면의 15건이 전부 그랬다.

        [급함] CatchapServiceDown — monitoring-kube-state-metrics-6cb6769d69-przfr 외 1건
        [참고] CPUThrottlingHigh — monitoring-prometheus-node-exporter-cgs55

    ★저장된 값은 건드리지 않는다 — 기록은 온 그대로 남아야 한다. 읽을 때만 바꾼다.
    ⚠️규칙 이름 바로 뒤에 한글이 붙은 것(CatChap경보시험-0813)은 우리가 붙인 이름이라
      건드리지 않는다.
    """
    m = _STORED_NAME_RE.match(title)
    if not m:
        return title
    ko = ALERT_NAME_KO.get(m.group(2))
    return f"{m.group(1)}{ko}{title[m.end():]}" if ko else title


def _title(payload: AlertmanagerPayload) -> str:
    """콘솔 벨과 메일 제목에 쓸 한 줄. Notification.title은 String(150)이라 잘라 넣는다."""
    first = payload.alerts[0]
    name = first.labels.get("alertname", "알 수 없는 경보")
    where = first.labels.get("pod") or first.labels.get("deployment") or first.labels.get("instance") or ""
    tag = SEVERITY_KO.get(_severity(payload.alerts), "안내")
    state = "해제" if payload.status == "resolved" else tag
    more = f" 외 {len(payload.alerts) - 1}건" if len(payload.alerts) > 1 else ""
    head = _headline(first, name)
    # 어디서 난 것인지가 이미 머리말에 들어 있으면(우리 경보는 summary 에 파드/배포 이름을
    # 넣는다) 같은 말을 두 번 붙이지 않는다.
    tail = f" — {where}" if where and where not in head else ""
    return f"[{state}] {head}{tail}{more}"[:150]


def _message(payload: AlertmanagerPayload) -> str:
    """본문 — 무엇이 왜 났고 어디를 보면 되는지. 운영자가 이것만 보고 움직일 수 있어야 한다."""
    lines: list[str] = []
    if payload.status == "resolved":
        lines.append("아래 경보가 해제되었습니다. 조치가 필요하지 않습니다.")
        lines.append("")
    for a in payload.alerts:
        name = a.labels.get("alertname", "?")
        sev = SEVERITY_KO.get(a.labels.get("severity", "none"), "안내")
        summary = a.annotations.get("summary") or name
        desc = a.annotations.get("description") or ""
        where = ", ".join(
            f"{k}={v}"
            for k, v in a.labels.items()
            if k in ("namespace", "pod", "deployment", "instance", "reason")
        )
        lines.append(f"· [{sev}] {summary}")
        if where:
            lines.append(f"  대상: {where}")
        if desc:
            lines.append(f"  {desc.strip()}")
        if a.startsAt:
            lines.append(f"  시작: {a.startsAt}")
        lines.append("")
    lines.append("그라파나 「CatChap 한눈에」와 운영 콘솔의 서버 상태에서 추이를 볼 수 있습니다.")
    return "\n".join(lines).strip()


# 등급별 색 — 메일에서 ★한눈에 급한 정도를 알아보게. (배경, 글자, 띠)
_TONE = {
    "critical": ("#fee2e2", "#b91c1c", "#dc2626"),
    "warning": ("#fef3c7", "#92400e", "#f59e0b"),
    "info": ("#f3f4f6", "#4b5563", "#9ca3af"),
    "none": ("#f3f4f6", "#4b5563", "#9ca3af"),
    "resolved": ("#dcfce7", "#166534", "#16a34a"),
}

# 라벨 이름 → 사람 말. 영어 키를 그대로 두면 메일에서 읽는 데 시간이 걸린다.
_LABEL_KO = {
    "namespace": "묶음",
    "pod": "파드",
    "deployment": "서비스",
    "instance": "서버",
    "reason": "이유",
    "container": "컨테이너",
}


def _esc(s: str) -> str:
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _email_html(payload: AlertmanagerPayload) -> str:
    """메일 본문 — ★훑어보고 바로 판단할 수 있게.

    ★왜 따로 만드나 — notify_user 는 email_html 이 없으면 본문을 <p> 하나로 감싼다.
    그런데 우리 본문은 여러 줄이고, HTML 에서 줄바꿈은 ★무시된다. 그래서 메일이
    한 덩어리 글로 보여 무엇이 급한지 알아보기 어려웠다(0806 지적).

    메일 클라이언트는 <style> 블록을 자주 지운다 — 그래서 ★인라인 스타일만 쓴다.
    """
    sev = "resolved" if payload.status == "resolved" else _severity(payload.alerts)
    bg, fg, bar = _TONE.get(sev, _TONE["none"])
    head = "해제됨" if payload.status == "resolved" else SEVERITY_KO.get(sev, "안내")
    lead = (
        "아래 문제가 풀렸습니다. 조치하지 않으셔도 됩니다."
        if payload.status == "resolved"
        else "아래 문제가 감지되었습니다."
    )

    cards: list[str] = []
    for a in payload.alerts:
        name = a.labels.get("alertname", "?")
        summary = _esc(a.annotations.get("summary") or name)
        desc = _esc((a.annotations.get("description") or "").strip())
        chips = "".join(
            f'<span style="display:inline-block;background:#f3f4f6;color:#374151;'
            f'border-radius:5px;padding:2px 7px;margin:0 5px 5px 0;font-size:12px">'
            f"{_esc(_LABEL_KO.get(k, k))} <b>{_esc(v)}</b></span>"
            for k, v in a.labels.items()
            if k in _LABEL_KO
        )
        started = (
            f'<div style="margin-top:6px;font-size:12px;color:#9ca3af">시작 {_esc(a.startsAt)}</div>'
            if a.startsAt
            else ""
        )
        cards.append(
            f'<div style="border:1px solid #e5e7eb;border-left:4px solid {bar};'
            f'border-radius:8px;padding:14px 16px;margin:0 0 10px">'
            f'<div style="font-size:15px;font-weight:700;color:#111827;line-height:1.5">{summary}</div>'
            f'{f"<div style=\'margin-top:8px\'>{chips}</div>" if chips else ""}'
            f'{f"<div style=\'margin-top:8px;font-size:13px;color:#4b5563;line-height:1.65\'>{desc}</div>" if desc else ""}'
            f"{started}</div>"
        )

    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Malgun Gothic\',sans-serif;'
        'max-width:640px;margin:0 auto;padding:20px 16px;color:#111827">'
        # ★맨 위 띠 — 색과 글자 둘 다로 급한 정도를 알린다(색만 쓰면 못 보는 사람이 생긴다)
        f'<div style="background:{bg};border-radius:10px;padding:14px 16px;margin-bottom:16px">'
        f'<span style="display:inline-block;background:{fg};color:#fff;border-radius:6px;'
        f'padding:3px 10px;font-size:13px;font-weight:700">{head}</span>'
        f'<span style="margin-left:10px;font-size:14px;color:{fg}">{lead}</span>'
        f'<div style="margin-top:6px;font-size:13px;color:{fg}">모두 {len(payload.alerts)}건</div>'
        "</div>"
        + "".join(cards)
        + '<div style="margin-top:16px;padding-top:14px;border-top:1px solid #e5e7eb;'
        'font-size:12px;color:#6b7280;line-height:1.7">'
        "운영 콘솔의 <b>시스템 경보</b>에서 지난 경보를 모아 볼 수 있고, 그 화면에서 "
        "<b>메일 수신을 끄고 켤 수</b> 있습니다.<br>"
        "추이는 그라파나 <b>「CatChap 한눈에」</b>와 운영 콘솔 <b>모니터링</b>에서 봅니다."
        "</div></div>"
    )


def _wants_email(db: Session, user_id: str) -> bool:
    """이 운영자가 경보 메일을 받기로 했나 — 설정이 없으면 ★받는 것이 기본이다.

    설정 화면(PUT /settings/me)에서 {"alerts": {"email": false}} 로 끄면 콘솔 벨에만 남는다.
    ★기본을 '받음'으로 두는 이유 — 안 받는 것이 기본이면 아무도 설정을 만지지 않아
    경보가 조용히 아무 데도 안 가는 상태가 된다.
    """
    row = (
        db.query(UserSetting)
        .filter(UserSetting.subject_type == "user", UserSetting.subject_id == user_id)
        .first()
    )
    if row is None or not row.settings:
        return True
    alerts = row.settings.get("alerts")
    if not isinstance(alerts, dict):
        return True
    return alerts.get("email", True) is not False


def _recipients(db: Session) -> list[User]:
    """알릴 운영자 — 활성 상태이고 메일 주소가 있는 ops 계정.

    ⚠️정지(disabled)·대기(pending) 계정은 뺀다. 퇴사자에게 계속 경보가 가지 않게.
    """
    return (
        db.query(User)
        .filter(User.role == "ops", User.status == "active", User.email.isnot(None))
        .all()
    )


def _already_sent(db: Session, user_id: str, title: str) -> bool:
    """최근 DEDUP_WINDOW_MIN 안에 같은 제목을 이미 보냈나(= Alertmanager 재전송)."""
    since = datetime.now() - timedelta(minutes=DEDUP_WINDOW_MIN)
    return (
        db.query(Notification.id)
        .filter(
            Notification.user_id == user_id,
            Notification.type == ALERT_TYPE,
            Notification.title == title,
            Notification.created_at >= since,
        )
        .first()
        is not None
    )


def _authorized(secret: str, x_metrics_token: str, authorization: str) -> bool:
    """토큰이 맞나 — 두 가지 형태를 받는다.

    ★왜 둘인가 — Alertmanager 웹훅은 ★임의 헤더를 넣을 수 없다(http_config가 basic_auth·
    authorization·oauth2만 지원). 그래서 표준 `Authorization: Bearer <토큰>`을 같이 받는다.
    사람이 손으로 부를 때는 다른 인제스트 경로와 같은 X-Metrics-Token이 편하다.

    ⚠️토큰이 비어 있으면 ★어느 쪽도 통과시키지 않는다 — 빈 값끼리 맞아떨어져서
    아무나 가짜 경보를 넣는 구멍이 생기지 않게.
    """
    if not secret:
        return False
    if x_metrics_token and x_metrics_token == secret:
        return True
    scheme, _, credentials = authorization.partition(" ")
    return scheme.lower() == "bearer" and credentials.strip() == secret


@router.post("/internal/alerts")
def ingest_alerts(
    payload: AlertmanagerPayload,
    x_metrics_token: str = Header(default=""),
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Alertmanager 웹훅 — 경보를 운영자 전원의 콘솔 벨(+메일)로 보낸다.

    ★항상 2xx로 답하려 애쓴다(받는 사람이 0명이어도). 5xx를 주면 Alertmanager가 재전송하는데,
    '보낼 사람이 없다'는 재전송해도 달라지지 않는다. 다만 ★인증 실패는 403으로 분명히 막는다.
    """
    if not _authorized(get_settings().METRICS_INGEST_TOKEN, x_metrics_token, authorization):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="경보 수신 인증 실패")

    if not payload.alerts:
        return {"ok": True, "notified": 0, "note": "경보가 비어 있음"}

    sev = _severity(payload.alerts)
    title = _title(payload)
    message = _message(payload)   # 콘솔 벨에 뜰 평문
    html = _email_html(payload)   # ★메일에 갈 형태 — 없으면 여러 줄이 한 덩어리로 보인다
    # 해제 알림은 메일까지 보내지 않는다 — 급한 일이 끝났다는 소식이라 벨로 충분하다.
    mail_ok = sev in EMAIL_SEVERITIES and payload.status != "resolved"

    people = _recipients(db)
    if len(people) > MAX_RECIPIENTS:
        _log.error(
            "경보 수신자가 %d명 — 상한 %d명을 넘어 보내지 않았습니다 (명단을 확인하세요)",
            len(people),
            MAX_RECIPIENTS,
        )
        return {"ok": True, "notified": 0, "note": f"수신자 과다({len(people)}명) — 미발송"}

    notified = 0
    skipped = 0
    for user in people:
        if _already_sent(db, user.id, title):
            skipped += 1
            continue
        try:
            notify_service.notify_user(
                db,
                user.id,
                type=ALERT_TYPE,
                category=ALERT_CATEGORY,
                title=title,
                message=message,
                email_html=html,
                send_mail=mail_ok and _wants_email(db, user.id),
            )
            notified += 1
        except Exception:  # 한 사람 실패가 나머지를 막지 않게 — 경보는 최대한 퍼져야 한다
            _log.exception("경보 알림 실패 user=%s title=%s", user.id, title)

    _log.warning("시스템 경보 수신: %s (알림 %d명·중복 %d건)", title, notified, skipped)
    return {"ok": True, "notified": notified, "deduped": skipped, "severity": sev}
