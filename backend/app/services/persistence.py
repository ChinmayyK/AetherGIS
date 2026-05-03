"""Persistent metadata store for sessions, runs, and artifacts."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.db import engine, session_scope
from backend.app.models.db_models import ArtifactRecord, Base, RunRecord, SessionRecord, UserRecord
from backend.app.utils.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)

DEMO_SESSION_ID = "00000000-0000-0000-0000-000000000001"
DEMO_USER_ID = "00000000-0000-0000-0000-000000000000"


def init_database() -> None:
    Base.metadata.create_all(bind=engine)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_dump(value: Any) -> str:
    return json.dumps(value, default=str)


def _json_load(value: Optional[str], fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise ValueError(f"Unsupported datetime value: {value!r}")


def _can_access_owner(record_user_id: str | None, requested_user_id: str | None) -> bool:
    if not requested_user_id:
        return True
    if record_user_id == requested_user_id:
        return True
    return settings.aether_mode != "production" and record_user_id is None


def ensure_demo_session(provider_default: str = "nasa_gibs", name: str = "Demo Workspace") -> SessionRecord:
    with session_scope() as db:
        user = db.get(UserRecord, DEMO_USER_ID)
        if user is None:
            user = UserRecord(
                id=DEMO_USER_ID,
                email="demo@aethergis.com",
                display_name="System Guest",
                role="demo"
            )
            db.add(user)
            db.flush()

        session = db.get(SessionRecord, DEMO_SESSION_ID)
        if session is None:
            session = SessionRecord(
                id=DEMO_SESSION_ID,
                user_id=DEMO_USER_ID,
                name=name,
                provider_default=provider_default,
            )
            db.add(session)
            db.flush()
        elif session.provider_default != provider_default:
            session.provider_default = provider_default
            session.updated_at = _utcnow()
            db.add(session)
            db.flush()
        return session


def ensure_user(user_id: str, email: str | None = None, name: str | None = None) -> UserRecord:
    """Ensure a user record exists in the database."""
    with session_scope() as db:
        user = db.get(UserRecord, user_id)
        if user is None:
            user = UserRecord(
                id=user_id,
                email=email,
                display_name=name,
                role="demo"
            )
            db.add(user)
            db.flush()
        else:
            # Update info if changed
            if email and user.email != email:
                user.email = email
            if name and user.display_name != name:
                user.display_name = name
            db.add(user)
            db.flush()
        return user


def create_session(name: str, provider_default: str = "nasa_gibs", user_id: str | None = None) -> dict[str, Any]:
    with session_scope() as db:
        record = SessionRecord(
            id=str(uuid.uuid4()),
            user_id=user_id,
            name=name.strip() or "Untitled Session",
            provider_default=provider_default,
        )
        db.add(record)
        db.flush()
        return serialize_session(record, db)


def _ensure_personal_session(db: Session, *, user_id: str, provider_default: str, session_name: str | None = None) -> str:
    stmt = (
        select(SessionRecord)
        .where(SessionRecord.user_id == user_id)
        .where(SessionRecord.archived_at.is_(None))
        .order_by(SessionRecord.updated_at.desc())
    )
    existing = db.scalars(stmt).first()
    if existing is not None:
        return existing.id

    record = SessionRecord(
        id=str(uuid.uuid4()),
        user_id=user_id,
        name=(session_name or "My Workspace").strip() or "My Workspace",
        provider_default=provider_default,
    )
    db.add(record)
    db.flush()
    return record.id


def rename_session(session_id: str, name: str, user_id: str | None = None) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        record = db.get(SessionRecord, session_id)
        if record is None:
            return None
        if user_id and not _can_access_owner(record.user_id, user_id):
            return None
        record.name = name.strip() or record.name
        record.updated_at = _utcnow()
        db.add(record)
        db.flush()
        return serialize_session(record, db)


def archive_session(session_id: str, user_id: str | None = None) -> bool:
    with session_scope() as db:
        record = db.get(SessionRecord, session_id)
        if record is None:
            return False
        if user_id and not _can_access_owner(record.user_id, user_id):
            return False
        record.archived_at = _utcnow()
        record.updated_at = _utcnow()
        db.add(record)
        return True


def get_session(session_id: str, user_id: str | None = None) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        record = db.get(SessionRecord, session_id)
        if record is None:
            return None
        if user_id and not _can_access_owner(record.user_id, user_id):
            return None
        return serialize_session(record, db)


def list_sessions(include_archived: bool = False, user_id: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as db:
        stmt = select(SessionRecord).order_by(SessionRecord.updated_at.desc())
        if not include_archived:
            stmt = stmt.where(SessionRecord.archived_at.is_(None))
        records = db.scalars(stmt).all()
        return [serialize_session(record, db) for record in records if _can_access_owner(record.user_id, user_id)]


def serialize_session(record: SessionRecord, db: Session) -> dict[str, Any]:
    run_count = db.scalar(select(func.count(RunRecord.id)).where(RunRecord.session_id == record.id)) or 0
    last_run_at = db.scalar(select(func.max(RunRecord.created_at)).where(RunRecord.session_id == record.id))
    return {
        "session_id": record.id,
        "name": record.name,
        "provider_default": record.provider_default,
        "user_id": record.user_id,
        "run_count": int(run_count),
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "last_run_at": last_run_at.isoformat() if last_run_at else None,
        "archived_at": record.archived_at.isoformat() if record.archived_at else None,
    }


def create_run(
    *,
    job_id: str,
    payload: dict[str, Any],
    manifest: dict[str, Any] | None = None,
    priority: str = "normal",
    session_id: str | None = None,
    session_name: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    expires_at = _utcnow() + timedelta(hours=settings.run_artifact_ttl_hours)
    with session_scope() as db:
        target_session_id = session_id
        if target_session_id is None:
            if user_id:
                target_session_id = _ensure_personal_session(
                    db,
                    user_id=user_id,
                    provider_default=payload.get("data_source", "nasa_gibs"),
                    session_name=session_name,
                )
                effective_user_id = user_id
            else:
                target_session_id = DEMO_SESSION_ID
                ensure_demo_session(provider_default=payload.get("data_source", "nasa_gibs"), name=session_name or "Demo Workspace")
                effective_user_id = DEMO_USER_ID

        session_record = db.get(SessionRecord, target_session_id)
        if session_record is None:
            raise ValueError(f"Session {target_session_id} not found")
        if user_id and not _can_access_owner(session_record.user_id, user_id):
            raise ValueError("Session ownership mismatch")
        if session_name and session_record.name != session_name:
            session_record.name = session_name.strip() or session_record.name

        run = db.get(RunRecord, job_id)
        if run is None:
            run = RunRecord(
                id=job_id,
                session_id=target_session_id,
                user_id=effective_user_id,
                provider=payload.get("data_source", "nasa_gibs"),
                status="QUEUED",
                priority=priority,
                layer_id=payload["layer_id"],
                aoi_bbox_json=_json_dump(payload["bbox"]),
                time_start=_to_datetime(payload["time_start"]),
                time_end=_to_datetime(payload["time_end"]),
                resolution=int(payload.get("resolution", 1024)),
                interpolation_model=str(payload.get("interpolation_model", "film")),
                n_intermediate=int(payload.get("n_intermediate", 4)),
                step_minutes=payload.get("step_minutes"),
                include_low_confidence=bool(payload.get("include_low_confidence", False)),
                params_json=_json_dump(payload),
                manifest_json=_json_dump(manifest) if manifest else None,
                expires_at=expires_at,
            )
            db.add(run)
        else:
            run.session_id = target_session_id
            run.provider = payload.get("data_source", run.provider)
            run.priority = priority
            run.layer_id = payload["layer_id"]
            run.aoi_bbox_json = _json_dump(payload["bbox"])
            run.time_start = _to_datetime(payload["time_start"])
            run.time_end = _to_datetime(payload["time_end"])
            run.resolution = int(payload.get("resolution", run.resolution))
            run.interpolation_model = str(payload.get("interpolation_model", run.interpolation_model))
            run.n_intermediate = int(payload.get("n_intermediate", run.n_intermediate))
            run.step_minutes = payload.get("step_minutes")
            run.include_low_confidence = bool(payload.get("include_low_confidence", run.include_low_confidence))
            run.params_json = _json_dump(payload)
            run.manifest_json = _json_dump(manifest) if manifest else run.manifest_json
            run.expires_at = expires_at
            db.add(run)

        session_record.updated_at = _utcnow()
        db.add(session_record)

        db.flush()
        return serialize_run(run)


def update_run_state(
    job_id: str,
    *,
    status: str | None = None,
    progress: float | None = None,
    stage: str | None = None,
    message: str | None = None,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        run = db.get(RunRecord, job_id)
        if run is None:
            return None

        if status:
            run.status = status
            if status == "RUNNING" and run.started_at is None:
                run.started_at = _utcnow()
            if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                run.completed_at = _utcnow()
        if progress is not None:
            run.progress = max(0.0, min(1.0, progress))
        if stage:
            run.current_stage = stage
        if message is not None:
            run.message = message
        if error is not None:
            run.error_message = error
        if result is not None:
            run.result_json = _json_dump(result)
            metrics = result.get("metrics") if isinstance(result, dict) else None
            if metrics is not None:
                run.metrics_json = _json_dump(metrics)
        if manifest is not None:
            run.manifest_json = _json_dump(manifest)

        session_record = db.get(SessionRecord, run.session_id)
        if session_record:
            session_record.updated_at = _utcnow()
            db.add(session_record)

        db.add(run)
        db.flush()
        return serialize_run(run)


def get_run(job_id: str, user_id: str | None = None) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        run = db.get(RunRecord, job_id)
        if run is None:
            return None
        if user_id and not _can_access_owner(run.user_id, user_id):
            return None
        return serialize_run(run)


def list_runs(session_id: str | None = None, limit: int = 50, user_id: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as db:
        stmt = select(RunRecord).order_by(RunRecord.created_at.desc()).limit(limit)
        if session_id:
            stmt = stmt.where(RunRecord.session_id == session_id)
        runs = db.scalars(stmt).all()
        return [serialize_run(run) for run in runs if _can_access_owner(run.user_id, user_id)]


def delete_run(job_id: str, user_id: str | None = None) -> bool:
    with session_scope() as db:
        run = db.get(RunRecord, job_id)
        if run is None:
            return False
        if user_id and not _can_access_owner(run.user_id, user_id):
            return False
        db.delete(run)
        return True


def upsert_run_artifacts(job_id: str, export_dir: Path) -> list[dict[str, Any]]:
    with session_scope() as db:
        run = db.get(RunRecord, job_id)
        if run is None:
            return []

        existing = {artifact.kind: artifact for artifact in run.artifacts}
        discovered: list[ArtifactRecord] = []
        expiry = _utcnow() + timedelta(hours=settings.run_artifact_ttl_hours)

        candidates: list[tuple[str, Path]] = [
            ("metadata", export_dir / "metadata.json"),
            ("video_original", export_dir / "original.mp4"),
            ("video_interpolated", export_dir / "interpolated.mp4"),
            ("video_all", export_dir / "all.mp4"),
            ("report_html", export_dir / "report.html"),
        ]

        for kind, path in candidates:
            if not path.exists() or not path.is_file():
                continue
            artifact = existing.get(kind)
            if artifact is None:
                artifact = ArtifactRecord(
                    id=str(uuid.uuid4()),
                    run_id=job_id,
                    kind=kind,
                    file_path=str(path),
                )
            artifact.size_bytes = path.stat().st_size
            artifact.file_path = str(path)
            artifact.expires_at = expiry
            db.add(artifact)
            discovered.append(artifact)

        db.flush()
        return [serialize_artifact(item) for item in discovered]


def serialize_run(run: RunRecord) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "job_id": run.id,
        "session_id": run.session_id,
        "user_id": run.user_id,
        "provider": run.provider,
        "status": run.status,
        "priority": run.priority,
        "layer_id": run.layer_id,
        "bbox": _json_load(run.aoi_bbox_json, []),
        "time_start": run.time_start.isoformat(),
        "time_end": run.time_end.isoformat(),
        "resolution": run.resolution,
        "interpolation_model": run.interpolation_model,
        "n_intermediate": run.n_intermediate,
        "step_minutes": run.step_minutes,
        "include_low_confidence": run.include_low_confidence,
        "params": _json_load(run.params_json, {}),
        "metrics": _json_load(run.metrics_json, {}),
        "manifest": _json_load(run.manifest_json, {}),
        "result": _json_load(run.result_json, None),
        "error_message": run.error_message,
        "current_stage": run.current_stage,
        "progress": run.progress,
        "message": run.message,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "expires_at": run.expires_at.isoformat() if run.expires_at else None,
    }


def serialize_artifact(artifact: ArtifactRecord) -> dict[str, Any]:
    return {
        "artifact_id": artifact.id,
        "run_id": artifact.run_id,
        "kind": artifact.kind,
        "file_path": artifact.file_path,
        "size_bytes": artifact.size_bytes,
        "checksum": artifact.checksum,
        "created_at": artifact.created_at.isoformat(),
        "expires_at": artifact.expires_at.isoformat() if artifact.expires_at else None,
    }


def cleanup_expired_runs(limit: int = 500) -> dict[str, int]:
    """Delete expired run rows and associated on-disk artifacts."""
    now = _utcnow()
    deleted_runs = 0
    deleted_files = 0
    deleted_dirs = 0

    with session_scope() as db:
        stmt = (
            select(RunRecord)
            .where(RunRecord.expires_at.is_not(None))
            .where(RunRecord.expires_at <= now)
            .order_by(RunRecord.expires_at.asc())
            .limit(limit)
        )
        runs = db.scalars(stmt).all()

        for run in runs:
            for artifact in run.artifacts:
                path = Path(artifact.file_path)
                try:
                    if path.exists() and path.is_file():
                        path.unlink(missing_ok=True)
                        deleted_files += 1
                except Exception as exc:
                    logger.warning("Failed to delete expired artifact file", run_id=run.id, path=str(path), error=str(exc))

            export_dir = settings.exports_dir / run.id
            try:
                if export_dir.exists() and export_dir.is_dir():
                    import shutil

                    shutil.rmtree(export_dir, ignore_errors=True)
                    deleted_dirs += 1
            except Exception as exc:
                logger.warning("Failed to delete expired export directory", run_id=run.id, path=str(export_dir), error=str(exc))

            db.delete(run)
            deleted_runs += 1

    if deleted_runs:
        logger.info(
            "Expired run cleanup completed",
            deleted_runs=deleted_runs,
            deleted_files=deleted_files,
            deleted_dirs=deleted_dirs,
        )

    return {
        "deleted_runs": deleted_runs,
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
    }
