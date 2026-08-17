"""행동데이터 비동기 내보내기 API 계약 테스트."""

from datetime import datetime, timedelta

import pytest

from app.api.v1.endpoints import ops_exports
from app.core.permissions import Principal, require_ops
from app.main import app
from app.models import AuditLog
from app.models.behavior_export import BehaviorExportJob


@pytest.fixture(autouse=True)
def ops_principal():
    app.dependency_overrides[require_ops] = lambda: Principal(
        kind="user", id="00000000-0000-0000-0000-000000000001", role="ops"
    )
    yield
    app.dependency_overrides.pop(require_ops, None)


def _payload(**overrides):
    data = {
        "mode": "aggregate",
        "dataset": "included",
        "purpose": "운영 품질 분석용 정기 집계",
        "dua_acknowledged": False,
        "idempotency_key": "exp-test-0001",
    }
    data.update(overrides)
    return data


def test_create_returns_202_location_and_is_idempotent(client, db, monkeypatch):
    monkeypatch.setattr(ops_exports, "run_export_job", lambda _job_id: None)

    first = client.post("/api/v1/ops/behavior/exports", json=_payload())
    assert first.status_code == 202
    body = first.json()
    assert body["status"] == "pending"
    assert first.headers["location"].endswith(body["id"])
    assert first.headers["retry-after"] == "2"

    second = client.post("/api/v1/ops/behavior/exports", json=_payload())
    assert second.status_code == 202
    assert second.json()["id"] == body["id"]
    assert db.query(BehaviorExportJob).count() == 1

    requested = (
        db.query(AuditLog)
        .filter(AuditLog.action == "behavior.export.requested")
        .all()
    )
    assert len(requested) == 1
    assert requested[0].target_id == body["id"]


def test_rows_requires_data_use_acknowledgement(client, monkeypatch):
    monkeypatch.setattr(ops_exports, "run_export_job", lambda _job_id: None)
    response = client.post(
        "/api/v1/ops/behavior/exports",
        json=_payload(
            mode="rows",
            idempotency_key="exp-test-rows",
            dua_acknowledged=False,
        ),
    )
    assert response.status_code == 422
    assert "데이터 이용 조건" in response.json()["detail"]


def test_only_one_active_job_per_actor(client, monkeypatch):
    monkeypatch.setattr(ops_exports, "run_export_job", lambda _job_id: None)
    assert client.post("/api/v1/ops/behavior/exports", json=_payload()).status_code == 202

    response = client.post(
        "/api/v1/ops/behavior/exports",
        json=_payload(idempotency_key="exp-test-0002"),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["job_id"]


def test_download_link_is_short_lived_and_audited(client, db, monkeypatch):
    actor_id = "00000000-0000-0000-0000-000000000001"
    job = BehaviorExportJob(
        requested_by=actor_id,
        idempotency_key="exp-test-download",
        mode="aggregate",
        status="succeeded",
        phase="completed",
        filters_json={"dataset": "included"},
        purpose="운영 품질 분석용 정기 집계",
        dua_acknowledged=False,
        snapshot_at=datetime.now(),
        row_count=12,
        processed_count=12,
        object_key="ops/behavior/2026/08/test.csv",
        file_name="catchap-behavior.csv",
        file_size=2048,
        sha256="a" * 64,
        finished_at=datetime.now(),
        expires_at=datetime.now() + timedelta(hours=1),
    )
    db.add(job)
    db.commit()
    monkeypatch.setattr(
        ops_exports,
        "issue_download_url",
        lambda _job: "https://object.example/signed?X-Amz-Expires=300",
    )

    response = client.post(f"/api/v1/ops/behavior/exports/{job.id}/download-link")
    assert response.status_code == 200
    assert response.json()["expires_in"] == 300
    assert response.json()["sha256"] == "a" * 64
    assert "X-Amz-Expires=300" in response.json()["url"]

    audit = (
        db.query(AuditLog)
        .filter(AuditLog.action == "behavior.export.download_issued")
        .one()
    )
    assert audit.target_id == job.id
    assert "url" not in (audit.after_json or {})


def test_list_is_scoped_to_requester(client, db):
    common = {
        "mode": "aggregate",
        "status": "failed",
        "phase": "failed",
        "filters_json": {},
        "purpose": "테스트",
        "dua_acknowledged": False,
        "snapshot_at": datetime.now(),
    }
    own = BehaviorExportJob(
        requested_by="00000000-0000-0000-0000-000000000001",
        idempotency_key="own-job-1",
        **common,
    )
    other = BehaviorExportJob(
        requested_by="00000000-0000-0000-0000-000000000002",
        idempotency_key="other-job-1",
        **common,
    )
    db.add_all([own, other])
    db.commit()

    response = client.get("/api/v1/ops/behavior/exports")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [own.id]
