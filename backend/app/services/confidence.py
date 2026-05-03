"""AetherGIS — Optical Flow Validation & Confidence Scoring.

Implements PRD §6.4:
  - Forward-backward Farneback optical flow consistency
  - Mean Absolute Difference (MAD) computation
  - Confidence Score (CS) algorithm
  - Confidence classification (High / Medium / Low / Rejected)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from backend.app.config import get_settings
from backend.app.models.schemas import ConfidenceClass
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


@dataclass
class ConfidenceResult:
    """Per-frame confidence evaluation result."""
    confidence_score: float          # CS in [0.0, 1.0]
    confidence_class: ConfidenceClass
    flow_consistency: float          # normalized forward-backward consistency
    mad_score: float                 # normalized mean absolute difference
    gap_factor: float                # temporal gap factor (0–1)
    gap_minutes: float
    is_rejected: bool = False        # True → no frame generated
    reject_reason: str = ""
    extra_flags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.extra_flags is None:
            self.extra_flags = []


def _to_gray_uint8(frame: np.ndarray) -> np.ndarray:
    """Convert float32 RGB [H,W,3] → uint8 grayscale."""
    rgb8 = (frame * 255).clip(0, 255).astype(np.uint8)
    return cv2.cvtColor(rgb8, cv2.COLOR_RGB2GRAY)


def compute_optical_flow(
    frame_a: np.ndarray, frame_b: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute dense Farneback optical flow:
    Returns (flow_fwd: [H,W,2], flow_bwd: [H,W,2])
    """
    gray_a = _to_gray_uint8(frame_a)
    gray_b = _to_gray_uint8(frame_b)

    farneback_params = dict(
        pyr_scale=0.5, levels=3, winsize=15,
        iterations=3, poly_n=5, poly_sigma=1.2,
        flags=0,
    )
    flow_fwd = cv2.calcOpticalFlowFarneback(gray_a, gray_b, None, **farneback_params)
    flow_bwd = cv2.calcOpticalFlowFarneback(gray_b, gray_a, None, **farneback_params)
    return flow_fwd, flow_bwd


def compute_flow_consistency_score(
    flow_fwd: np.ndarray, flow_bwd: np.ndarray
) -> float:
    """
    Forward-backward flow consistency (PRD §6.4.1).
    Warp flow_bwd back using flow_fwd; compute per-pixel Euclidean norm.
    Returns normalized mean consistency error in [0, 1].
    """
    h, w = flow_fwd.shape[:2]

    # Build identity grid
    grid_x, grid_y = np.meshgrid(np.arange(w, dtype=np.float32),
                                  np.arange(h, dtype=np.float32))

    # Warp coordinates using forward flow
    map_x = grid_x + flow_fwd[..., 0]
    map_y = grid_y + flow_fwd[..., 1]

    # Remap backward flow to forward-flow coordinates
    map_x_clamped = np.clip(map_x, 0, w - 1).astype(np.float32)
    map_y_clamped = np.clip(map_y, 0, h - 1).astype(np.float32)
    bwd_warped_x = cv2.remap(flow_bwd[..., 0], map_x_clamped, map_y_clamped, cv2.INTER_LINEAR)
    bwd_warped_y = cv2.remap(flow_bwd[..., 1], map_x_clamped, map_y_clamped, cv2.INTER_LINEAR)

    # Consistency error = fwd + warped_bwd (should be ~0 for consistent flow)
    err_x = flow_fwd[..., 0] + bwd_warped_x
    err_y = flow_fwd[..., 1] + bwd_warped_y
    consistency_err = np.sqrt(err_x ** 2 + err_y ** 2)

    # Normalize by max possible flow magnitude (image diagonal)
    max_flow = np.sqrt(h ** 2 + w ** 2)
    normalized = float(np.clip(consistency_err.mean() / max_flow, 0, 1))
    return normalized


def compute_mad(frame_a: np.ndarray, frame_b: np.ndarray) -> float:
    """Mean Absolute pixel Difference (normalized, PRD §6.4.2)."""
    return float(np.abs(frame_b - frame_a).mean())


def score_generated_frame(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    gap_minutes: float,
    extra_flags: list[str] | None = None,
) -> ConfidenceResult:
    """
    Core confidence scoring algorithm (PRD §6.4.3).

    CS = w1*(1-flow_consistency) + w2*(1-MAD_norm) + w3*(1-gap_factor)
    where w1=0.40, w2=0.35, w3=0.25
    """
    extra_flags = extra_flags or []

    # Compute optical flow
    flow_fwd, flow_bwd = compute_optical_flow(frame_a, frame_b)
    flow_consistency = compute_flow_consistency_score(flow_fwd, flow_bwd)

    # Check for outright rejection (PRD §6.4.1)
    if flow_consistency > settings.flow_rejection_threshold:
        logger.warning(
            "Frame pair rejected — flow consistency exceeds threshold",
            flow_consistency=round(float(flow_consistency), 4),
            threshold=settings.flow_rejection_threshold,
        )
        return ConfidenceResult(
            confidence_score=0.0,
            confidence_class=ConfidenceClass.rejected,
            flow_consistency=flow_consistency,
            mad_score=0.0,
            gap_factor=min(gap_minutes / 30.0, 1.0),
            gap_minutes=gap_minutes,
            is_rejected=True,
            reject_reason="FLOW_CONSISTENCY_TOO_HIGH",
            extra_flags=extra_flags,
        )

    # Compute MAD
    mad = compute_mad(frame_a, frame_b)

    # Large pixel change → flag
    if mad > settings.large_diff_threshold:
        extra_flags.append(f"LARGE_CHANGE:{mad:.3f}")

    # Gap factor (PRD §6.4.3)
    gap_factor = min(gap_minutes / 30.0, 1.0)

    # Conservative Merge Rule (Rule OI-01): use minimum of sub-scores
    sub_flow = 1.0 - flow_consistency
    sub_mad = 1.0 - min(mad / 1.0, 1.0)
    sub_gap = 1.0 - gap_factor

    # Weighted sum
    w1, w2, w3 = settings.cs_weight_flow, settings.cs_weight_mad, settings.cs_weight_gap
    cs_weighted = w1 * sub_flow + w2 * sub_mad + w3 * sub_gap

    # Conservative: take min of weighted and minimum sub-score (Rule OI-01)
    cs = min(cs_weighted, min(sub_flow, sub_mad, sub_gap))

    cs = float(np.clip(cs, 0.0, 1.0))

    # Rule MG-03: Apply confidence cap based on gap category
    from backend.app.services.preprocessing import classify_gap
    _, _, _, confidence_floor = classify_gap(gap_minutes)
    cs = min(cs, confidence_floor)

    # Classify
    cls = classify_confidence(cs)

    logger.debug(
        "Frame pair scored",
        cs=round(float(cs), 4),
        cls=cls,
        flow=round(float(flow_consistency), 4),
        mad=round(float(mad), 4),
        gap_minutes=gap_minutes,
    )
    return ConfidenceResult(
        confidence_score=cs,
        confidence_class=cls,
        flow_consistency=flow_consistency,
        mad_score=mad,
        gap_factor=gap_factor,
        gap_minutes=gap_minutes,
        extra_flags=extra_flags,
    )


def classify_confidence(cs: float) -> ConfidenceClass:
    """PRD §6.4.4 — Confidence classification."""
    if cs >= settings.high_confidence_threshold:
        return ConfidenceClass.high
    elif cs >= settings.medium_confidence_threshold:
        return ConfidenceClass.medium
    else:
        return ConfidenceClass.low


def compute_temporal_consistency_score(frames: list[np.ndarray]) -> float:
    """
    Temporal Consistency Score (TCS): measures structural smoothness
    of frame-to-frame transitions across the sequence.
    Uses SSIM-based variance to detect structural flickering.
    """
    if len(frames) < 3:
        return 1.0

    # Compute SSIM between consecutive frames
    ssims = []
    from skimage.metrics import structural_similarity as ssim
    for i in range(len(frames) - 1):
        s = ssim(frames[i], frames[i + 1], data_range=1.0, channel_axis=-1)
        ssims.append(s)
    
    # High variance in SSIM = structural instability/flicker
    variance = float(np.var(ssims))
    tcs = 1.0 / (1.0 + variance * 50.0)
    return float(np.clip(tcs, 0.0, 1.0))


def compute_frame_stability_index(frames: list[np.ndarray]) -> float:
    """
    Frame Stability Index (FSI): measures global intensity stability.
    Detects sudden jumps in brightness or color shift.
    """
    if len(frames) < 2:
        return 1.0
        
    # Standard deviation of mean intensities across channels
    means = [np.mean(f, axis=(0, 1)) for f in frames] # List of [R, G, B] means
    means_arr = np.array(means)
    
    # Compute stability per channel then average
    ch_vars = np.var(means_arr, axis=0) # [Var_R, Var_G, Var_B]
    avg_var = float(ch_vars.mean())
    
    fsi = 1.0 / (1.0 + avg_var * 250.0)
    return float(np.clip(fsi, 0.0, 1.0))
