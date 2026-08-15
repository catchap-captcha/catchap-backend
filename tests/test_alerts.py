"""시스템 경보 수신 — Alertmanager 웹훅이 운영자에게 제대로 닿는지.

★여기서 지키려는 것은 셋이다.
  ① 아무나 못 넣는다 — 이 경로로 가짜 경보를 넣으면 운영자 전원에게 메일이 간다.
  ② ★조용해지지 않는다 — 중복 제거·수신 설정·상한 같은 '덜 보내는' 장치가
     실수로 ★전부를 막아 버리면, 경보가 안 오는데 아무도 모른다.
  ③ 덜 급한 것으로 잠을 깨우지 않는다 — 참고(info)와 해제 알림은 메일까지 가지 않는다.
"""

from datetime import datetime

from app.core.security import hash_password
from app.models import Notification, User, UserSetting
from tests.test_captcha_api import _ops, auth

# ⚠️영문·숫자만 — HTTP 헤더 값은 latin-1로만 실려서 한글을 넣으면 요청 자체가 못 나간다.
TOKEN = "test-alert-token-0806"


def _patch_token(monkeypatch, token: str = TOKEN):
    from app.api.v1.endpoints import alerts

    class _S:
        METRICS_INGEST_TOKEN = token

    monkeypatch.setattr(alerts, "get_settings", lambda: _S())


def _catch_mail(monkeypatch) -> list[tuple[str, str]]:
    """실제로 메일을 보내지 않고 (받는사람, 제목)만 모은다."""
    sent: list[tuple[str, str]] = []
    from app.services import notify_service

    def _fake(db, to_email, subject, html, user_id=None):  # noqa: ARG001
        sent.append((to_email, subject))
        return True

    monkeypatch.setattr(notify_service, "send_email", _fake)
    return sent


def _catch_mail_html(monkeypatch) -> list[str]:
    """메일 ★본문(HTML)만 모은다 — 생김새를 검사하려고."""
    bodies: list[str] = []
    from app.services import notify_service

    def _fake(db, to_email, subject, html, user_id=None):  # noqa: ARG001
        bodies.append(html)
        return True

    monkeypatch.setattr(notify_service, "send_email", _fake)
    return bodies


def _more_ops(db, n: int, *, status: str = "active", email: str | None = None) -> list[User]:
    made = []
    for i in range(n):
        u = User(
            email=email or f"ops{i}@t.dev",
            password_hash=hash_password("Password123!"),
            name=f"운영자{i}",
            role="ops",
            status=status,
            email_verified_at=datetime.utcnow(),
        )
        db.add(u)
        made.append(u)
    db.commit()
    return made


def _payload(*, severity="critical", status="firing", alertname="CatchapServiceDown", n=1):
    return {
        "status": status,
        "alerts": [
            {
                "status": status,
                "labels": {
                    "alertname": alertname,
                    "severity": severity,
                    "namespace": "catchap",
                    "deployment": f"backend-api{i or ''}",
                },
                "annotations": {
                    "summary": f"{alertname} 가 한 벌도 안 떠 있습니다",
                    "description": "이 서비스로 오는 요청이 전부 실패합니다.",
                },
                "startsAt": "2026-08-06T05:00:00Z",
                "fingerprint": f"fp{i}",
            }
            for i in range(n)
        ],
    }


def _post(client, body, token=TOKEN):
    return client.post("/api/v1/internal/alerts", json=body, headers={"X-Metrics-Token": token})


def _post_bearer(client, body, token=TOKEN):
    """Alertmanager가 실제로 쓰는 방식 — 임의 헤더를 못 넣어 Authorization으로 온다."""
    return client.post(
        "/api/v1/internal/alerts", json=body, headers={"Authorization": f"Bearer {token}"}
    )


# ─────────────────────────────────────────────────────────────
# ① 아무나 못 넣는다
# ─────────────────────────────────────────────────────────────


def test_wrong_token_is_rejected(client, db, monkeypatch):
    """★토큰이 틀리면 403 — 이 경로가 열려 있으면 가짜 경보로 운영자를 깨울 수 있다."""
    _patch_token(monkeypatch)
    _catch_mail(monkeypatch)
    _ops(client, db)
    assert _post(client, _payload(), token="wrong-token").status_code == 403
    assert db.query(Notification).count() == 0


def test_ingest_disabled_when_token_unset(client, db, monkeypatch):
    """토큰을 안 정해 둔 환경에서는 ★아무것도 받지 않는다(빈 토큰끼리 맞아떨어지지 않게)."""
    _patch_token(monkeypatch, token="")
    _catch_mail(monkeypatch)
    _ops(client, db)
    assert _post(client, _payload(), token="").status_code == 403
    assert _post_bearer(client, _payload(), token="").status_code == 403


def test_alertmanager_bearer_header_is_accepted(client, db, monkeypatch):
    """★Alertmanager가 쓰는 `Authorization: Bearer`로도 들어와야 한다.

    ⚠️이게 없으면 손으로 시험할 때는 되는데 ★실제 Alertmanager가 보내면 403이 된다
    (웹훅 설정에 임의 헤더를 넣을 수 없기 때문). 배포 후에야 드러나는 종류의 구멍이다.
    """
    _patch_token(monkeypatch)
    sent = _catch_mail(monkeypatch)
    _ops(client, db)

    r = _post_bearer(client, _payload())
    assert r.status_code == 200, r.text
    assert r.json()["notified"] == 1
    assert len(sent) == 1
    # 틀린 Bearer 는 막힌다
    assert _post_bearer(client, _payload(), token="wrong-token").status_code == 403
    # Bearer 가 아닌 방식도 막힌다
    assert client.post(
        "/api/v1/internal/alerts", json=_payload(), headers={"Authorization": TOKEN}
    ).status_code == 403


# ─────────────────────────────────────────────────────────────
# ② 실제로 닿는다
# ─────────────────────────────────────────────────────────────


def test_alert_reaches_every_active_ops(client, db, monkeypatch):
    """급함 경보는 활성 운영자 ★전원의 콘솔 벨과 메일로 간다."""
    _patch_token(monkeypatch)
    sent = _catch_mail(monkeypatch)
    _ops(client, db)  # ops@t.dev
    _more_ops(db, 2)  # ops0@t.dev, ops1@t.dev

    r = _post(client, _payload())
    assert r.status_code == 200, r.text
    assert r.json()["notified"] == 3
    assert r.json()["severity"] == "critical"

    rows = db.query(Notification).all()
    assert len(rows) == 3
    assert all(n.type == "시스템경보" for n in rows)
    assert rows[0].title.startswith("[급함] CatchapServiceDown")
    # 본문에 '무엇이 왜'가 들어 있어야 운영자가 이것만 보고 움직일 수 있다
    assert "한 벌도 안 떠 있습니다" in rows[0].message
    assert "요청이 전부 실패합니다" in rows[0].message
    assert {e for e, _ in sent} == {"ops@t.dev", "ops0@t.dev", "ops1@t.dev"}


def test_only_active_ops_with_email_are_notified(client, db, monkeypatch):
    """정지된 계정과 ★운영자가 아닌 사람에게는 가지 않는다."""
    _patch_token(monkeypatch)
    sent = _catch_mail(monkeypatch)
    _ops(client, db)
    _more_ops(db, 1, status="disabled", email="퇴사자@t.dev")
    db.add(
        User(
            email="teacher@t.dev",
            password_hash=hash_password("Password123!"),
            name="강사",
            role="instructor",
            status="active",
            email_verified_at=datetime.utcnow(),
        )
    )
    db.commit()

    assert _post(client, _payload()).json()["notified"] == 1
    assert {e for e, _ in sent} == {"ops@t.dev"}


# ─────────────────────────────────────────────────────────────
# ②-2 메일이 ★한눈에 들어와야 한다
# ─────────────────────────────────────────────────────────────


def test_mail_is_not_one_blob_of_text(client, db, monkeypatch):
    """★메일 본문이 글 한 덩어리면 안 된다.

    ⚠️email_html 을 안 넘기면 notify_user 가 본문을 <p> 하나로 감싸는데,
    우리 본문은 여러 줄이고 ★HTML 은 줄바꿈을 무시한다 — 그러면 무엇이 급한지
    알아볼 수 없는 한 덩어리가 된다(0806 지적). 그래서 형태를 검사한다.
    """
    _patch_token(monkeypatch)
    bodies = _catch_mail_html(monkeypatch)
    _ops(client, db)

    _post(client, _payload(n=2))
    assert len(bodies) == 1
    html = bodies[0]
    # 등급이 ★글자로도 있어야 한다 — 색만 쓰면 못 보는 사람이 생긴다
    assert "급함" in html
    # 경보마다 칸이 따로 있어야 한다(한 덩어리가 아니라는 뜻)
    assert html.count("border-left:4px solid") == 2
    # 라벨이 사람 말로 바뀌어야 한다
    assert "서비스" in html and "묶음" in html
    # ★인라인 스타일만 — 메일 클라이언트가 <style> 블록을 자주 지운다
    assert "<style" not in html
    # 어디를 보면 되는지가 있어야 한다
    assert "시스템 경보" in html


def test_mail_marks_resolved_differently(client, db, monkeypatch):
    """해제 메일은 ★다르게 보여야 한다 — 급한 것과 같은 색이면 또 놀란다."""
    from app.api.v1.endpoints import alerts

    html = alerts._email_html(
        alerts.AlertmanagerPayload(
            status="resolved",
            alerts=[alerts.AlertItem(labels={"alertname": "X", "severity": "critical"})],
        )
    )
    assert "해제됨" in html
    assert "#dcfce7" in html  # 초록 계열
    assert "조치하지 않으셔도" in html


def test_mail_escapes_values(client, db, monkeypatch):
    """라벨 값에 꺾쇠가 들어와도 ★메일이 깨지지 않는다."""
    from app.api.v1.endpoints import alerts

    html = alerts._email_html(
        alerts.AlertmanagerPayload(
            alerts=[
                alerts.AlertItem(
                    labels={"alertname": "X", "severity": "warning", "pod": "<img src=x>"},
                    annotations={"summary": "a & b"},
                )
            ]
        )
    )
    assert "<img src=x>" not in html
    assert "&lt;img src=x&gt;" in html
    assert "a &amp; b" in html


# ─────────────────────────────────────────────────────────────
# ③ 덜 급한 것으로 깨우지 않는다 — 단 벨에는 남는다
# ─────────────────────────────────────────────────────────────


def test_info_stays_in_console_only(client, db, monkeypatch):
    """참고(info)는 ★메일을 보내지 않는다. 그래도 콘솔 벨에는 남아야 한다.

    ⚠️여기서 '알림도 안 만든다'로 잘못 만들면, 나중에 왜 그랬는지 추적할 기록이 사라진다.
    """
    _patch_token(monkeypatch)
    sent = _catch_mail(monkeypatch)
    _ops(client, db)

    assert _post(client, _payload(severity="info")).json()["notified"] == 1
    assert db.query(Notification).count() == 1  # ★벨에는 남는다
    assert sent == []  # ★메일은 안 간다


def test_resolved_does_not_send_mail(client, db, monkeypatch):
    """해제 알림은 벨로 충분하다 — 급한 일이 끝났다는 소식으로 메일함을 채우지 않는다."""
    _patch_token(monkeypatch)
    sent = _catch_mail(monkeypatch)
    _ops(client, db)

    r = _post(client, _payload(status="resolved"))
    assert r.json()["notified"] == 1
    n = db.query(Notification).first()
    assert n.title.startswith("[해제]")
    assert "해제되었습니다" in n.message
    assert sent == []


def test_user_can_turn_off_alert_mail(client, db, monkeypatch):
    """설정에서 끈 사람은 ★벨에만 남는다. 끄지 않은 사람에게는 그대로 간다."""
    _patch_token(monkeypatch)
    sent = _catch_mail(monkeypatch)
    _ops(client, db)
    quiet = _more_ops(db, 1)[0]
    db.add(
        UserSetting(
            subject_type="user", subject_id=quiet.id, settings={"alerts": {"email": False}}
        )
    )
    db.commit()

    assert _post(client, _payload()).json()["notified"] == 2
    assert db.query(Notification).count() == 2  # ★둘 다 벨에는 뜬다
    assert {e for e, _ in sent} == {"ops@t.dev"}  # ★끈 사람만 메일이 빠진다


def test_unrelated_settings_do_not_silence_mail(client, db, monkeypatch):
    """★설정 행이 있어도 alerts 키가 없으면 그대로 받는다.

    ⚠️이걸 안 지키면 '설정을 한 번이라도 저장한 운영자'가 조용히 경보를 못 받게 된다.
    """
    _patch_token(monkeypatch)
    sent = _catch_mail(monkeypatch)
    _ops(client, db)
    u = db.query(User).filter(User.email == "ops@t.dev").first()
    db.add(UserSetting(subject_type="user", subject_id=u.id, settings={"twofa": True}))
    db.commit()

    assert _post(client, _payload()).json()["notified"] == 1
    assert {e for e, _ in sent} == {"ops@t.dev"}


# ─────────────────────────────────────────────────────────────
# ④ 재전송으로 도배되지 않는다 — 단 다른 경보는 막지 않는다
# ─────────────────────────────────────────────────────────────


def test_same_alert_resent_is_deduped(client, db, monkeypatch):
    """Alertmanager 재전송으로 같은 제목이 또 오면 ★한 번만 알린다."""
    _patch_token(monkeypatch)
    sent = _catch_mail(monkeypatch)
    _ops(client, db)

    assert _post(client, _payload()).json()["notified"] == 1
    again = _post(client, _payload())
    assert again.json()["notified"] == 0 and again.json()["deduped"] == 1
    assert db.query(Notification).count() == 1
    assert len(sent) == 1


def test_dedup_does_not_swallow_a_different_alert(client, db, monkeypatch):
    """★중복 제거가 ★다른 경보까지 막으면 안 된다 — 그러면 조용히 못 받는 상태가 된다."""
    _patch_token(monkeypatch)
    sent = _catch_mail(monkeypatch)
    _ops(client, db)

    assert _post(client, _payload(alertname="CatchapServiceDown")).json()["notified"] == 1
    assert _post(client, _payload(alertname="CatchapImagePullFailing")).json()["notified"] == 1
    assert db.query(Notification).count() == 2
    assert len(sent) == 2


# ─────────────────────────────────────────────────────────────
# ⑤ 가장자리
# ─────────────────────────────────────────────────────────────


def test_group_of_alerts_becomes_one_notification(client, db, monkeypatch):
    """묶음으로 온 경보 여러 건은 ★알림 하나로 합친다(운영자 6명 × N통이 되지 않게)."""
    _patch_token(monkeypatch)
    sent = _catch_mail(monkeypatch)
    _ops(client, db)

    assert _post(client, _payload(n=3)).json()["notified"] == 1
    n = db.query(Notification).first()
    assert "외 2건" in n.title
    assert n.message.count("· [급함]") == 3  # 본문에는 세 건이 다 있어야 한다
    assert len(sent) == 1


def test_highest_severity_wins_in_a_mixed_group(client, db, monkeypatch):
    """경고와 급함이 섞이면 ★급함으로 다룬다 — 급한 하나가 경고 여럿에 묻히지 않게."""
    _patch_token(monkeypatch)
    _catch_mail(monkeypatch)
    _ops(client, db)

    body = _payload(severity="warning", n=2)
    body["alerts"][1]["labels"]["severity"] = "critical"
    r = _post(client, body)
    assert r.json()["severity"] == "critical"
    assert db.query(Notification).first().title.startswith("[급함]")


def test_empty_alert_list_is_accepted_quietly(client, db, monkeypatch):
    """빈 묶음은 200으로 조용히 받는다 — 5xx를 주면 Alertmanager가 계속 재전송한다."""
    _patch_token(monkeypatch)
    _catch_mail(monkeypatch)
    _ops(client, db)

    r = _post(client, {"status": "firing", "alerts": []})
    assert r.status_code == 200 and r.json()["notified"] == 0
    assert db.query(Notification).count() == 0


def test_too_many_recipients_stops_the_send(client, db, monkeypatch):
    """명단이 상한을 넘으면 ★아무에게도 안 보낸다.

    ★일부만 보내면 '나는 받았으니 다들 받았겠지'가 되어 더 나쁘다. 로그로 드러내고 멈춘다.
    """
    from app.api.v1.endpoints import alerts

    _patch_token(monkeypatch)
    sent = _catch_mail(monkeypatch)
    monkeypatch.setattr(alerts, "MAX_RECIPIENTS", 2)
    _ops(client, db)
    _more_ops(db, 3)

    r = _post(client, _payload())
    assert r.status_code == 200 and r.json()["notified"] == 0
    assert "수신자 과다" in r.json()["note"]
    assert db.query(Notification).count() == 0 and sent == []


def test_one_failing_recipient_does_not_block_the_rest(client, db, monkeypatch):
    """한 사람에게 실패해도 ★나머지에게는 간다 — 경보는 최대한 퍼져야 한다."""
    from app.api.v1.endpoints import alerts
    from app.services import notify_service

    _patch_token(monkeypatch)
    _catch_mail(monkeypatch)
    _ops(client, db)
    _more_ops(db, 2)

    real = notify_service.notify_user
    calls = {"n": 0}

    def _flaky(db_, user_id, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("첫 사람에게서 터짐")
        return real(db_, user_id, **kw)

    monkeypatch.setattr(alerts.notify_service, "notify_user", _flaky)

    r = _post(client, _payload())
    assert r.status_code == 200
    assert r.json()["notified"] == 2  # ★셋 중 둘은 받았다


# ── 경보 제목이 읽히는 말인가 (0815 화면 지적) ────────────────────────────
# 그전에는 화면에 이렇게 떴다:
#   [급함] CatchapServiceDown — monitoring-kube-state-metrics-6cb6769d69-przfr 외 1건
# 규칙에 한글 summary 가 붙어 있는데도 ★메일 본문에만 쓰고 제목엔 규칙 이름을 찍었다.
def _title_payload(alerts, status="firing"):
    from app.api.v1.endpoints.alerts import AlertmanagerPayload

    return AlertmanagerPayload(status=status, alerts=alerts)


def test_title_uses_korean_summary_when_the_rule_has_one():
    """우리가 만든 경보는 summary 가 한글이다 — 규칙 이름 대신 그것을 쓴다."""
    from app.api.v1.endpoints.alerts import _title

    t = _title(_title_payload([{
        "labels": {"alertname": "CatchapMetricsCollectionStopped", "severity": "critical"},
        "annotations": {"summary": "★catchap 지표가 10분째 안 들어옵니다"},
    }]))
    assert "catchap 지표가 10분째 안 들어옵니다" in t
    assert "CatchapMetricsCollectionStopped" not in t, "규칙 이름이 그대로 새면 안 된다"
    assert t.startswith("[급함]")


def test_title_does_not_repeat_the_place_twice():
    """summary 에 이미 파드 이름이 있으면 뒤에 또 붙이지 않는다."""
    from app.api.v1.endpoints.alerts import _title

    t = _title(_title_payload([{
        "labels": {"alertname": "CatchapPodRestartingRepeatedly", "severity": "warning",
                   "pod": "behavior-ai-5d5d995d64-bfzgd"},
        "annotations": {"summary": "behavior-ai-5d5d995d64-bfzgd 가 한 시간에 6번 다시 떴습니다"},
    }]))
    assert t.count("behavior-ai-5d5d995d64-bfzgd") == 1


def test_title_falls_back_to_korean_name_for_foreign_rules():
    """남이 만든 규칙은 summary 가 영문이다 — 자주 뜨는 것은 한글 이름을 쓴다."""
    from app.api.v1.endpoints.alerts import _title

    # summary 가 아예 없는 경우 → 표의 한글 이름
    t = _title(_title_payload([{
        "labels": {"alertname": "CPUThrottlingHigh", "severity": "info",
                   "pod": "monitoring-prometheus-node-exporter-cgs55"},
        "annotations": {},
    }]))
    assert "CPU 가 한도에 걸려 느려지는 중" in t
    assert "monitoring-prometheus-node-exporter-cgs55" in t, "어디서 났는지는 남아야 한다"


def test_title_keeps_the_rule_name_when_nothing_else_is_known():
    """표에도 없고 summary 도 없으면 규칙 이름 그대로 — 조용히 지우지 않는다."""
    from app.api.v1.endpoints.alerts import _title

    t = _title(_title_payload([{
        "labels": {"alertname": "SomeBrandNewAlert", "severity": "warning"},
        "annotations": {},
    }]))
    assert "SomeBrandNewAlert" in t


def test_resolved_alert_says_so():
    """해제된 경보는 등급 대신 '해제' 로 — 기존 동작이 유지되는지."""
    from app.api.v1.endpoints.alerts import _title

    t = _title(_title_payload([{
        "labels": {"alertname": "CatchapServiceDown", "severity": "critical"},
        "annotations": {"summary": "★backend-api 가 한 벌도 안 떠 있습니다"},
    }], status="resolved"))
    assert t.startswith("[해제]")
