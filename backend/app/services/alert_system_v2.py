"""AetherGIS — Region Alert System (MODULE 5).

Detects critical changes in geo-regions:
  • rapid_cloud_formation  — sudden brightness increase
  • strong_motion          — large displacement between frames
  • high_uncertainty       — AI confidence below threshold
  • temporal_anomaly       — sudden intensity jump (MAD spike)
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import List, Optional
import numpy as np


def _to_gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 3:
        return (0.299 * frame[:, :, 0] + 0.587 * frame[:, :, 1] + 0.114 * frame[:, :, 2])
    return frame.astype(np.float32)


def _brightness_change(a: np.ndarray, b: np.ndarray) -> float:
    return float(abs(_to_gray(b).mean() - _to_gray(a).mean()))


def _motion_magnitude(a: np.ndarray, b: np.ndarray) -> float:
    ga, gb = _to_gray(a), _to_gray(b)
    fa = np.fft.fft2(ga - ga.mean())
    fb = np.fft.fft2(gb - gb.mean())
    denom = np.abs(fa * np.conj(fb)) + 1e-9
    cross = (fa * np.conj(fb)) / denom
    corr = np.real(np.fft.ifft2(cross))
    h, w = corr.shape
    peak = np.unravel_index(np.argmax(corr), corr.shape)
    dy = peak[0] if peak[0] < h // 2 else peak[0] - h
    dx = peak[1] if peak[1] < w // 2 else peak[1] - w
    return math.sqrt((dx / w) ** 2 + (dy / h) ** 2)


def _mad(a: np.ndarray, b: np.ndarray) -> float:
    ga, gb = _to_gray(a), _to_gray(b)
    return float(np.mean(np.abs(ga.astype(np.float32) - gb.astype(np.float32))))


def _severity(value: float, medium: float, high: float) -> str:
    if value >= high:
        return "high"
    if value >= medium:
        return "medium"
    return "low"


def detect_alerts(
    frames: List[np.ndarray],
    frame_metadata: Optional[List[dict]] = None,
    job_id: str = "",
) -> List[dict]:
    """
    Scan a sequence of frames and emit alert objects for significant events.

    Each alert:
        id, frame_index, region, type, severity, description, timestamp
    """
    if len(frames) < 2:
        return []

    alerts: List[dict] = []
    alert_id = 0
    now = datetime.utcnow().isoformat() + "Z"

    BRIGHT_MEDIUM, BRIGHT_HIGH = 0.04, 0.10
    MOTION_MEDIUM, MOTION_HIGH = 0.02, 0.06
    MAD_MEDIUM, MAD_HIGH = 0.12, 0.25

    for i in range(1, len(frames)):
        a, b = frames[i - 1], frames[i]
        ts = (frame_metadata[i]["timestamp"] if frame_metadata and i < len(frame_metadata)
              else now)

        # Determine approximate geo region from frame index
        region_label = f"Region around frame {i}"

        # 1. Rapid cloud formation
        bc = _brightness_change(a, b)
        if bc >= BRIGHT_MEDIUM:
            alert_id += 1
            alerts.append({
                "id": f"{job_id}-alert-{alert_id}",
                "frame_index": i,
                "region": region_label,
                "type": "rapid_change",
                "severity": _severity(bc, BRIGHT_MEDIUM, BRIGHT_HIGH),
                "description": f"Brightness change {bc:.3f} — possible rapid cloud formation or dissipation.",
                "timestamp": ts,
                "value": round(bc, 5),
            })

        # 2. Strong motion
        mm = _motion_magnitude(a, b)
        if mm >= MOTION_MEDIUM:
            alert_id += 1
            alerts.append({
                "id": f"{job_id}-alert-{alert_id}",
                "frame_index": i,
                "region": region_label,
                "type": "strong_motion",
                "severity": _severity(mm, MOTION_MEDIUM, MOTION_HIGH),
                "description": f"Motion magnitude {mm:.4f} — strong translational displacement detected.",
                "timestamp": ts,
                "value": round(mm, 6),
            })

        # 3. High uncertainty / MAD
        mad = _mad(a, b)
        if mad >= MAD_MEDIUM:
            alert_id += 1
            alerts.append({
                "id": f"{job_id}-alert-{alert_id}",
                "frame_index": i,
                "region": region_label,
                "type": "high_uncertainty",
                "severity": _severity(mad, MAD_MEDIUM, MAD_HIGH),
                "description": f"MAD {mad:.3f} — high inter-frame difference, AI confidence degraded.",
                "timestamp": ts,
                "value": round(mad, 5),
            })

    # De-duplicate: keep only highest severity per frame per type
    seen: dict = {}
    deduped: List[dict] = []
    severity_rank = {"high": 3, "medium": 2, "low": 1}
    for alert in alerts:
        key = (alert["frame_index"], alert["type"])
        if key not in seen or severity_rank[alert["severity"]] > severity_rank[seen[key]["severity"]]:
            seen[key] = alert
    deduped = list(seen.values())
    deduped.sort(key=lambda a: (a["frame_index"], a["type"]))
    return deduped
