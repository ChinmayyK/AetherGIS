"""AetherGIS — Uncertainty Map Generation (MODULE 6).

Generates pixel-wise confidence/uncertainty maps for each interpolated frame.

Sources of uncertainty:
  1. Flow inconsistency  — forward/backward optical flow divergence
  2. Intensity error     — absolute difference between frame and references
  3. Interpolation variance — variance across n_intermediate positions

Output: confidence_map.png — single-channel heatmap (0=low, 1=high confidence)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from backend.app.config import get_settings
from backend.app.utils.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)


def _optical_flow_consistency(frame_a: np.ndarray, frame_b: np.ndarray) -> np.ndarray:
    """Compute pixel-wise flow consistency map using OpenCV if available."""
    try:
        import cv2
        a_gray = cv2.cvtColor((frame_a * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
        b_gray = cv2.cvtColor((frame_b * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
        fwd = cv2.calcOpticalFlowFarneback(a_gray, b_gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        bwd = cv2.calcOpticalFlowFarneback(b_gray, a_gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        # Warp backward flow to forward space and compare
        h, w = fwd.shape[:2]
        grid_x, grid_y = np.meshgrid(np.arange(w), np.arange(h))
        map_x = (grid_x + fwd[..., 0]).astype(np.float32)
        map_y = (grid_y + fwd[..., 1]).astype(np.float32)
        map_x_clipped = np.clip(map_x, 0, w - 1).astype(np.float32)
        map_y_clipped = np.clip(map_y, 0, h - 1).astype(np.float32)
        warped_bwd_x = cv2.remap(bwd[..., 0], map_x_clipped, map_y_clipped, cv2.INTER_LINEAR)
        warped_bwd_y = cv2.remap(bwd[..., 1], map_x_clipped, map_y_clipped, cv2.INTER_LINEAR)
        # Consistency: magnitude of (fwd + warped_bwd)
        inconsistency = np.sqrt(
            (fwd[..., 0] + warped_bwd_x) ** 2 + (fwd[..., 1] + warped_bwd_y) ** 2
        )
        # Normalise to [0,1] — higher = more inconsistent
        max_val = inconsistency.max()
        if max_val > 0:
            inconsistency = inconsistency / max_val
        return inconsistency.astype(np.float32)
    except Exception:
        # Fallback: simple gradient magnitude
        a_gray = frame_a.mean(axis=-1)
        b_gray = frame_b.mean(axis=-1)
        return np.abs(a_gray - b_gray).astype(np.float32)


def _intensity_error_map(
    interpolated: np.ndarray,
    ref_a: np.ndarray,
    ref_b: np.ndarray,
    t_pos: float,
) -> np.ndarray:
    """Expected intensity = linear blend; deviation from it indicates error."""
    expected = ref_a * (1 - t_pos) + ref_b * t_pos
    error = np.abs(interpolated - expected).mean(axis=-1)
    return error.astype(np.float32)


def _variance_map(frames: list[np.ndarray]) -> np.ndarray:
    """Pixel-wise variance across multiple intermediate frames."""
    if len(frames) < 2:
        return np.zeros(frames[0].shape[:2], dtype=np.float32)
    stack = np.stack([f.mean(axis=-1) for f in frames], axis=0)
    return np.var(stack, axis=0).astype(np.float32)


def generate_uncertainty_map(
    interpolated_frame: np.ndarray,
    ref_a: np.ndarray,
    ref_b: np.ndarray,
    t_pos: float,
    neighboring_frames: Optional[list[np.ndarray]] = None,
    w_flow: float = 0.40,
    w_intensity: float = 0.35,
    w_variance: float = 0.25,
) -> np.ndarray:
    """Generate a pixel-wise confidence map in [0, 1].

    1.0 = high confidence (low uncertainty)
    0.0 = low confidence (high uncertainty)
    """
    h, w = interpolated_frame.shape[:2]

    # 1. Flow inconsistency
    flow_inconsistency = _optical_flow_consistency(ref_a, ref_b)
    flow_inconsistency = _resize_map(flow_inconsistency, h, w)

    # 2. Intensity error
    intensity_err = _intensity_error_map(interpolated_frame, ref_a, ref_b, t_pos)
    intensity_err = _resize_map(intensity_err, h, w)

    # 3. Interpolation variance
    if neighboring_frames and len(neighboring_frames) >= 2:
        var_map = _variance_map(neighboring_frames)
    else:
        var_map = np.zeros((h, w), dtype=np.float32)

    # Normalise each component to [0, 1]
    def _norm(x: np.ndarray) -> np.ndarray:
        mx = x.max()
        return x / mx if mx > 0 else x

    flow_n = _norm(flow_inconsistency)
    intensity_n = _norm(intensity_err)
    var_n = _norm(var_map)

    # Weighted uncertainty (0=good, 1=bad)
    uncertainty = w_flow * flow_n + w_intensity * intensity_n + w_variance * var_n

    # Convert to confidence (invert)
    confidence = 1.0 - np.clip(uncertainty, 0, 1)
    return confidence.astype(np.float32)


def _resize_map(arr: np.ndarray, h: int, w: int) -> np.ndarray:
    if arr.shape == (h, w):
        return arr
    try:
        from PIL import Image
        img = Image.fromarray((arr * 255).astype(np.uint8))
        img = img.resize((w, h), Image.LANCZOS)
        return np.array(img, dtype=np.float32) / 255.0
    except Exception:
        return arr[:h, :w] if arr.shape[0] >= h and arr.shape[1] >= w else arr


def _confidence_to_heatmap(confidence: np.ndarray) -> np.ndarray:
    """Convert single-channel confidence [0,1] to RGB heatmap."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.cm as cm
        cmap = cm.get_cmap("RdYlGn")
        rgba = cmap(confidence)
        return (rgba[:, :, :3] * 255).astype(np.uint8)
    except ImportError:
        # Fallback: green = high confidence, red = low
        h, w = confidence.shape
        rgb = np.zeros((h, w, 3), dtype=np.uint8)
        rgb[:, :, 0] = ((1 - confidence) * 255).astype(np.uint8)
        rgb[:, :, 1] = (confidence * 255).astype(np.uint8)
        return rgb


def save_confidence_map(
    confidence: np.ndarray,
    output_path: Path,
    frame_idx: int,
) -> Path:
    """Save confidence map as PNG heatmap."""
    from PIL import Image

    output_path.mkdir(parents=True, exist_ok=True)
    heatmap = _confidence_to_heatmap(confidence)
    img = Image.fromarray(heatmap)
    path = output_path / f"confidence_map_{frame_idx:04d}.png"
    img.save(str(path))
    return path


def generate_and_save_confidence_maps(
    all_frames: list[np.ndarray],
    all_t_positions: list[Optional[float]],
    is_interpolated: list[bool],
    ref_pairs: list[Optional[tuple[int, int]]],
    job_id: str,
) -> dict[int, Path]:
    """Batch generate confidence maps for all interpolated frames in a job."""
    export_dir = settings.exports_dir / job_id / "confidence_maps"
    export_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[int, Path] = {}

    for i, (frame, t_pos, is_interp) in enumerate(zip(all_frames, all_t_positions, is_interpolated)):
        if not is_interp or ref_pairs[i] is None:
            continue

        ref_a_idx, ref_b_idx = ref_pairs[i]
        ref_a = all_frames[ref_a_idx]
        ref_b = all_frames[ref_b_idx]

        # Neighboring frames for variance estimation
        neighbors = []
        for j in range(max(0, i - 2), min(len(all_frames), i + 3)):
            if is_interpolated[j] and j != i:
                neighbors.append(all_frames[j])

        try:
            conf_map = generate_uncertainty_map(
                frame, ref_a, ref_b,
                t_pos=t_pos if t_pos is not None else 0.5,
                neighboring_frames=neighbors,
            )
            path = save_confidence_map(conf_map, export_dir, i)
            paths[i] = path
        except Exception as exc:
            logger.warning("Confidence map generation failed", frame_idx=i, error=str(exc))

    logger.info("Confidence maps generated", job_id=job_id, count=len(paths))
    return paths
