"""AetherGIS API â€” Analytics + Streaming Routes.

Covers:
  MODULE 4  â€” Cache: GET /api/v1/cache/status, POST /api/v1/cache/clear
  MODULE 5  â€” Models: GET /api/v1/models
  MODULE 6  â€” Confidence maps: GET /api/v1/jobs/{job_id}/confidence_map/{frame}
  MODULE 7  â€” Change maps: GET /api/v1/jobs/{job_id}/change_map/{frame}
  MODULE 9  â€” Region query: POST /api/v1/region/query
  MODULE 10 â€” Metrics: GET /api/v1/metrics/summary
  MODULE 11 â€” Stream: GET /api/v1/jobs/{job_id}/stream (SSE)
  MODULE 14 â€” System: GET /api/v1/system/performance
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.app.api.deps.identity import resolve_current_user_id, require_owned_run

from backend.app.config import get_settings
from backend.app.utils.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)

# â”€â”€ Sub-routers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

cache_router = APIRouter(prefix="/cache", tags=["Cache"])
models_router = APIRouter(prefix="/models", tags=["Models"])
analytics_router = APIRouter(prefix="/jobs", tags=["Analytics"])
region_router = APIRouter(prefix="/region", tags=["Region"])
metrics_router = APIRouter(prefix="/metrics", tags=["Metrics"])
system_router = APIRouter(prefix="/system", tags=["System"])
stream_router = APIRouter(prefix="/jobs", tags=["Streaming"])


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 4 â€” Cache
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@cache_router.get("/status")
async def cache_status(current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Return cache statistics for L1 (memory) and L2 (Redis) tiers."""
    from backend.app.services.tile_cache import cache_status as _status
    return _status()


@cache_router.post("/clear")
async def cache_clear(layer_id: Optional[str] = None, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Clear the tile cache. Optionally target a specific layer."""
    from backend.app.services.tile_cache import cache_invalidate
    cleared = cache_invalidate(layer_id=layer_id)
    return {"cleared_items": cleared, "layer_id": layer_id or "all"}


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 5 â€” Model Registry
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

MODEL_REGISTRY = [
    {
        "id": "rife",
        "name": "RIFE",
        "description": "Real-time Intermediate Flow Estimation. Primary interpolation engine.",
        "type": "deep_learning",
        "priority": 1,
        "gpu_required": True,
        "fallback_to": "optical_flow",
        "loaded": None,  # populated at runtime
    },
    {
        "id": "film",
        "name": "FILM",
        "description": "Frame Interpolation for Large Motion (Google). Handles large displacements.",
        "type": "deep_learning",
        "priority": 2,
        "gpu_required": True,
        "fallback_to": "optical_flow",
        "loaded": None,
    },
    {
        "id": "optical_flow",
        "name": "Lucas-Kanade Optical Flow",
        "description": "Classical dense optical flow. CPU-compatible fallback.",
        "type": "classical",
        "priority": 3,
        "gpu_required": False,
        "fallback_to": None,
        "loaded": True,
    },
    {
        "id": "lk_fallback",
        "name": "LK Pyramid Fallback",
        "description": "Lightweight pyramid LK flow for degraded frames.",
        "type": "classical",
        "priority": 4,
        "gpu_required": False,
        "fallback_to": None,
        "loaded": True,
    },
]


@models_router.get("")
async def list_models(current_user_id: str = Depends(resolve_current_user_id)) -> list[dict]:
    """List all available interpolation models with their load status."""
    from backend.app.services.interpolation import get_engine
    result = []
    for m in MODEL_REGISTRY:
        entry = dict(m)
        if m["id"] in ("rife", "film"):
            try:
                eng = get_engine(m["id"])
                entry["loaded"] = eng.is_loaded
            except Exception:
                entry["loaded"] = False
        result.append(entry)
    return result


@models_router.get("/{model_id}")
async def get_model_info(model_id: str, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Get detailed info for a specific model."""
    for m in MODEL_REGISTRY:
        if m["id"] == model_id:
            return dict(m)
    raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 6 â€” Confidence Maps
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@analytics_router.get("/{job_id}/confidence_map/{frame_idx}")
async def get_confidence_map(job_id: str, frame_idx: int, current_user_id: str = Depends(resolve_current_user_id)) -> FileResponse:
    """Get the pixel-wise confidence/uncertainty map for a frame."""
    require_owned_run(job_id, current_user_id)
    conf_dir = settings.exports_dir / job_id / "confidence_maps"
    path = conf_dir / f"confidence_map_{frame_idx:04d}.png"

    if not path.exists():
        # Try to generate on-demand
        frame_path = settings.exports_dir / job_id / "frames" / f"frame_{frame_idx:04d}.png"
        if not frame_path.exists():
            raise HTTPException(status_code=404, detail=f"Frame {frame_idx} not found for job {job_id}")

        try:
            import numpy as np
            from PIL import Image
            from backend.app.services.uncertainty_maps import generate_uncertainty_map, save_confidence_map

            img = np.array(Image.open(str(frame_path)).convert("RGB"), dtype=np.float32) / 255.0

            # Load ref frames
            prev_path = settings.exports_dir / job_id / "frames" / f"frame_{max(0, frame_idx-1):04d}.png"
            next_path = settings.exports_dir / job_id / "frames" / f"frame_{frame_idx+1:04d}.png"

            ref_a = np.array(Image.open(str(prev_path)).convert("RGB"), dtype=np.float32) / 255.0 if prev_path.exists() else img
            ref_b = np.array(Image.open(str(next_path)).convert("RGB"), dtype=np.float32) / 255.0 if next_path.exists() else img

            conf_map = generate_uncertainty_map(img, ref_a, ref_b, t_pos=0.5)
            conf_dir.mkdir(parents=True, exist_ok=True)
            save_confidence_map(conf_map, conf_dir, frame_idx)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Confidence map generation failed: {exc}")

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Confidence map not found for frame {frame_idx}")

    return FileResponse(str(path), media_type="image/png")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 7 â€” Change Maps
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@analytics_router.get("/{job_id}/change_map/{frame_idx}")
async def get_change_map(job_id: str, frame_idx: int, current_user_id: str = Depends(resolve_current_user_id)) -> FileResponse:
    """Get the change/difference map between frames {frame_idx-1} and {frame_idx}."""
    require_owned_run(job_id, current_user_id)
    change_dir = settings.exports_dir / job_id / "change_maps"
    path = change_dir / f"change_map_{frame_idx:04d}.png"

    if not path.exists():
        # Generate on-demand
        frames_dir = settings.exports_dir / job_id / "frames"
        prev_path = frames_dir / f"frame_{max(0, frame_idx-1):04d}.png"
        curr_path = frames_dir / f"frame_{frame_idx:04d}.png"

        if not curr_path.exists():
            raise HTTPException(status_code=404, detail=f"Frame {frame_idx} not found")

        try:
            import numpy as np
            from PIL import Image
            from backend.app.services.change_anomaly import compute_change_map, save_change_map

            curr = np.array(Image.open(str(curr_path)).convert("RGB"), dtype=np.float32) / 255.0
            prev = np.array(Image.open(str(prev_path)).convert("RGB"), dtype=np.float32) / 255.0 if prev_path.exists() else curr

            cm = compute_change_map(prev, curr, frame_index=frame_idx)
            change_dir.mkdir(parents=True, exist_ok=True)
            save_change_map(cm, change_dir)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Change map generation failed: {exc}")

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Change map not found for frame {frame_idx}")

    return FileResponse(str(path), media_type="image/png")


@analytics_router.get("/{job_id}/change_stats")
async def get_change_stats(job_id: str, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Get change detection statistics for all frames in a job."""
    require_owned_run(job_id, current_user_id)

    frames_dir = settings.exports_dir / job_id / "frames"
    if not frames_dir.exists():
        raise HTTPException(status_code=404, detail=f"Job {job_id} has no exported frames")

    try:
        import numpy as np
        from PIL import Image
        from backend.app.services.change_anomaly import compute_change_map

        frame_paths = sorted(frames_dir.glob("frame_*.png"))
        stats = []
        prev_frame = None

        for path in frame_paths:
            frame_idx = int(path.stem.split("_")[1])
            img = np.array(Image.open(str(path)).convert("RGB"), dtype=np.float32) / 255.0

            if prev_frame is not None:
                cm = compute_change_map(prev_frame, img, frame_index=frame_idx)
                stats.append({
                    "frame_index": frame_idx,
                    "motion_magnitude": round(cm.motion_magnitude, 4),
                    "change_percentage": round(cm.change_percentage, 2),
                })
            prev_frame = img

        return {"job_id": job_id, "frame_stats": stats, "total_frames": len(frame_paths)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@analytics_router.get("/{job_id}/anomaly_report")
async def get_anomaly_report(job_id: str, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Get anomaly detection report for all frames in a job."""
    require_owned_run(job_id, current_user_id)
    frames_dir = settings.exports_dir / job_id / "frames"
    if not frames_dir.exists():
        raise HTTPException(status_code=404, detail=f"Job {job_id} has no exported frames")

    try:
        import numpy as np
        from PIL import Image
        from backend.app.services.change_anomaly import (
            compute_change_map, run_anomaly_detection
        )

        frame_paths = sorted(frames_dir.glob("frame_*.png"))
        frames = [
            np.array(Image.open(str(p)).convert("RGB"), dtype=np.float32) / 255.0
            for p in frame_paths
        ]

        # Compute change maps
        change_maps = []
        for i in range(1, len(frames)):
            cm = compute_change_map(frames[i-1], frames[i], frame_index=i)
            change_maps.append(cm)

        # Run anomaly detection
        anomaly_results = run_anomaly_detection(frames, change_maps, job_id)

        return {
            "job_id": job_id,
            "total_frames": len(frames),
            "anomaly_count": sum(1 for r in anomaly_results if r.label.value == "ANOMALY"),
            "normal_count": sum(1 for r in anomaly_results if r.label.value == "NORMAL"),
            "frames": [
                {
                    "frame_index": r.frame_index,
                    "label": r.label.value,
                    "anomaly_score": round(r.anomaly_score, 4),
                    "intensity_spike": r.intensity_spike,
                    "motion_anomaly": r.motion_anomaly,
                    "structure_anomaly": r.structure_anomaly,
                    "details": r.details,
                }
                for r in anomaly_results
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 9 â€” Region Query
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

class RegionQueryRequest(BaseModel):
    job_id: str
    bbox: list[float] = Field(..., min_length=4, max_length=4, description="[minLon, minLat, maxLon, maxLat]")
    time_start: datetime
    time_end: datetime


@region_router.post("/query")
async def query_region(request: RegionQueryRequest, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Compute spatial statistics for a geo-region within a job's time range."""
    require_owned_run(request.job_id, current_user_id)
    from backend.app.services.geo_analytics import query_region as _query
    from dataclasses import asdict

    result = _query(
        job_id=request.job_id,
        region_bbox=request.bbox,
        time_start=request.time_start,
        time_end=request.time_end,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No frames found for job {request.job_id} in the specified time range",
        )

    return asdict(result)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 10 â€” Metrics Dashboard
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@metrics_router.get("/summary")
async def metrics_summary(current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Get aggregated metrics across all processed jobs."""
    from backend.app.services.geo_analytics import load_global_metrics
    from dataclasses import asdict

    gm = load_global_metrics()
    return asdict(gm)


@metrics_router.get("/job/{job_id}")
async def job_metrics(job_id: str, current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Get per-job quality metrics."""
    require_owned_run(job_id, current_user_id)
    from backend.app.services.job_manager import get_job

    record = get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if record.status != "COMPLETED" or record.result is None:
        raise HTTPException(status_code=202, detail=f"Job not yet completed (status: {record.status})")

    result = record.result
    return {
        "job_id": job_id,
        "metrics": result.get("metrics"),
        "total_frames": result.get("frames", []),
        "completed_at": record.completed_at,
    }


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 11 â€” SSE Streaming
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@stream_router.get("/{job_id}/stream")
async def stream_frames(job_id: str, request: Request, current_user_id: str = Depends(resolve_current_user_id)) -> StreamingResponse:
    """Server-Sent Events stream for real-time frame delivery and job progress."""
    require_owned_run(job_id, current_user_id)

    async def _event_generator():
        from backend.app.services.job_manager import get_job, get_job_logs
        import base64

        sent_frame_indices = set()
        last_log_count = 0
        frames_dir = settings.exports_dir / job_id / "frames"

        yield f"data: {json.dumps({'type': 'connected', 'job_id': job_id})}\n\n"

        while True:
            if await request.is_disconnected():
                break

            record = get_job(job_id)
            if record is None:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Job not found'})}\n\n"
                break

            # Send progress update
            yield f"data: {json.dumps({'type': 'progress', 'status': record.status, 'progress': record.progress, 'stage': record.current_stage, 'message': record.message})}\n\n"

            # Send new log entries
            logs = get_job_logs(job_id)
            new_logs = logs[last_log_count:]
            for log in new_logs:
                yield f"data: {json.dumps({'type': 'log', **log})}\n\n"
            last_log_count = len(logs)

            # Stream new frames as they appear
            if frames_dir.exists():
                frame_paths = sorted(frames_dir.glob("frame_*.png"))
                for path in frame_paths:
                    idx = int(path.stem.split("_")[1])
                    if idx not in sent_frame_indices:
                        try:
                            with open(str(path), "rb") as f:
                                raw = f.read()
                            b64 = base64.b64encode(raw).decode()
                            yield f"data: {json.dumps({'type': 'frame', 'frame_index': idx, 'data': b64, 'mime': 'image/png'})}\n\n"
                            sent_frame_indices.add(idx)
                        except Exception:
                            pass

            # Terminal states
            if record.status in ("COMPLETED", "FAILED", "CANCELLED"):
                yield f"data: {json.dumps({'type': 'done', 'status': record.status, 'error': record.error})}\n\n"
                break

            await asyncio.sleep(1.5)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODULE 14 â€” System Performance
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@system_router.get("/config")
async def get_system_config(current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Expose system configuration mode for frontend adaptation."""
    return {
        "mode": settings.aether_mode,
        "version": "2.0.0",
        "gpu_support": True,
        "is_dev_preview": settings.aether_mode != "production" and settings.dev_preview_enabled,
        "features": {
            "auth": settings.aether_mode == "production",
            "queuing": settings.aether_mode == "production",
            "mosdac_offline": settings.aether_mode == "development"
        }
    }


@system_router.get("/session/status")
async def get_session_status(session_id: str = Query(""), current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Check the status of the current user session (granted vs waiting)."""
    from backend.app.services.session_lock import lock_service
    return lock_service.get_status(session_id)


@system_router.post("/session/heartbeat")
async def post_session_heartbeat(
    session_id: str = Query(""),
    phase: str = Query(None, description="'active' or 'grace'"),
    current_user_id: str = Depends(resolve_current_user_id),
) -> dict:
    """Extend the session lease. Must be called every â‰¤30 s to stay alive.

    - In 'active' phase (no pipeline run yet): TTL = 45 s.
    - In 'grace' phase (post-pipeline, exporting): TTL = 300 s.
    """
    from backend.app.services.session_lock import lock_service
    return lock_service.heartbeat(session_id, phase=phase)


@system_router.post("/session/start_grace")
async def post_session_start_grace(session_id: str = Query(""), current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Switch session to the post-pipeline grace window (5 min export/download window).

    Frontend should call this immediately after pipeline completion.
    Heartbeats in grace phase extend the TTL to 5 min instead of 45 s.
    """
    from backend.app.services.session_lock import lock_service
    return lock_service.start_grace(session_id)


@system_router.post("/session/release")
async def post_session_release(session_id: str = Query(""), current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Release an active or queued session when a user leaves the dashboard."""
    from backend.app.services.session_lock import lock_service
    released = lock_service.release(session_id)
    return {"status": "released" if released else "noop"}


@system_router.get("/session/queue")
async def get_session_queue(current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Return the current queue depth (number of users waiting)."""
    from backend.app.services.session_lock import lock_service
    return {"queue_length": lock_service.queue_length()}



@system_router.get("/performance")
async def system_performance(current_user_id: str = Depends(resolve_current_user_id)) -> dict:
    """Get real-time system performance metrics (GPU, CPU, RAM, job queue)."""
    from backend.app.services.performance import collect_system_performance, to_dict
    perf = collect_system_performance()
    return to_dict(perf)


@system_router.get("/providers")
async def list_satellite_providers(current_user_id: str = Depends(resolve_current_user_id)) -> list[dict]:
    """List all satellite data providers and their availability."""
    from backend.app.services.satellite_providers import list_providers
    return list_providers()

