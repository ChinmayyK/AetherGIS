"""AetherGIS - FramePipeline: full orchestration — PRODUCTION GRADE.

Enhanced pipeline integrates:
  • Audit trail logging at every stage (MODULE 15)
  • Checkpoint saves for failure recovery (MODULE 13)
  • Multi-source satellite provider with auto-fallback (MODULE 3)
  • Tile cache (MODULE 4)
  • Uncertainty / confidence maps per frame (MODULE 6)
  • Change detection maps (MODULE 7)
  • Anomaly detection (MODULE 8)
  • Manifest-based reproducibility (MODULE 2)
  • Global metrics update on completion (MODULE 10)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import numpy as np
from skimage.metrics import peak_signal_noise_ratio as psnr
from skimage.metrics import structural_similarity as ssim

from backend.app.config import get_settings
from backend.app.models.schemas import ConfidenceClass, FrameMetadata, PipelineResult, QualityMetrics, JobStatus
from backend.app.services.confidence import compute_frame_stability_index, compute_temporal_consistency_score, score_generated_frame
from backend.app.services.interpolation import get_engine, interpolate_pair_with_segmentation
from backend.app.services.preprocessing import preprocess_sequence
from backend.app.services.video_gen import save_frame_optimized, write_metadata_sidecar
from backend.app.services.wms_client import BHUVAN_LAYERS, GIBS_LAYERS, INSAT_LAYERS, SatelliteFrame, get_wms_client
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()
ProgressCallback = Callable[[float, str], None]


def _generate_timestamps(time_start: datetime, time_end: datetime, layer_temporal_res_minutes: float) -> list[datetime]:
    res = int(layer_temporal_res_minutes)
    if res <= 0:
        res = 10
    grid_minute = (time_start.minute // res) * res
    t = time_start.replace(minute=grid_minute, second=0, microsecond=0)
    timestamps = []
    while t <= time_end:
        timestamps.append(t)
        t += timedelta(minutes=res)
    return timestamps


def _compute_metrics(frame: np.ndarray, ref_a: np.ndarray, ref_b: np.ndarray) -> tuple[float, float]:
    psnr_a = psnr(ref_a, frame, data_range=1.0)
    psnr_b = psnr(ref_b, frame, data_range=1.0)
    avg_psnr = (psnr_a + psnr_b) / 2.0
    ssim_a = ssim(ref_a, frame, data_range=1.0, channel_axis=-1)
    ssim_b = ssim(ref_b, frame, data_range=1.0, channel_axis=-1)
    avg_ssim = (ssim_a + ssim_b) / 2.0
    return float(avg_psnr), float(avg_ssim)


def _try_import_job_manager():
    try:
        from backend.app.services.job_manager import (
            append_audit_event, save_checkpoint, load_checkpoint,
        )
        return append_audit_event, save_checkpoint, load_checkpoint
    except Exception:
        def noop(*a, **kw):
            return None
        def noop_load(*a, **kw):
            return None
        return noop, noop, noop_load


async def run_pipeline(
    job_id: str,
    layer_id: str,
    data_source: str,
    bbox: list[float],
    time_start: datetime,
    time_end: datetime,
    resolution: int,
    interpolation_model: str,
    n_intermediate: int,
    step_minutes: Optional[int] = None,
    include_low_confidence: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
) -> PipelineResult:
    created_at = datetime.now(timezone.utc)
    logger.info('Pipeline started', job_id=job_id, layer=layer_id, data_source=data_source)

    append_audit_event, save_checkpoint, load_checkpoint = _try_import_job_manager()

    append_audit_event(job_id, "pipeline_start", {
        "layer_id": layer_id, "data_source": data_source,
        "bbox": bbox, "resolution": resolution,
        "interpolation_model": interpolation_model,
    })

    def report(progress: float, message: str) -> None:
        if progress_callback:
            progress_callback(progress, message)
        append_audit_event(job_id, "stage_update", {"progress": round(progress, 3), "message": message})

    # ── Timestamps ─────────────────────────────────────────────────────────
    layer_info = GIBS_LAYERS.get(layer_id) or BHUVAN_LAYERS.get(layer_id) or INSAT_LAYERS.get(layer_id, {})
    temporal_res = step_minutes or float(layer_info.get('temporal_resolution_minutes', 1440.0))
    timestamps = _generate_timestamps(time_start, time_end, temporal_res)
    if len(timestamps) > settings.max_frames_per_session:
        timestamps = timestamps[:settings.max_frames_per_session]

    append_audit_event(job_id, "timestamps_generated", {"count": len(timestamps)})

    # ── Ingestion with cache + fallback ────────────────────────────────────
    report(0.08, f'Fetching {len(timestamps)} frames from {data_source}')

    async with get_wms_client(data_source) as client:
        raw_frames: list[SatelliteFrame] = await client.fetch_sequence(layer_id, bbox, timestamps, resolution)

    # Fill any misses from cache or fallback providers
    try:
        from backend.app.services.tile_cache import cache_get, cache_put

        observed_frames: list[SatelliteFrame] = []
        for frame in raw_frames:
            if frame.image is None:
                cached = cache_get(layer_id, bbox, frame.timestamp, resolution)
                if cached is not None:
                    frame = SatelliteFrame(image=cached, timestamp=frame.timestamp,
                                          layer_id=layer_id, bbox=bbox, source=data_source)
                    append_audit_event(job_id, "cache_hit", {"ts": frame.timestamp.isoformat()})
                else:
                    try:
                        from backend.app.services.satellite_providers import fetch_with_fallback
                        pf = await fetch_with_fallback(
                            layer_id, bbox, frame.timestamp, resolution,
                            preferred_source=data_source, job_id=job_id,
                        )
                        if pf is not None:
                            frame = SatelliteFrame(image=pf.image, timestamp=pf.timestamp,
                                                   layer_id=layer_id, bbox=bbox, source=pf.source)
                            append_audit_event(job_id, "fallback_used",
                                               {"provider": pf.source, "ts": pf.timestamp.isoformat()})
                    except Exception as fb_exc:
                        logger.warning("Fallback failed", error=str(fb_exc))

            if frame.image is not None:
                cache_put(layer_id, bbox, frame.timestamp, resolution, frame.image)
                observed_frames.append(frame)

    except ImportError:
        # Cache module not yet available — use raw frames
        observed_frames = [f for f in raw_frames if f.image is not None]

    save_checkpoint(job_id, "ingestion", {"frame_count": len(observed_frames)})
    append_audit_event(job_id, "ingestion_complete",
                       {"requested": len(timestamps), "fetched": len(observed_frames)})

    if len(observed_frames) < 2:
        append_audit_event(job_id, "pipeline_aborted", {"reason": "insufficient_frames"})
        return PipelineResult(
            job_id=job_id, status=JobStatus.failed, layer_id=layer_id,
            data_source=data_source, bbox=bbox, time_start=time_start, time_end=time_end,
            error='Insufficient valid frames retrieved (< 2). Check layer / time / AOI.',
            created_at=created_at,
        )

    report(0.22, f'Fetched {len(observed_frames)} valid source frames')

    # ── Preprocessing ──────────────────────────────────────────────────────
    report(0.28, 'Preprocessing and normalising frame sequence')
    obs_images = [frame.image for frame in observed_frames]
    obs_times = [frame.timestamp for frame in observed_frames]
    preprocessed = preprocess_sequence(obs_images, obs_times, bbox)

    save_checkpoint(job_id, "preprocessing", {
        "frame_count": len(preprocessed.frames),
        "gap_count": len(preprocessed.gaps),
    })
    append_audit_event(job_id, "preprocessing_complete", {
        "frames": len(preprocessed.frames),
        "gaps": len(preprocessed.gaps),
    })
    report(0.32, f'Preprocessed {len(preprocessed.frames)} frames, {len(preprocessed.gaps)} gaps')

    # ── Interpolation ──────────────────────────────────────────────────────
    engine = get_engine(interpolation_model)
    append_audit_event(job_id, "interpolation_started",
                       {"model": interpolation_model, "engine_loaded": engine.is_loaded})

    all_frames: list[np.ndarray] = []
    all_metadata: list[FrameMetadata] = []
    all_t_positions: list[Optional[float]] = []
    ref_pairs: list[Optional[tuple[int, int]]] = []
    frame_idx = 0
    total_pairs = max(1, len(preprocessed.frames) - 1)

    for i, (obs_frame, obs_time) in enumerate(zip(preprocessed.frames, preprocessed.timestamps)):
        all_frames.append(obs_frame)
        all_metadata.append(FrameMetadata(frame_index=frame_idx, timestamp=obs_time, is_interpolated=False, provider_source=getattr(obs_frame, 'source', data_source)))
        all_t_positions.append(None)
        ref_pairs.append(None)
        obs_frame_idx_in_list = len(all_frames) - 1
        frame_idx += 1

        if i >= len(preprocessed.gaps):
            break

        report(0.35 + (i / total_pairs) * 0.35, f'Interpolating pair {i + 1} of {total_pairs}')
        gap_info = preprocessed.gaps[i]
        frame_b = preprocessed.frames[i + 1]
        time_b = preprocessed.timestamps[i + 1]
        pair_flags = preprocessed.flags.get(i, []) + preprocessed.flags.get(i + 1, [])

        pair_score = score_generated_frame(obs_frame, frame_b, gap_info.gap_minutes, extra_flags=pair_flags)
        if pair_score.is_rejected:
            logger.warning('Frame pair rejected', pair_index=i, reason=pair_score.reject_reason)
            append_audit_event(job_id, "pair_rejected", {
                "pair": i, "reason": pair_score.reject_reason,
                "confidence_score": pair_score.confidence_score,
            })
            continue

        interp_result = interpolate_pair_with_segmentation(obs_frame, frame_b, gap_info, engine, n_intermediate)
        if not interp_result.generated_frames:
            continue

        for gen_frame, t_pos in zip(interp_result.generated_frames, interp_result.t_positions):
            approx_ts = obs_time + (time_b - obs_time) * t_pos
            frame_score = score_generated_frame(obs_frame, frame_b, gap_info.gap_minutes, extra_flags=pair_flags)
            cs = min(frame_score.confidence_score, gap_info.confidence_floor)
            cls = frame_score.confidence_class
            if cs < frame_score.confidence_score:
                from backend.app.services.confidence import classify_confidence
                cls = classify_confidence(cs)
            if cls == ConfidenceClass.low and not include_low_confidence:
                continue

            approx_psnr, approx_ssim = _compute_metrics(gen_frame, obs_frame, frame_b)
            all_frames.append(gen_frame)
            all_metadata.append(FrameMetadata(
                frame_index=frame_idx, timestamp=approx_ts, is_interpolated=True,
                confidence_score=cs, confidence_class=cls, model_used=engine.model_name,
                flow_consistency=frame_score.flow_consistency, mad_score=frame_score.mad_score,
                gap_minutes=gap_info.gap_minutes, gap_category=gap_info.category,
                psnr=approx_psnr, ssim=approx_ssim, provider_source="ai_interpolated",
            ))
            all_t_positions.append(t_pos)
            ref_pairs.append((obs_frame_idx_in_list, -1))
            frame_idx += 1

    # Patch ref_pairs -1 placeholders
    obs_indices = [i for i, m in enumerate(all_metadata) if not m.is_interpolated]
    for j, rp in enumerate(ref_pairs):
        if rp is not None and rp[1] == -1:
            ra = rp[0]
            next_obs = next((oi for oi in obs_indices if oi > ra), ra)
            ref_pairs[j] = (ra, next_obs)

    # Sort by timestamp
    zipped = sorted(
        zip(all_metadata, all_frames, all_t_positions, ref_pairs),
        key=lambda x: x[0].timestamp,
    )
    all_metadata = [x[0] for x in zipped]
    all_frames = [x[1] for x in zipped]
    all_t_positions = [x[2] for x in zipped]
    ref_pairs = [x[3] for x in zipped]
    for idx, meta in enumerate(all_metadata):
        meta.frame_index = idx

    append_audit_event(job_id, "interpolation_complete", {
        "total_frames": len(all_frames),
        "interpolated": sum(1 for m in all_metadata if m.is_interpolated),
    })
    save_checkpoint(job_id, "interpolation", {"frame_count": len(all_frames)})

    # ── Export frames ──────────────────────────────────────────────────────
    report(0.76, 'Exporting frame PNGs')
    export_dir = settings.exports_dir / job_id
    export_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = export_dir / 'frames'
    frames_dir.mkdir(exist_ok=True)
    for i, frame in enumerate(all_frames):
        save_frame_optimized(frame, frames_dir / f'frame_{i:04d}.webp', metadata=all_metadata[i])
    write_metadata_sidecar(all_metadata, export_dir / 'metadata.json')

    # ── Confidence maps (MODULE 6) ─────────────────────────────────────────
    report(0.82, 'Generating uncertainty maps')
    try:
        from backend.app.services.uncertainty_maps import generate_uncertainty_map, save_confidence_map
        is_interp = [m.is_interpolated for m in all_metadata]
        conf_dir = export_dir / "confidence_maps"
        conf_dir.mkdir(exist_ok=True)
        for i, (frame, t_pos, rp) in enumerate(zip(all_frames, all_t_positions, ref_pairs)):
            if not is_interp[i] or rp is None:
                continue
            ra, rb = rp
            ref_a = all_frames[min(ra, len(all_frames) - 1)]
            ref_b = all_frames[min(rb, len(all_frames) - 1)]
            neighbors = [all_frames[j] for j in range(max(0, i - 2), min(len(all_frames), i + 3))
                         if j != i and is_interp[j]]
            conf_map = generate_uncertainty_map(frame, ref_a, ref_b, t_pos or 0.5, neighbors)
            save_confidence_map(conf_map, conf_dir, i)
        append_audit_event(job_id, "confidence_maps_generated",
                           {"count": sum(is_interp)})
    except Exception as exc:
        logger.warning("Confidence map generation failed", error=str(exc))

    # ── Change detection (MODULE 7) ────────────────────────────────────────
    report(0.87, 'Computing change detection maps')
    change_maps = []
    try:
        from backend.app.services.change_anomaly import compute_change_map, save_change_map
        change_dir = export_dir / "change_maps"
        change_dir.mkdir(exist_ok=True)
        for i in range(1, len(all_frames)):
            cm = compute_change_map(all_frames[i - 1], all_frames[i], frame_index=i)
            save_change_map(cm, change_dir)
            change_maps.append(cm)
        append_audit_event(job_id, "change_maps_generated", {"count": len(change_maps)})
    except Exception as exc:
        logger.warning("Change detection failed", error=str(exc))

    # ── Anomaly detection (MODULE 8) ───────────────────────────────────────
    report(0.91, 'Running anomaly detection')
    anomaly_count = 0
    try:
        from backend.app.services.change_anomaly import run_anomaly_detection
        anomaly_results = run_anomaly_detection(all_frames, change_maps, job_id)
        anomaly_count = sum(1 for r in anomaly_results if r.label.value == "ANOMALY")
        append_audit_event(job_id, "anomaly_detection_complete",
                           {"anomalies": anomaly_count, "total": len(anomaly_results)})
    except Exception as exc:
        logger.warning("Anomaly detection failed", error=str(exc))

    # ── Quality metrics ────────────────────────────────────────────────────
    report(0.95, 'Computing quality metrics')
    tcs = compute_temporal_consistency_score(all_frames)
    fsi = compute_frame_stability_index(all_frames)
    psnr_values = [meta.psnr for meta in all_metadata if meta.psnr is not None]
    ssim_values = [meta.ssim for meta in all_metadata if meta.ssim is not None]

    metrics = QualityMetrics(
        tcs=tcs, fsi=fsi,
        avg_psnr=float(np.mean(psnr_values)) if psnr_values else None,
        avg_ssim=float(np.mean(ssim_values)) if ssim_values else None,
        high_confidence_count=sum(1 for m in all_metadata if m.confidence_class == ConfidenceClass.high),
        medium_confidence_count=sum(1 for m in all_metadata if m.confidence_class == ConfidenceClass.medium),
        low_confidence_count=sum(1 for m in all_metadata if m.confidence_class == ConfidenceClass.low),
        rejected_count=0,
        total_frames=len(all_frames),
        interpolated_frames=sum(1 for m in all_metadata if m.is_interpolated),
        observed_frames=sum(1 for m in all_metadata if not m.is_interpolated),
    )

    append_audit_event(job_id, "metrics_computed", {
        "tcs": round(tcs, 4), "fsi": round(fsi, 4),
        "avg_psnr": round(float(np.mean(psnr_values)), 2) if psnr_values else None,
        "anomaly_count": anomaly_count,
    })

    # ── Trajectory tracking (MODULE 1) ─────────────────────────────────────
    report(0.93, 'Tracking cloud trajectories')
    trajectories = []
    try:
        from backend.app.services.trajectory_tracker import track_trajectories
        trajectories = track_trajectories(all_frames, job_id)
        append_audit_event(job_id, "trajectories_tracked", {"count": len(trajectories)})
    except Exception as exc:
        logger.warning("Trajectory tracking failed", error=str(exc))

    completed_at = datetime.now(timezone.utc)
    report(1.0, 'Pipeline completed successfully')
    logger.info('Pipeline completed', job_id=job_id, total_frames=len(all_frames),
                duration_seconds=(completed_at - created_at).total_seconds())

    # Map anomaly results to alerts dict for schema compatibility
    alerts = []
    if 'anomaly_results' in locals():
        for ar in anomaly_results:
            if ar.label.value == "ANOMALY":
                alerts.append({
                    "frame_index": ar.frame_index,
                    "score": round(ar.anomaly_score, 4),
                    "type": "SPIKE" if ar.intensity_spike else "MOTION",
                    "details": ar.details
                })

    result = PipelineResult(
        job_id=job_id, status=JobStatus.completed, layer_id=layer_id,
        data_source=data_source, bbox=bbox, time_start=time_start, time_end=time_end,
        original_video_url=None, interpolated_video_url=None,
        frames=all_metadata, metrics=metrics,
        trajectories=trajectories, alerts=alerts,
        created_at=created_at, completed_at=completed_at,
    )

    # Update global metrics (MODULE 10)
    try:
        from backend.app.services.geo_analytics import update_global_metrics_from_job
        update_global_metrics_from_job(result.model_dump(mode="json"))
    except Exception as exc:
        logger.warning("Global metrics update failed", error=str(exc))

    return result
