"""AetherGIS — Temporal Consistency Checker (MODULE 11)."""
from __future__ import annotations

from typing import List
import numpy as np


def _to_gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 3:
        return (0.299 * frame[:, :, 0] + 0.587 * frame[:, :, 1] + 0.114 * frame[:, :, 2]).astype(np.float32)
    return frame.astype(np.float32)


def check_temporal_consistency(
    frames: List[np.ndarray],
    mad_threshold: float = 0.25,
    jump_threshold: float = 0.35,
) -> List[dict]:
    """
    Detect unrealistic transitions between frames.

    Returns list of issue dicts:
        frame, issue, mad_score, severity
    """
    if len(frames) < 3:
        return []

    grays = [_to_gray(f) for f in frames]
    issues: List[dict] = []

    # Compute per-frame MAD with previous frame
    mads = [0.0]  # frame 0 has no predecessor
    for i in range(1, len(grays)):
        mad = float(np.mean(np.abs(grays[i] - grays[i - 1])))
        mads.append(mad)

    mean_mad = float(np.mean(mads[1:]))
    std_mad = float(np.std(mads[1:]))

    for i in range(1, len(frames) - 1):
        mad_i = mads[i]
        z_score = (mad_i - mean_mad) / (std_mad + 1e-9)

        if mad_i >= jump_threshold or z_score > 3.0:
            issues.append({
                "frame": i,
                "issue": "sudden_jump",
                "mad_score": round(mad_i, 5),
                "z_score": round(z_score, 3),
                "severity": "high" if mad_i >= jump_threshold else "medium",
            })
        elif mad_i >= mad_threshold or z_score > 2.0:
            issues.append({
                "frame": i,
                "issue": "temporal_inconsistency",
                "mad_score": round(mad_i, 5),
                "z_score": round(z_score, 3),
                "severity": "medium" if z_score > 2.5 else "low",
            })

    return issues
