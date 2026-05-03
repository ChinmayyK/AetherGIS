"""AetherGIS API â€” Advanced Analytics Routes (Modules 1â€“15).

New endpoints:
  GET  /api/v1/jobs/{job_id}/trajectories          MODULE 1
  POST /api/v1/jobs/{job_id}/predict               MODULE 2
  GET  /api/v1/jobs/{job_id}/explain/{frame_idx}   MODULE 3
  GET  /api/v1/jobs/{job_id}/alerts                MODULE 5
  GET  /api/v1/jobs/{job_id}/time_series           MODULE 6
  POST /api/v1/jobs/{job_id}/replay                MODULE 8
  GET  /api/v1/jobs/{job_id}/heatmap/{type}        MODULE 10
  GET  /api/v1/jobs/{job_id}/temporal_consistency  MODULE 11
  GET  /api/v1/jobs/{job_id}/metric_evolution      MODULE 14
  GET  /api/v1/jobs/{job_id}/report                MODULE 15
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from backend.app.api.deps.identity import resolve_current_user_id, require_owned_run

from backend.app.config import get_settings
from backend.app.utils.logging import get_logger

router = APIRouter(prefix="/jobs", tags=["Advanced Analytics"])
settings = get_settings()
logger = get_logger(__name__)


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _load_frames_for_job(job_id: str, current_user_id: str) -> List[Any]:
    """Load all exported frame PNGs for a job as numpy arrays."""
    require_owned_run(job_id, current_user_id)
    import numpy as np
    from PIL import Image

    frames_dir = settings.exports_dir / job_id / "frames"
    if not frames_dir.exists():
        raise HTTPException(status_code=404, detail=f"No frames found for job {job_id}")

    paths = sorted(frames_dir.glob("frame_*.png"))
    if not paths:
        raise HTTPException(status_code=404, detail=f"No frames found for job {job_id}")

    frames = []
    for p in paths:
        try:
            img = np.array(Image.open(str(p)).convert("RGB"), dtype=np.float32) / 255.0
            frames.append(img)
        except Exception:
            pass
    return frames


def _get_completed_job(job_id: str, current_user_id: str) -> dict:
    """Retrieve a completed job record or raise 404/202."""
    require_owned_run(job_id, current_user_id)
    try:
        from backend.app.services.job_store import get_job as _get1
        record = _get1(job_id)
    except Exception:
        record = None

    if record is None:
        try:
            from backend.app.services.job_manager import get_job as _get2
            record = _get2(job_id)
        except Exception:
            pass

    if record is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    status = getattr(record, "status", None) or (record.get("status") if isinstance(record, dict) else None)
    if status not in ("COMPLETED", "completed"):
        raise HTTPException(status_code=202, detail=f"Job not yet completed (status: {status})")

    result = getattr(record, "result", None) or (record.get("result") if isinstance(record, dict) else {})
    return result or {}


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 1 â€” Trajectory Tracking
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@router.get("/{job_id}/trajectories")
async def get_trajectories(job_id: str, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Track cloud-cluster motion across all frames and return path overlays."""
    frames = _load_frames_for_job(job_id, current_user_id)

    from backend.app.services.trajectory_tracker import track_trajectories
    trajectories = track_trajectories(frames, job_id)

    return {
        "job_id": job_id,
        "total_trajectories": len(trajectories),
        "total_frames": len(frames),
        "trajectories": trajectories,
    }


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 2 â€” Multi-Step Future Prediction
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

class PredictRequest(BaseModel):
    n_ahead: int = Field(default=3, ge=1, le=6, description="Number of future frames to predict")
    step_minutes: int = Field(default=10, ge=5, le=60, description="Minutes between predicted frames")


@router.post("/{job_id}/predict")
async def predict_future(job_id: str, body: PredictRequest, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Predict future frames by extrapolating optical-flow motion."""
    frames = _load_frames_for_job(job_id, current_user_id)

    result = _get_completed_job(job_id, current_user_id)
    frame_meta = result.get("frames", [])
    last_ts = None
    if frame_meta:
        try:
            last_ts = datetime.fromisoformat(frame_meta[-1]["timestamp"].rstrip("Z"))
        except Exception:
            pass

    from backend.app.services.prediction_engine import predict_future_frames
    predictions = predict_future_frames(
        observed_frames=frames,
        n_ahead=body.n_ahead,
        step_minutes=body.step_minutes,
        last_timestamp=last_ts,
    )

    return {
        "job_id": job_id,
        "n_ahead": body.n_ahead,
        "step_minutes": body.step_minutes,
        "label": "PREDICTED (LOW CONFIDENCE)",
        "disclaimer": "AI-extrapolated frames. NOT suitable for operational forecasting.",
        "predictions": predictions,
    }


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 3 â€” Explainability Engine
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@router.get("/{job_id}/explain/{frame_idx}")
async def explain_frame(job_id: str, frame_idx: int, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Return explainability overlay for a single interpolated frame."""
    require_owned_run(job_id, current_user_id)
    import numpy as np
    from PIL import Image

    frames_dir = settings.exports_dir / job_id / "frames"
    paths = sorted(frames_dir.glob("frame_*.png")) if frames_dir.exists() else []

    def _load(idx: int):
        for p in paths:
            if f"frame_{idx:04d}" in p.name:
                return np.array(Image.open(str(p)).convert("RGB"), dtype=np.float32) / 255.0
        return None

    frame = _load(frame_idx)
    if frame is None:
        raise HTTPException(status_code=404, detail=f"Frame {frame_idx} not found for job {job_id}")

    prev_frame = _load(frame_idx - 1)
    next_frame = _load(frame_idx + 1)

    from backend.app.services.explainability_engine import generate_explanation
    explanation = generate_explanation(frame, prev_frame, next_frame, frame_index=frame_idx)
    explanation["job_id"] = job_id
    return explanation


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 5 â€” Region Alert System
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@router.get("/{job_id}/alerts")
async def get_alerts(job_id: str, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Detect critical changes and return an alert list for this job."""
    frames = _load_frames_for_job(job_id, current_user_id)

    result = _get_completed_job(job_id, current_user_id)
    frame_meta = result.get("frames", [])

    from backend.app.services.alert_system_v2 import detect_alerts
    alerts = detect_alerts(frames, frame_meta, job_id)

    high = sum(1 for a in alerts if a.get("severity") == "high")
    medium = sum(1 for a in alerts if a.get("severity") == "medium")

    return {
        "job_id": job_id,
        "total_alerts": len(alerts),
        "high_severity": high,
        "medium_severity": medium,
        "low_severity": len(alerts) - high - medium,
        "alerts": alerts,
    }


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 6 â€” Time-Series Analytics
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@router.get("/{job_id}/time_series")
async def get_time_series(job_id: str, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Compute brightness, motion, and coverage trends over all frames."""
    frames = _load_frames_for_job(job_id, current_user_id)

    result = _get_completed_job(job_id, current_user_id)
    frame_meta = result.get("frames", [])
    timestamps = [m.get("timestamp", str(i)) for i, m in enumerate(frame_meta)] if frame_meta else None

    from backend.app.services.time_series_analytics import compute_time_series
    ts_data = compute_time_series(frames, timestamps)
    ts_data["job_id"] = job_id
    return ts_data


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 8 â€” Scenario Replay Engine
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

class ReplayRequest(BaseModel):
    interpolation_model: str = Field(default="film", pattern="^(film|rife|lk_fallback)$")
    n_intermediate: int = Field(default=4, ge=1, le=8)
    region_bbox: Optional[List[float]] = Field(None, min_length=4, max_length=4)


@router.post("/{job_id}/replay")
async def replay_job(job_id: str, body: ReplayRequest, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """
    Re-run a completed job with different parameters.
    Submits a new child job and returns its ID.
    """
    result = _get_completed_job(job_id, current_user_id)

    new_job_id = str(uuid.uuid4())
    payload = {
        "job_id": new_job_id,
        "layer_id": result.get("layer_id", ""),
        "data_source": result.get("data_source", "nasa_gibs"),
        "bbox": body.region_bbox or result.get("bbox", []),
        "time_start": result.get("time_start", ""),
        "time_end": result.get("time_end", ""),
        "resolution": result.get("resolution", 1024),
        "interpolation_model": body.interpolation_model,
        "n_intermediate": body.n_intermediate,
        "step_minutes": None,
        "include_low_confidence": False,
        "parent_job_id": job_id,
    }

    # Try Celery; fall back to in-process
    try:
        from backend.app.tasks.celery_app import run_pipeline_task
        from backend.app.services.job_manager import create_job as _cj
        _cj(new_job_id, manifest={"parent_job_id": job_id, "replay_params": body.model_dump()})
        run_pipeline_task.apply_async(args=[payload], task_id=new_job_id)
    except Exception as exc:
        logger.warning("Replay via Celery failed; in-process", error=str(exc), job_id=new_job_id)
        try:
            from backend.app.services.job_store import create_job as _cj2
            from backend.app.api.routes.pipeline import _run_pipeline_in_process
            _cj2(new_job_id, message="Replay queued (in-process)")
            asyncio.create_task(_run_pipeline_in_process(payload))
        except Exception as exc2:
            logger.error("In-process replay failed", error=str(exc2))
            raise HTTPException(status_code=500, detail=f"Replay submission failed: {exc2}")

    return {
        "new_job_id": new_job_id,
        "parent_job_id": job_id,
        "status": "QUEUED",
        "params": body.model_dump(),
    }


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 10 â€” Heatmap Generation
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@router.get("/{job_id}/heatmap/{heatmap_type}")
async def get_heatmap(
    job_id: str,
    heatmap_type: str = Path(..., pattern="^(motion|uncertainty|anomaly)$"),
    current_user_id: str = Depends(resolve_current_user_id),
) -> dict:
    """Return a base64-encoded PNG heatmap overlay."""
    frames = _load_frames_for_job(job_id, current_user_id)

    from backend.app.services.heatmap_gen import generate_heatmap  # type: ignore
    data_url = generate_heatmap(frames, heatmap_type)  # type: ignore

    return {
        "job_id": job_id,
        "type": heatmap_type,
        "total_frames": len(frames),
        "data_url": data_url,
    }


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 11 â€” Temporal Consistency Checker
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@router.get("/{job_id}/temporal_consistency")
async def get_temporal_consistency(job_id: str, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Detect unrealistic transitions (sudden jumps, broken motion)."""
    frames = _load_frames_for_job(job_id, current_user_id)

    from backend.app.services.temporal_checker import check_temporal_consistency
    issues = check_temporal_consistency(frames)

    return {
        "job_id": job_id,
        "total_frames": len(frames),
        "issues_found": len(issues),
        "high_severity": sum(1 for i in issues if i.get("severity") == "high"),
        "issues": issues,
    }


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 14 â€” Metric Evolution Tracking
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@router.get("/{job_id}/metric_evolution")
async def get_metric_evolution(job_id: str, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Track PSNR / SSIM / confidence stability across the job's frame sequence."""
    result = _get_completed_job(job_id, current_user_id)
    frame_meta = result.get("frames", [])

    if not frame_meta:
        raise HTTPException(status_code=404, detail="No frame metadata available for this job")

    from backend.app.services.time_series_analytics import compute_metric_evolution
    evo = compute_metric_evolution(frame_meta)
    evo["job_id"] = job_id
    return evo


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 15 â€” Auto-Report Generator
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@router.get("/{job_id}/report", response_class=HTMLResponse)
async def generate_report(job_id: str, current_user_id: str = Depends(resolve_current_user_id)) -> HTMLResponse:
    """Generate a full HTML analysis report for a completed job."""
    result = _get_completed_job(job_id, current_user_id)
    frames = _load_frames_for_job(job_id, current_user_id)

    frame_meta = result.get("frames", [])

    # Gather supplementary analytics
    trajectories = None
    alerts = None
    time_series = None
    consistency_issues = None

    try:
        from backend.app.services.trajectory_tracker import track_trajectories
        trajectories = track_trajectories(frames, job_id)
    except Exception as e:
        logger.warning("Trajectories failed for report", error=str(e))

    try:
        from backend.app.services.alert_system_v2 import detect_alerts
        alerts = detect_alerts(frames, frame_meta, job_id)
    except Exception as e:
        logger.warning("Alerts failed for report", error=str(e))

    try:
        from backend.app.services.time_series_analytics import compute_time_series
        timestamps = [m.get("timestamp") for m in frame_meta] if frame_meta else None
        time_series = compute_time_series(frames, timestamps)
    except Exception as e:
        logger.warning("Time series failed for report", error=str(e))

    try:
        from backend.app.services.temporal_checker import check_temporal_consistency
        consistency_issues = check_temporal_consistency(frames)
    except Exception as e:
        logger.warning("Consistency check failed for report", error=str(e))

    from backend.app.services.report_service import generate_html_report
    html = generate_html_report(
        job_id=job_id,
        pipeline_result=result,
        trajectories=trajectories,
        alerts=alerts,
        time_series=time_series,
        consistency_issues=consistency_issues,
    )
    return HTMLResponse(content=html, status_code=200)

