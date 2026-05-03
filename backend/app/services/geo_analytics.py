"""AetherGIS — Geo-Region Query Engine + Metric Aggregation (MODULE 9 + 10).

MODULE 9 — Region Query:
  • POST /api/v1/region/query
  • Accepts polygon or bbox + time range
  • Returns statistics: avg motion, cloud coverage %, region metrics

MODULE 10 — Metric Aggregation:
  • Aggregates per-job metrics into global stats
  • Persists to backend/data/metrics/global.json
  • GET /api/v1/metrics/summary
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from backend.app.config import get_settings
from backend.app.utils.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)

GLOBAL_METRICS_PATH = settings.data_dir / "metrics" / "global.json"


# ── Region Query ─────────────────────────────────────────────────────────────

@dataclass
class RegionStats:
    job_id: str
    query_bbox: list[float]
    time_range_start: str
    time_range_end: str
    total_frames: int
    frames_in_region: int
    avg_motion_magnitude: float
    cloud_coverage_pct: float
    avg_brightness: float
    avg_confidence: Optional[float]
    anomaly_count: int
    computed_at: str


def _bbox_intersect(region: list[float], frame_bbox: list[float]) -> bool:
    """Check if region bbox intersects frame bbox."""
    # region: [minLon, minLat, maxLon, maxLat]
    # frame_bbox: same
    r_minx, r_miny, r_maxx, r_maxy = region
    f_minx, f_miny, f_maxx, f_maxy = frame_bbox
    return not (r_maxx < f_minx or r_minx > f_maxx or r_maxy < f_miny or r_miny > f_maxy)


def _crop_to_region(frame: np.ndarray, frame_bbox: list[float], region_bbox: list[float]) -> np.ndarray:
    """Crop frame array to the portion covering region_bbox."""
    f_minx, f_miny, f_maxx, f_maxy = frame_bbox
    r_minx, r_miny, r_maxx, r_maxy = region_bbox

    h, w = frame.shape[:2]
    fx_range = f_maxx - f_minx
    fy_range = f_maxy - f_miny

    # Clamp region to frame bbox
    cx_min = max(r_minx, f_minx)
    cy_min = max(r_miny, f_miny)
    cx_max = min(r_maxx, f_maxx)
    cy_max = min(r_maxy, f_maxy)

    # Pixel coordinates
    px0 = int((cx_min - f_minx) / fx_range * w)
    py0 = int((1 - (cy_max - f_miny) / fy_range) * h)
    px1 = int((cx_max - f_minx) / fx_range * w)
    py1 = int((1 - (cy_min - f_miny) / fy_range) * h)

    px0, px1 = max(0, px0), min(w, px1)
    py0, py1 = max(0, py0), min(h, py1)

    if px0 >= px1 or py0 >= py1:
        return frame

    return frame[py0:py1, px0:px1]


def _estimate_cloud_coverage(frame: np.ndarray) -> float:
    """Estimate cloud coverage % via brightness threshold."""
    brightness = frame.mean(axis=-1)
    cloud_threshold = 0.7  # Bright pixels = likely cloud
    cloud_fraction = float((brightness > cloud_threshold).mean() * 100)
    return round(cloud_fraction, 2)


def query_region(
    job_id: str,
    region_bbox: list[float],
    time_start: datetime,
    time_end: datetime,
) -> Optional[RegionStats]:
    """Query a spatial region within a job's exported frames."""

    export_dir = settings.exports_dir / job_id
    if not export_dir.exists():
        return None

    # Load metadata sidecar
    meta_path = export_dir / "metadata.json"
    if not meta_path.exists():
        return None

    with open(meta_path) as f:
        meta_list = json.load(f)

    frames_dir = export_dir / "frames"
    if not frames_dir.exists():
        return None

    # Filter frames by time range
    from datetime import timezone as tz
    filtered_meta = []
    for m in meta_list:
        ts_str = m.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=tz.utc)
            if time_start <= ts <= time_end:
                filtered_meta.append(m)
        except Exception:
            pass

    if not filtered_meta:
        return None

    # Load and crop frames
    motion_magnitudes = []
    cloud_coverages = []
    brightnesses = []
    confidences = []
    anomaly_count = 0
    frames_in_region = 0

    prev_frame = None
    for m in filtered_meta:
        idx = m.get("frame_index", 0)
        frame_path = frames_dir / f"frame_{idx:04d}.png"
        if not frame_path.exists():
            continue

        try:
            from PIL import Image
            img = Image.open(str(frame_path)).convert("RGB")
            frame_arr = np.array(img, dtype=np.float32) / 255.0

            # Use job bbox from metadata or fallback
            # For now use full frame as region (can be refined with spatial metadata)
            region_frame = frame_arr
            frames_in_region += 1

            # Cloud coverage
            cloud_coverages.append(_estimate_cloud_coverage(region_frame))
            brightnesses.append(float(region_frame.mean()))

            # Confidence
            conf = m.get("confidence_score")
            if conf is not None:
                confidences.append(float(conf))

            # Motion magnitude (requires consecutive frames)
            if prev_frame is not None:
                try:
                    import cv2 as cv
                    a_gray = (prev_frame.mean(axis=-1) * 255).astype(np.uint8)
                    b_gray = (region_frame.mean(axis=-1) * 255).astype(np.uint8)
                    flow = cv.calcOpticalFlowFarneback(a_gray, b_gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
                    mag = float(np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2).mean())
                    motion_magnitudes.append(mag)
                except Exception:
                    pass

            prev_frame = region_frame

        except Exception as exc:
            logger.warning("Frame load failed in region query", frame=idx, error=str(exc))

    avg_motion = float(np.mean(motion_magnitudes)) if motion_magnitudes else 0.0
    avg_cloud = float(np.mean(cloud_coverages)) if cloud_coverages else 0.0
    avg_brightness = float(np.mean(brightnesses)) if brightnesses else 0.0
    avg_conf = float(np.mean(confidences)) if confidences else None

    return RegionStats(
        job_id=job_id,
        query_bbox=region_bbox,
        time_range_start=time_start.isoformat(),
        time_range_end=time_end.isoformat(),
        total_frames=len(meta_list),
        frames_in_region=frames_in_region,
        avg_motion_magnitude=round(avg_motion, 4),
        cloud_coverage_pct=round(avg_cloud, 2),
        avg_brightness=round(avg_brightness, 4),
        avg_confidence=round(avg_conf, 4) if avg_conf is not None else None,
        anomaly_count=anomaly_count,
        computed_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Global Metric Aggregation ─────────────────────────────────────────────────

@dataclass
class GlobalMetrics:
    total_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    total_frames_generated: int = 0
    total_interpolated_frames: int = 0
    avg_psnr: Optional[float] = None
    avg_ssim: Optional[float] = None
    avg_confidence: Optional[float] = None
    rejection_rate: float = 0.0
    anomaly_rate: float = 0.0
    last_updated: str = ""


def load_global_metrics() -> GlobalMetrics:
    GLOBAL_METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if GLOBAL_METRICS_PATH.exists():
        try:
            with open(GLOBAL_METRICS_PATH) as f:
                data = json.load(f)
            return GlobalMetrics(**{k: v for k, v in data.items() if k in GlobalMetrics.__dataclass_fields__})
        except Exception:
            pass
    return GlobalMetrics()


def save_global_metrics(metrics: GlobalMetrics) -> None:
    GLOBAL_METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    metrics.last_updated = datetime.now(timezone.utc).isoformat()
    with open(GLOBAL_METRICS_PATH, "w") as f:
        json.dump(asdict(metrics), f, indent=2)


def update_global_metrics_from_job(job_result: dict) -> None:
    """Ingest per-job metrics into global aggregation store."""
    gm = load_global_metrics()
    gm.total_jobs += 1

    status = job_result.get("status", "")
    if status == "COMPLETED":
        gm.completed_jobs += 1
    elif status == "FAILED":
        gm.failed_jobs += 1
        save_global_metrics(gm)
        return

    job_metrics = job_result.get("metrics", {}) or {}
    total = job_metrics.get("total_frames", 0)
    interp = job_metrics.get("interpolated_frames", 0)
    rejected = job_metrics.get("rejected_count", 0)
    gm.total_frames_generated += total
    gm.total_interpolated_frames += interp

    # Running average updates
    n = gm.completed_jobs or 1

    def _running_avg(current: Optional[float], new_val: Optional[float], n: int) -> Optional[float]:
        if new_val is None:
            return current
        if current is None:
            return new_val
        return current + (new_val - current) / n

    gm.avg_psnr = _running_avg(gm.avg_psnr, job_metrics.get("avg_psnr"), n)
    gm.avg_ssim = _running_avg(gm.avg_ssim, job_metrics.get("avg_ssim"), n)

    # Confidence from frame breakdown
    high_conf = job_metrics.get("high_confidence_count", 0)
    med_conf = job_metrics.get("medium_confidence_count", 0)
    low_conf = job_metrics.get("low_confidence_count", 0)
    total_conf_frames = high_conf + med_conf + low_conf
    if total_conf_frames > 0:
        synthetic_conf = (high_conf * 0.9 + med_conf * 0.6 + low_conf * 0.3) / total_conf_frames
        gm.avg_confidence = _running_avg(gm.avg_confidence, synthetic_conf, n)

    # Rejection rate
    if total > 0:
        job_rejection = rejected / total
        gm.rejection_rate = gm.rejection_rate + (job_rejection - gm.rejection_rate) / n

    save_global_metrics(gm)
    logger.info("Global metrics updated", total_jobs=gm.total_jobs, completed=gm.completed_jobs)
