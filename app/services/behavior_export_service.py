"""행동데이터 미리보기·비동기 CSV 생성·보관 서비스.

장시간 HTTP 요청 대신 DB 작업 행을 먼저 만들고, FastAPI BackgroundTasks가 별도 세션에서
CSV를 청크 생성한다. 현재 저장소의 AI 문항 생성과 같은 운영 패턴이며, 결과는 비공개
Object Storage에 24시간 보관하고 다운로드할 때마다 5분짜리 서명 URL을 새로 발급한다.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import logging
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterator

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import BehaviorSummary, StudentProfile
from app.models.behavior_export import BehaviorExportJob
from app.utils.helpers import audit

_log = logging.getLogger(__name__)
K_ANON_MIN = 5
PREVIEW_LIMIT = 50
CHUNK_SIZE = 1000
RETENTION_HOURS = 24
SIGNED_URL_SECONDS = 300
STUCK_MINUTES = 30
_PASS = ("correct", "pass")
_FAIL = ("incorrect", "fail")
_ROW_COLUMNS = [
    "anon_code", "grade_band", "gender", "source_type", "input_type",
    "interaction_result", "risk_level", "sample_label", "solve_time_ms",
    "path_length", "avg_speed", "pause_count", "retry_count",
    "drop_distance_norm", "date",
]
_AGG_COLUMNS = [
    "grade_band", "gender", "source_type", "input_type", "n_events",
    "n_students", "avg_solve_time_ms", "avg_path_length",
    "avg_pause_count", "correct_rate",
]
_DANGER = ("=", "+", "-", "@", "\t", "\r")


def normalize_filters(raw: dict | None) -> dict:
    raw = raw or {}
    out: dict[str, str] = {}
    if raw.get("dataset") in ("included", "candidate", "excluded", "all"):
        out["dataset"] = raw["dataset"]
    else:
        out["dataset"] = "included"
    if raw.get("source_type"):
        out["source_type"] = str(raw["source_type"])[:40]
    if raw.get("risk") in ("low", "review", "elevated"):
        out["risk"] = raw["risk"]
    if raw.get("result_filter") in ("pass", "fail"):
        out["result_filter"] = raw["result_filter"]
    for key in ("date_from", "date_to"):
        if raw.get(key):
            date.fromisoformat(str(raw[key]))
            out[key] = str(raw[key])
    return out


def _apply_filters(q, filters: dict, snapshot_at: datetime):
    q = q.filter(
        BehaviorSummary.student_id.isnot(None),
        BehaviorSummary.created_at <= snapshot_at,
    )
    dataset = filters.get("dataset", "included")
    if dataset != "all":
        q = q.filter(BehaviorSummary.dataset_status == dataset)
    if filters.get("source_type"):
        q = q.filter(BehaviorSummary.source_type == filters["source_type"])
    if filters.get("risk"):
        q = q.filter(BehaviorSummary.risk_level == filters["risk"])
    if filters.get("result_filter") == "pass":
        q = q.filter(BehaviorSummary.interaction_result.in_(_PASS))
    elif filters.get("result_filter") == "fail":
        q = q.filter(BehaviorSummary.interaction_result.in_(_FAIL))
    if filters.get("date_from"):
        q = q.filter(
            BehaviorSummary.created_at
            >= datetime.combine(date.fromisoformat(filters["date_from"]), time.min)
        )
    if filters.get("date_to"):
        q = q.filter(
            BehaviorSummary.created_at
            < datetime.combine(
                date.fromisoformat(filters["date_to"]) + timedelta(days=1), time.min
            )
        )
    return q


def _anon(student_id: str) -> str:
    secret = get_settings().JWT_SECRET_KEY.encode()
    return hmac.new(secret, student_id.encode(), hashlib.sha256).hexdigest()[:12].upper()


def _safe(value):
    if isinstance(value, str) and value and value[0] in _DANGER:
        return "'" + value
    return value


def _row_query(db: Session, filters: dict, snapshot_at: datetime):
    q = (
        db.query(BehaviorSummary, StudentProfile)
        .join(StudentProfile, StudentProfile.id == BehaviorSummary.student_id)
    )
    return _apply_filters(q, filters, snapshot_at)


def _row_dict(summary: BehaviorSummary, student: StudentProfile) -> dict:
    when = summary.occurred_at or summary.created_at
    return {
        "anon_code": _anon(student.id),
        "grade_band": student.grade_band or "unknown",
        "gender": student.gender or "unknown",
        "source_type": summary.source_type,
        "input_type": summary.input_type,
        "interaction_result": summary.interaction_result,
        "risk_level": summary.risk_level,
        "sample_label": summary.sample_label,
        "solve_time_ms": summary.solve_time_ms,
        "path_length": summary.path_length,
        "avg_speed": summary.avg_speed,
        "pause_count": summary.pause_count,
        "retry_count": summary.retry_count,
        "drop_distance_norm": summary.drop_distance_norm,
        "date": when.date().isoformat() if when else None,
    }


def _aggregate_records(
    db: Session, filters: dict, snapshot_at: datetime
) -> tuple[list[dict], int]:
    gb = func.coalesce(StudentProfile.grade_band, "unknown")
    gender = func.coalesce(StudentProfile.gender, "unknown")
    inp = func.coalesce(BehaviorSummary.input_type, "unknown")
    q = (
        db.query(
            gb.label("grade_band"),
            gender.label("gender"),
            BehaviorSummary.source_type.label("source_type"),
            inp.label("input_type"),
            func.count(BehaviorSummary.id).label("n_events"),
            func.count(func.distinct(BehaviorSummary.student_id)).label("n_students"),
            func.avg(BehaviorSummary.solve_time_ms).label("avg_solve_time_ms"),
            func.avg(BehaviorSummary.path_length).label("avg_path_length"),
            func.avg(BehaviorSummary.pause_count).label("avg_pause_count"),
            func.sum(
                case((BehaviorSummary.interaction_result == "correct", 1), else_=0)
            ).label("correct_count"),
        )
        .join(StudentProfile, StudentProfile.id == BehaviorSummary.student_id)
    )
    q = _apply_filters(q, filters, snapshot_at)
    rows = q.group_by(gb, gender, BehaviorSummary.source_type, inp).all()
    records: list[dict] = []
    dropped = 0
    for row in rows:
        students = int(row.n_students or 0)
        if students < K_ANON_MIN:
            dropped += 1
            continue
        events = int(row.n_events or 0)
        records.append({
            "grade_band": row.grade_band,
            "gender": row.gender,
            "source_type": row.source_type,
            "input_type": row.input_type,
            "n_events": events,
            "n_students": students,
            "avg_solve_time_ms": round(float(row.avg_solve_time_ms or 0), 1),
            "avg_path_length": round(float(row.avg_path_length or 0), 1),
            "avg_pause_count": round(float(row.avg_pause_count or 0), 2),
            "correct_rate": round(float(row.correct_count or 0) / events * 100, 1)
            if events else 0,
        })
    return records, dropped


def build_preview(
    db: Session,
    *,
    actor_id: str,
    mode: str,
    filters: dict,
) -> dict:
    filters = normalize_filters(filters)
    snapshot_at = datetime.now()
    if mode == "rows":
        q = _row_query(db, filters, snapshot_at)
        total = q.count()
        pairs = (
            q.order_by(BehaviorSummary.created_at.desc(), BehaviorSummary.id.desc())
            .limit(PREVIEW_LIMIT)
            .all()
        )
        records = [_row_dict(summary, student) for summary, student in pairs]
        columns = _ROW_COLUMNS
        dropped = 0
    else:
        records, dropped = _aggregate_records(db, filters, snapshot_at)
        total = len(records)
        records = records[:PREVIEW_LIMIT]
        columns = _AGG_COLUMNS
    audit(
        db,
        action="behavior.export.preview",
        actor_user_id=actor_id,
        target_type="behavior_export",
        after={
            "mode": mode,
            "filters": filters,
            "count": total,
            "preview_rows": len(records),
            "k_dropped": dropped,
        },
    )
    db.commit()
    return {
        "mode": mode,
        "count": total,
        "k_anon_min": K_ANON_MIN,
        "k_dropped": dropped,
        "columns": columns,
        "rows": records,
        "snapshot_at": snapshot_at.isoformat(),
    }


def _s3():
    settings = get_settings()
    if settings.MEDIA_STORAGE_BACKEND.strip().lower() != "object":
        raise RuntimeError(
            "비동기 내보내기는 Object Storage 설정이 필요합니다 "
            "(MEDIA_STORAGE_BACKEND=object)."
        )
    missing = [
        name for name in (
            "MEDIA_BUCKET", "MEDIA_S3_ENDPOINT",
            "MEDIA_S3_ACCESS_KEY", "MEDIA_S3_SECRET_KEY",
        )
        if not (getattr(settings, name, "") or "").strip()
    ]
    if missing:
        raise RuntimeError("Object Storage 설정 누락: " + ", ".join(missing))
    import boto3
    from botocore.config import Config
    client = boto3.client(
        "s3",
        endpoint_url=settings.MEDIA_S3_ENDPOINT,
        region_name=settings.MEDIA_S3_REGION,
        aws_access_key_id=settings.MEDIA_S3_ACCESS_KEY,
        aws_secret_access_key=settings.MEDIA_S3_SECRET_KEY,
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )
    return client, settings.MEDIA_BUCKET


def _object_key(job_id: str, now: datetime) -> str:
    prefix = get_settings().MEDIA_KEY_PREFIX.strip("/")
    path = f"exports/{now:%Y/%m/%d}/{job_id}.csv"
    return f"{prefix}/{path}" if prefix else path


def _upload_file(path: Path, key: str) -> int:
    client, bucket = _s3()
    client.upload_file(
        str(path),
        bucket,
        key,
        ExtraArgs={
            "ContentType": "text/csv; charset=utf-8",
            "ServerSideEncryption": "AES256",
        },
    )
    return path.stat().st_size


def issue_download_url(job: BehaviorExportJob) -> str:
    client, bucket = _s3()
    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": job.object_key,
            "ResponseContentType": "text/csv; charset=utf-8",
            "ResponseContentDisposition": f'attachment; filename="{job.file_name}"',
        },
        ExpiresIn=SIGNED_URL_SECONDS,
    )


def _delete_object(key: str) -> None:
    client, bucket = _s3()
    client.delete_object(Bucket=bucket, Key=key)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run_export_job(job_id: str, *, session_factory=SessionLocal) -> None:
    db = session_factory()
    temp_path: Path | None = None
    try:
        job = db.get(BehaviorExportJob, job_id)
        if job is None or job.status != "pending":
            return
        job.status = "running"
        job.phase = "querying"
        job.started_at = datetime.now()
        db.commit()

        filters = normalize_filters(job.filters_json)
        stamp = job.snapshot_at.strftime("%Y%m%d")
        job.file_name = f"catchap_behavior_{job.mode}_{stamp}.csv"
        handle = tempfile.NamedTemporaryFile(
            prefix=f"catchap-export-{job.id}-", suffix=".csv", delete=False
        )
        temp_path = Path(handle.name)
        handle.close()

        with temp_path.open("w", newline="", encoding="utf-8-sig") as stream:
            if job.mode == "aggregate":
                records, dropped = _aggregate_records(db, filters, job.snapshot_at)
                job.row_count = len(records)
                job.k_dropped = dropped
                job.phase = "writing"
                db.commit()
                writer = csv.DictWriter(stream, fieldnames=_AGG_COLUMNS)
                writer.writeheader()
                for record in records:
                    writer.writerow({key: _safe(value) for key, value in record.items()})
                job.processed_count = len(records)
                db.commit()
            else:
                base = _row_query(db, filters, job.snapshot_at)
                job.row_count = base.count()
                job.phase = "writing"
                db.commit()
                writer = csv.DictWriter(stream, fieldnames=_ROW_COLUMNS)
                writer.writeheader()
                offset = 0
                while True:
                    pairs = (
                        _row_query(db, filters, job.snapshot_at)
                        .order_by(BehaviorSummary.created_at, BehaviorSummary.id)
                        .offset(offset)
                        .limit(CHUNK_SIZE)
                        .all()
                    )
                    if not pairs:
                        break
                    for summary, student in pairs:
                        record = _row_dict(summary, student)
                        writer.writerow({key: _safe(value) for key, value in record.items()})
                    offset += len(pairs)
                    job.processed_count = offset
                    db.commit()

        job.phase = "uploading"
        db.commit()
        job.object_key = _object_key(job.id, datetime.now())
        job.file_size = _upload_file(temp_path, job.object_key)
        job.sha256 = _sha256(temp_path)
        job.status = "succeeded"
        job.phase = None
        job.finished_at = datetime.now()
        job.expires_at = job.finished_at + timedelta(hours=RETENTION_HOURS)
        audit(
            db,
            action="behavior.export.completed",
            actor_user_id=job.requested_by,
            target_type="behavior_export",
            target_id=job.id,
            after={
                "mode": job.mode,
                "count": job.row_count,
                "k_dropped": job.k_dropped,
                "file_size": job.file_size,
                "sha256": job.sha256,
                "expires_at": job.expires_at.isoformat(),
            },
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 — 실패를 작업 상태와 감사에 남긴다
        _log.exception("행동데이터 내보내기 실패 job=%s", job_id)
        db.rollback()
        job = db.get(BehaviorExportJob, job_id)
        if job is not None:
            job.status = "failed"
            job.phase = None
            job.error_detail = str(exc)[:2000]
            job.finished_at = datetime.now()
            audit(
                db,
                action="behavior.export.failed",
                actor_user_id=job.requested_by,
                target_type="behavior_export",
                target_id=job.id,
                after={"error": job.error_detail},
            )
            db.commit()
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        db.close()


def sweep_stuck_export_jobs(db: Session) -> int:
    cutoff = datetime.now() - timedelta(minutes=STUCK_MINUTES)
    jobs = (
        db.query(BehaviorExportJob)
        .filter(
            BehaviorExportJob.status.in_(("pending", "running")),
            BehaviorExportJob.updated_at < cutoff,
        )
        .all()
    )
    for job in jobs:
        job.status = "failed"
        job.phase = None
        job.finished_at = datetime.now()
        job.error_detail = "서버 재시작 또는 처리 중단으로 작업이 완료되지 않았습니다. 다시 요청해 주세요."
        audit(
            db,
            action="behavior.export.failed",
            actor_user_id=job.requested_by,
            target_type="behavior_export",
            target_id=job.id,
            after={"reason": "stuck_after_restart"},
        )
    if jobs:
        db.commit()
    return len(jobs)


def expire_old_exports(db: Session) -> int:
    jobs = (
        db.query(BehaviorExportJob)
        .filter(
            BehaviorExportJob.status == "succeeded",
            BehaviorExportJob.expires_at < datetime.now(),
        )
        .all()
    )
    expired = 0
    for job in jobs:
        if job.object_key:
            try:
                _delete_object(job.object_key)
            except Exception as exc:  # 삭제 실패 시 상태를 바꿔 재시도 기회를 남긴다
                _log.warning("만료 내보내기 삭제 실패 job=%s: %s", job.id, exc)
                continue
        job.status = "expired"
        expired += 1
    if expired:
        db.commit()
    return expired


def job_to_dict(job: BehaviorExportJob) -> dict:
    return {
        "id": job.id,
        "requested_by": job.requested_by,
        "mode": job.mode,
        "status": job.status,
        "phase": job.phase,
        "filters": job.filters_json,
        "purpose": job.purpose,
        "dua_acknowledged": bool(job.dua_acknowledged),
        "snapshot_at": job.snapshot_at.isoformat(),
        "row_count": int(job.row_count or 0),
        "processed_count": int(job.processed_count or 0),
        "k_dropped": int(job.k_dropped or 0),
        "file_name": job.file_name,
        "file_size": int(job.file_size or 0) if job.file_size is not None else None,
        "sha256": job.sha256,
        "error_detail": job.error_detail,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "expires_at": job.expires_at.isoformat() if job.expires_at else None,
    }
