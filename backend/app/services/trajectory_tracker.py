"""AetherGIS — Trajectory Tracker (MODULE 1).

Detects key cloud-cluster regions and tracks their motion across frames
using phase-correlation for global motion and grid-based intensity maxima
for cluster identification.
"""
from __future__ import annotations

import math
from typing import List
import numpy as np

# ─── Types ────────────────────────────────────────────────────────────────────

def _to_gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 3:
        return (0.299 * frame[:, :, 0] + 0.587 * frame[:, :, 1] + 0.114 * frame[:, :, 2])
    return frame.astype(np.float32)


def detect_key_regions(frame: np.ndarray, n_regions: int = 6) -> List[dict]:
    """Find n brightest cluster centroids in a frame."""
    gray = _to_gray(frame)
    h, w = gray.shape
    grid = 5
    ch, cw = h // grid, w // grid
    regions: List[dict] = []

    for gy in range(grid):
        for gx in range(grid):
            y1, y2 = gy * ch, min((gy + 1) * ch, h)
            x1, x2 = gx * cw, min((gx + 1) * cw, w)
            cell = gray[y1:y2, x1:x2]
            intensity = float(np.mean(cell))
            peak_y, peak_x = np.unravel_index(np.argmax(cell), cell.shape)
            regions.append({
                "x": (x1 + peak_x) / w,
                "y": (y1 + peak_y) / h,
                "intensity": intensity,
                "bbox": [x1 / w, y1 / h, x2 / w, y2 / h],
            })

    regions.sort(key=lambda r: r["intensity"], reverse=True)
    return regions[:n_regions]


def _phase_motion(a: np.ndarray, b: np.ndarray):
    """Sub-pixel motion estimation via phase correlation."""
    fa = np.fft.fft2(a - a.mean())
    fb = np.fft.fft2(b - b.mean())
    denom = np.abs(fa * np.conj(fb)) + 1e-9
    cross = (fa * np.conj(fb)) / denom
    corr = np.real(np.fft.ifft2(cross))
    h, w = corr.shape
    peak = np.unravel_index(np.argmax(corr), corr.shape)
    dy = peak[0] if peak[0] < h // 2 else peak[0] - h
    dx = peak[1] if peak[1] < w // 2 else peak[1] - w
    return float(dx / w), float(dy / h)


def track_trajectories(frames: List[np.ndarray], job_id: str) -> List[dict]:
    """
    Track cloud cluster trajectories across a sequence of frames.
    Returns list of trajectory dicts suitable for JSON serialisation.
    """
    if len(frames) < 2:
        return []

    grays = [_to_gray(f) for f in frames]
    initial_regions = detect_key_regions(frames[0])
    trajectories: List[dict] = []

    for idx, region in enumerate(initial_regions):
        cx, cy = region["x"], region["y"]
        points = [{"x": cx, "y": cy, "frame_index": 0}]

        for i in range(1, len(grays)):
            dx, dy = _phase_motion(grays[i - 1], grays[i])
            cx = max(0.0, min(1.0, cx + dx))
            cy = max(0.0, min(1.0, cy + dy))
            points.append({"x": round(cx, 5), "y": round(cy, 5), "frame_index": i})

        n = len(points) - 1
        total_dx = points[-1]["x"] - points[0]["x"]
        total_dy = points[-1]["y"] - points[0]["y"]
        avg_dx = total_dx / n if n else 0.0
        avg_dy = total_dy / n if n else 0.0
        speed = math.sqrt(avg_dx ** 2 + avg_dy ** 2)

        trajectories.append({
            "id": f"{job_id}-t{idx}",
            "start_frame": 0,
            "end_frame": len(frames) - 1,
            "points": points,
            "motion_vector": {"dx": round(avg_dx, 6), "dy": round(avg_dy, 6)},
            "speed": round(speed, 6),
            "intensity": round(region["intensity"], 4),
            "direction_deg": round(math.degrees(math.atan2(avg_dy, avg_dx)), 1),
        })

    return trajectories
