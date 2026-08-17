"""운영자 행동데이터 미리보기·비동기 내보내기 API.

미리보기는 제한된 행만 동기 조회하고, 실제 반출은 202 작업으로 분리한다.
작업 상태는 DB에 남고 결과 파일은 비공개 Object Storage의 만료형 링크로만 제공한다.
"""

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.permissions import Principal, require_ops
from app.db.session import get_db
from app.models.behavior_export import BehaviorExportJob
from app.services.behavior_export_service import (
    build_preview,
    expire_old_exports,
    issue_download_url,
    job_to_dict,
    normalize_filters,
    run_export_job,
    sweep_stuck_export_jobs,
)
from app.utils.helpers import audit

router = APIRouter(prefix="/ops", tags=["ops"])

Mode = Literal["aggregate", "rows"]
Dataset = Literal["included", "candidate", "excluded", "all"]
Risk = Literal["low", "review", "elevated"]
ResultFilter = Literal["pass", "fail"]


class ExportCreate(BaseModel):
    mode: Mode = "aggregate"
    dataset: Dataset = "included"
    source_type: str | None = Field(default=None, max_length=40)
    risk: Risk | None = None
    result_filter: ResultFilter | None = None
    date_from: date | None = None
    date_to: date | None = None
    purpose: str = Field(min_length=5, max_length=255)
    dua_acknowledged: bool = False
    idempotency_key: str = Field(min_length=8, max_length=64)


def _filters(
    *,
    dataset: str,
    source_type: str | None,
    risk: str | None,
    result_filter: str | None,
    date_from: date | None,
    date_to: date | None,
) -> dict:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="시작일은 종료일보다 늦을 수 없습니다.",
        )
    return normalize_filters(
        {
            "dataset": dataset,
            "source_type": source_type,
            "risk": risk,
            "result_filter": result_filter,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
        }
    )


def _owned_job(db: Session, actor_id: str, job_id: str) -> BehaviorExportJob:
    job = (
        db.query(BehaviorExportJob)
        .filter(
            BehaviorExportJob.id == job_id,
            BehaviorExportJob.requested_by == actor_id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="내보내기 작업을 찾을 수 없습니다.")
    return job


@router.get("/behavior/export/preview")
def preview_behavior_export(
    mode: Mode = "aggregate",
    dataset: Dataset = "included",
    source_type: str | None = None,
    risk: Risk | None = None,
    result_filter: ResultFilter | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """현재 필터 기준 50행 이하 미리보기. 실제 파일 반출과 감사 이벤트를 구분한다."""
    filters = _filters(
        dataset=dataset,
        source_type=source_type,
        risk=risk,
        result_filter=result_filter,
        date_from=date_from,
        date_to=date_to,
    )
    return build_preview(db, actor_id=principal.id, mode=mode, filters=filters)


@router.post("/behavior/exports", status_code=status.HTTP_202_ACCEPTED)
def create_behavior_export(
    req: ExportCreate,
    background_tasks: BackgroundTasks,
    response: Response,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """내보내기 작업을 생성하고 즉시 202를 반환한다."""
    if req.mode == "rows" and not req.dua_acknowledged:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="행 단위 원자료는 데이터 이용 조건 확인이 필요합니다.",
        )

    filters = _filters(
        dataset=req.dataset,
        source_type=req.source_type,
        risk=req.risk,
        result_filter=req.result_filter,
        date_from=req.date_from,
        date_to=req.date_to,
    )

    existing = (
        db.query(BehaviorExportJob)
        .filter(
            BehaviorExportJob.requested_by == principal.id,
            BehaviorExportJob.idempotency_key == req.idempotency_key,
        )
        .first()
    )
    if existing:
        response.headers["Location"] = f"/api/v1/ops/behavior/exports/{existing.id}"
        response.headers["Retry-After"] = "2"
        return job_to_dict(existing)

    active = (
        db.query(BehaviorExportJob)
        .filter(
            BehaviorExportJob.requested_by == principal.id,
            BehaviorExportJob.status.in_(("pending", "running")),
        )
        .first()
    )
    if active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"message": "진행 중인 내보내기 작업이 있습니다.", "job_id": active.id},
        )

    job = BehaviorExportJob(
        requested_by=principal.id,
        idempotency_key=req.idempotency_key,
        mode=req.mode,
        status="pending",
        phase="queued",
        filters_json=filters,
        purpose=req.purpose.strip(),
        dua_acknowledged=req.dua_acknowledged,
        snapshot_at=datetime.now(),
    )
    db.add(job)
    try:
        db.flush()
        audit(
            db,
            action="behavior.export.requested",
            actor_user_id=principal.id,
            target_type="behavior_export",
            target_id=job.id,
            after={
                "mode": job.mode,
                "filters": filters,
                "purpose": job.purpose,
                "dua_acknowledged": job.dua_acknowledged,
            },
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(BehaviorExportJob)
            .filter(
                BehaviorExportJob.requested_by == principal.id,
                BehaviorExportJob.idempotency_key == req.idempotency_key,
            )
            .first()
        )
        if not existing:
            raise
        response.headers["Location"] = f"/api/v1/ops/behavior/exports/{existing.id}"
        response.headers["Retry-After"] = "2"
        return job_to_dict(existing)

    background_tasks.add_task(run_export_job, job.id)
    response.headers["Location"] = f"/api/v1/ops/behavior/exports/{job.id}"
    response.headers["Retry-After"] = "2"
    return job_to_dict(job)


@router.get("/behavior/exports")
def list_behavior_exports(
    limit: int = 20,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """요청자 본인의 최근 작업 이력. 전체 조직 감사는 별도 감사 로그에서 확인한다."""
    sweep_stuck_export_jobs(db)
    expire_old_exports(db)
    limit = max(1, min(50, limit))
    jobs = (
        db.query(BehaviorExportJob)
        .filter(BehaviorExportJob.requested_by == principal.id)
        .order_by(BehaviorExportJob.created_at.desc())
        .limit(limit)
        .all()
    )
    return {"items": [job_to_dict(job) for job in jobs]}


@router.get("/behavior/exports/{job_id}")
def get_behavior_export(
    job_id: str,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """폴링용 작업 상태 조회."""
    sweep_stuck_export_jobs(db)
    expire_old_exports(db)
    return job_to_dict(_owned_job(db, principal.id, job_id))


@router.post("/behavior/exports/{job_id}/download-link")
def create_behavior_export_download_link(
    job_id: str,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """완료 파일의 5분 서명 URL을 발급한다. URL 자체는 감사 로그에 저장하지 않는다."""
    expire_old_exports(db)
    job = _owned_job(db, principal.id, job_id)
    if job.status != "succeeded" or not job.object_key:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="아직 다운로드할 수 없는 작업입니다.")
    if job.expires_at and job.expires_at <= datetime.now():
        raise HTTPException(status.HTTP_410_GONE, detail="보관 기간이 끝난 파일입니다.")

    url = issue_download_url(job)
    audit(
        db,
        action="behavior.export.download_issued",
        actor_user_id=principal.id,
        target_type="behavior_export",
        target_id=job.id,
        after={
            "mode": job.mode,
            "row_count": job.row_count,
            "file_size": job.file_size,
            "sha256": job.sha256,
            "expires_in_seconds": 300,
        },
    )
    db.commit()
    return {
        "url": url,
        "expires_in": 300,
        "file_name": job.file_name,
        "sha256": job.sha256,
    }


@router.get("/behavior/export", include_in_schema=False)
def legacy_behavior_export(
    fmt: str = "json",
    mode: Mode = "aggregate",
    dataset: Dataset = "included",
    source_type: str | None = None,
    risk: Risk | None = None,
    result_filter: ResultFilter | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    principal: Principal = Depends(require_ops),
    db: Session = Depends(get_db),
):
    """구 UI 호환: JSON 미리보기만 허용하고 장시간 동기 CSV 응답은 중단한다."""
    if fmt != "json":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="CSV는 비동기 내보내기 작업으로 생성해 주세요.",
        )
    filters = _filters(
        dataset=dataset,
        source_type=source_type,
        risk=risk,
        result_filter=result_filter,
        date_from=date_from,
        date_to=date_to,
    )
    return build_preview(db, actor_id=principal.id, mode=mode, filters=filters)
