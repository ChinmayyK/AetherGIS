"""AetherGIS API â€” Jobs Router (MODULE 1 + 2 + 15).

Endpoints:
  POST   /api/v1/jobs                          â€” submit new job
  GET    /api/v1/jobs/{job_id}/status          â€” poll status + ETA + queue pos
  GET    /api/v1/jobs/{job_id}/logs            â€” stage log stream
  POST   /api/v1/jobs/{job_id}/cancel          â€” cancel job
  GET    /api/v1/jobs/{job_id}/reproduce       â€” get reproducibility manifest
  GET    /api/v1/jobs/{job_id}/audit           â€” full audit trail
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.config import get_settings
from backend.app.api.deps.identity import resolve_current_user_id
from backend.app.models.schemas import InterpolationModel, Resolution, DataSource
from backend.app.services.job_manager import (
    JobPriority,
    can_accept_new_job,
    cancel_job,
    create_job,
    get_job,
    get_job_logs,
    get_queue_position,
    load_audit_trail,
    load_manifest,
    save_manifest,
    update_job,
    complete_job,
    fail_job,
    append_audit_event,
)
from backend.app.utils.logging import get_logger
from backend.app.services.persistence import get_run as get_persisted_run

router = APIRouter(prefix="/jobs", tags=["Jobs"])
logger = get_logger(__name__)
settings = get_settings()


# â”€â”€ Request/Response schemas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class JobSubmitRequest(BaseModel):
    layer_id: str = Field(..., examples=["GOES-East_ABI_Band2_Red_Visible_1km"])
    data_source: DataSource = DataSource.nasa_gibs
    session_id: Optional[str] = None
    session_name: Optional[str] = Field(default=None, max_length=255)
    bbox: list[float] = Field(..., min_length=4, max_length=4)
    time_start: datetime
    time_end: datetime
    resolution: Resolution = Resolution.medium
    interpolation_model: InterpolationModel = InterpolationModel.rife
    n_intermediate: int = Field(default=4, ge=1, le=8)
    step_minutes: Optional[int] = Field(None, ge=1, le=1440)
    include_low_confidence: bool = False
    priority: JobPriority = JobPriority.normal


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    priority: str
    progress: float
    current_stage: str
    stage_index: int
    queue_position: int
    estimated_completion: Optional[str]
    message: Optional[str]
    error: Optional[str]
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]


class JobLogEntry(BaseModel):
    ts: str
    msg: str
    progress: float
    stage: str


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _build_payload(job_id: str, req: JobSubmitRequest) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "session_id": req.session_id,
        "session_name": req.session_name,
        "layer_id": req.layer_id,
        "data_source": req.data_source.value,
        "bbox": req.bbox,
        "time_start": req.time_start.isoformat(),
        "time_end": req.time_end.isoformat(),
        "resolution": req.resolution.value,
        "interpolation_model": req.interpolation_model.value,
        "n_intermediate": req.n_intermediate,
        "step_minutes": req.step_minutes,
        "include_low_confidence": req.include_low_confidence,
    }


def _build_manifest(job_id: str, req: JobSubmitRequest) -> dict:
    """Build reproducibility manifest capturing all job parameters."""
    import hashlib
    from backend.app.services.interpolation import get_engine

    get_engine(req.interpolation_model.value)
    # Hash model weights file if it exists
    model_path = settings.rife_model_path if req.interpolation_model.value == "rife" else settings.film_model_path
    weight_hash = None
    if model_path.exists():
        with open(model_path, "rb") as f:
            weight_hash = hashlib.sha256(f.read(1024 * 1024)).hexdigest()[:16]  # first 1MB

    params = _build_payload(job_id, req)
    return {
        "schema_version": "1.0",
        "job_id": job_id,
        "submitted_at": datetime.utcnow().isoformat() + "Z",
        "parameters": params,
        "model": {
            "name": req.interpolation_model.value,
            "weight_hash": weight_hash,
        },
        "preprocessing": {
            "bbox": req.bbox,
            "resolution": req.resolution.value,
        },
        "config_snapshot": {
            "flow_consistency_threshold": settings.flow_consistency_threshold,
            "flow_rejection_threshold": settings.flow_rejection_threshold,
            "large_diff_threshold": settings.large_diff_threshold,
            "cs_weight_flow": settings.cs_weight_flow,
            "cs_weight_mad": settings.cs_weight_mad,
            "cs_weight_gap": settings.cs_weight_gap,
            "high_confidence_threshold": settings.high_confidence_threshold,
            "medium_confidence_threshold": settings.medium_confidence_threshold,
        },
        "wms_urls": {
            "nasa_gibs": settings.nasa_gibs_base_url,
            "bhuvan": settings.bhuvan_wms_url,
        },
    }


async def _run_job_async(payload: dict, job_id: str) -> None:
    """In-process async job execution with full audit trail."""
    from backend.app.services.pipeline import run_pipeline

    update_job(job_id, status="RUNNING", stage="ingestion", progress=0.02, message="Initializing pipeline")
    append_audit_event(job_id, "pipeline_started", {"payload": payload})

    def on_progress(progress: float, message: str) -> None:
        # Map progress to stage
        if progress < 0.25:
            stage = "ingestion"
        elif progress < 0.35:
            stage = "preprocessing"
        elif progress < 0.75:
            stage = "interpolation"
        elif progress < 0.90:
            stage = "confidence"
        else:
            stage = "export"
        update_job(job_id, status="RUNNING", progress=progress, stage=stage, message=message)
        append_audit_event(job_id, "progress_update", {"progress": progress, "stage": stage, "message": message})

    try:
        result = await run_pipeline(
            job_id=payload["job_id"],
            layer_id=payload["layer_id"],
            data_source=payload["data_source"],
            bbox=payload["bbox"],
            time_start=datetime.fromisoformat(payload["time_start"]),
            time_end=datetime.fromisoformat(payload["time_end"]),
            resolution=payload["resolution"],
            interpolation_model=payload["interpolation_model"],
            n_intermediate=payload["n_intermediate"],
            step_minutes=payload.get("step_minutes"),
            include_low_confidence=payload["include_low_confidence"],
            progress_callback=on_progress,
        )
        result_dict = result.model_dump(mode="json")
        complete_job(job_id, result_dict)
        append_audit_event(job_id, "pipeline_completed", {
            "total_frames": result.metrics.total_frames if result.metrics else 0,
        })

        # Update global metrics
        try:
            from backend.app.services.geo_analytics import update_global_metrics_from_job
            update_global_metrics_from_job(result_dict)
        except Exception as exc:
            logger.warning("Global metrics update failed", error=str(exc))

    except Exception as exc:
        logger.exception("Pipeline execution failed", job_id=job_id)
        fail_job(job_id, str(exc))
        append_audit_event(job_id, "pipeline_failed", {"error": str(exc)})


# â”€â”€ Endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post("", response_model=dict, status_code=202)
async def submit_job(
    request: JobSubmitRequest,
    current_user_id: str = Depends(resolve_current_user_id),
) -> dict:
    """Submit a new pipeline job with priority queue support."""
    allowed, reason = can_accept_new_job()
    if not allowed:
        raise HTTPException(status_code=429, detail=reason)

    job_id = str(uuid.uuid4())
    manifest = _build_manifest(job_id, request)
    payload = _build_payload(job_id, request)
    record = create_job(
        job_id,
        manifest=manifest,
        priority=request.priority,
        payload=payload,
        session_id=request.session_id,
        session_name=request.session_name,
        user_id=current_user_id,
    )
    save_manifest(job_id, manifest)

    # Try Celery first, fallback to in-process async
    try:
        from backend.app.tasks.celery_app import run_pipeline_task
        run_pipeline_task.apply_async(
            args=[payload],
            task_id=job_id,
            priority={"high": 9, "normal": 5, "low": 1}[request.priority.value],
        )
        logger.info("Job queued via Celery", job_id=job_id, priority=request.priority)
    except Exception as exc:
        logger.warning("Celery unavailable â€” in-process execution", error=str(exc), job_id=job_id)
        update_job(job_id, message="Running in-process (Celery unavailable)")
        asyncio.create_task(_run_job_async(payload, job_id))

    return {
        "job_id": job_id,
        "status": "QUEUED",
        "priority": request.priority.value,
        "queue_position": record.queue_position,
        "estimated_completion": record.estimated_completion,
    }


@router.get("/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(job_id: str, current_user_id: str = Depends(resolve_current_user_id)) -> JobStatusResponse:
    """Get full job status including stage, queue position, and ETA."""
    if get_persisted_run(job_id, user_id=current_user_id) is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    record = get_job(job_id)
    if record is None:
        # Try Celery
        try:
            from celery.result import AsyncResult
            from backend.app.tasks.celery_app import celery_app
            result = AsyncResult(job_id, app=celery_app)
            state_map = {
                "PENDING": "QUEUED", "STARTED": "RUNNING",
                "RUNNING": "RUNNING", "SUCCESS": "COMPLETED", "FAILURE": "FAILED",
            }
            status = state_map.get(result.state, "QUEUED")
            meta = result.info or {}
            return JobStatusResponse(
                job_id=job_id, status=status, priority="normal",
                progress=meta.get("progress", 0.0) if isinstance(meta, dict) else 0.0,
                current_stage=meta.get("stage", "unknown") if isinstance(meta, dict) else "unknown",
                stage_index=0, queue_position=0, estimated_completion=None,
                message=meta.get("message") if isinstance(meta, dict) else None,
                error=str(result.result) if result.state == "FAILURE" else None,
                created_at="", started_at=None, completed_at=None,
            )
        except Exception:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    pos = get_queue_position(job_id) if record.status == "QUEUED" else 0
    return JobStatusResponse(
        job_id=job_id,
        status=record.status,
        priority=record.priority,
        progress=record.progress,
        current_stage=record.current_stage,
        stage_index=record.stage_index,
        queue_position=pos,
        estimated_completion=record.estimated_completion,
        message=record.message,
        error=record.error,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
    )


@router.get("/{job_id}/logs", response_model=list[JobLogEntry])
async def get_job_logs_endpoint(
    job_id: str,
    since: Optional[str] = Query(None, description="ISO timestamp â€” return only logs after this time"),
    current_user_id: str = Depends(resolve_current_user_id),
) -> list[dict]:
    """Get structured log stream for a job."""
    if get_persisted_run(job_id, user_id=current_user_id) is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    logs = get_job_logs(job_id)
    if not logs:
        raise HTTPException(status_code=404, detail=f"No logs found for job {job_id}")

    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            logs = [l for l in logs if datetime.fromisoformat(l["ts"]) > since_dt]
        except Exception:
            pass

    return logs


@router.post("/{job_id}/cancel", response_model=dict)
async def cancel_job_endpoint(job_id: str, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Cancel a queued or running job."""
    if get_persisted_run(job_id, user_id=current_user_id) is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    # Revoke Celery task
    try:
        from backend.app.tasks.celery_app import celery_app
        celery_app.control.revoke(job_id, terminate=True, signal="SIGKILL")
    except Exception as exc:
        logger.warning("Celery revoke failed", job_id=job_id, error=str(exc))

    record = cancel_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return {"job_id": job_id, "status": "CANCELLED"}


@router.get("/{job_id}/reproduce", response_model=dict)
async def get_reproduce_manifest(job_id: str, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Get the full reproducibility manifest for a job (MODULE 2)."""
    if get_persisted_run(job_id, user_id=current_user_id) is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    manifest = load_manifest(job_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"No manifest found for job {job_id}")
    return manifest


@router.get("/{job_id}/audit", response_model=list)
async def get_audit_trail(job_id: str, current_user_id: str = Depends(resolve_current_user_id)) -> list:
    """Get the complete audit trail for a job (MODULE 15)."""
    if get_persisted_run(job_id, user_id=current_user_id) is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    trail = load_audit_trail(job_id)
    if not trail:
        raise HTTPException(status_code=404, detail=f"No audit trail found for job {job_id}")
    return trail

