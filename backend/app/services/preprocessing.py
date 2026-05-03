"""AetherGIS — Preprocessing pipeline.

Implements:
  - DQ-01: Frame content validation (moved here from wms_client for centralization)
  - DQ-02: Sequence segmentation at gap boundaries
  - DQ-03: Calibration shift detection
  - FR-PP-05: Limb masking (geometric heuristic)
  - FR-PP-06: Terminator crossing detection (heuristic)
  - Image alignment (phase correlation)
  - Normalization and gap analysis
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import cv2
import numpy as np

from backend.app.config import get_settings
from backend.app.models.schemas import GapCategory
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


@dataclass
class GapInfo:
    """Describes a temporal gap between two consecutive frames."""
    index: int              # index of the FIRST frame in the pair
    gap_minutes: float
    category: GapCategory
    sub_intervals: int      # how many sub-intervals to split into
    max_frames_per_interval: int
    confidence_floor: float  # maximum achievable CS for this gap


@dataclass
class PreprocessedSequence:
    """Output of the preprocessing pipeline."""
    frames: list[np.ndarray]                # float32 RGB [H, W, 3]
    timestamps: list[datetime]
    gaps: list[GapInfo]
    original_indices: list[int]             # mapping back to raw frame list
    flags: dict[int, list[str]] = field(default_factory=dict)  # per-frame flags


def classify_gap(gap_minutes: float) -> tuple[GapCategory, int, int, float]:
    """
    Classify a temporal gap, return:
    (category, sub_intervals, max_frames_per_interval, confidence_floor)
    PRD §11.1 + §11.2
    """
    if gap_minutes < settings.gap_short_max:
        return GapCategory.short, 1, 8, 1.0
    elif gap_minutes < settings.gap_medium_max:
        return GapCategory.medium, 2, 4, 1.0
    elif gap_minutes < settings.gap_large_max:
        return GapCategory.large, 4, 2, 0.74       # max Medium confidence
    else:
        return GapCategory.very_large, 2, 1, 0.44  # max Low confidence


def compute_temporal_gaps(timestamps: list[datetime]) -> list[GapInfo]:
    """Compute gap info for each consecutive frame pair."""
    gaps = []
    for i in range(len(timestamps) - 1):
        delta = (timestamps[i + 1] - timestamps[i]).total_seconds() / 60.0
        cat, sub_int, max_fps, cf = classify_gap(delta)
        gaps.append(
            GapInfo(
                index=i,
                gap_minutes=delta,
                category=cat,
                sub_intervals=sub_int,
                max_frames_per_interval=max_fps,
                confidence_floor=cf,
            )
        )
    return gaps


def segment_observed_frames(
    frames: list[np.ndarray],
    timestamps: list[datetime],
    max_gap_minutes: float = 30.0,
) -> list[tuple[list[np.ndarray], list[datetime]]]:
    """
    Rule DQ-02: Split the sequence into sub-sequences wherever a gap
    exceeds max_gap_minutes.
    Returns a list of (frames, timestamps) segments.
    """
    if not frames:
        return []

    segments: list[tuple[list[np.ndarray], list[datetime]]] = []
    seg_frames: list[np.ndarray] = [frames[0]]
    seg_times: list[datetime] = [timestamps[0]]

    for i in range(1, len(frames)):
        gap = (timestamps[i] - timestamps[i - 1]).total_seconds() / 60.0
        if gap > max_gap_minutes:
            logger.info(
                "DQ-02: Segment split at gap", gap_minutes=gap, index=i
            )
            segments.append((seg_frames, seg_times))
            seg_frames = [frames[i]]
            seg_times = [timestamps[i]]
        else:
            seg_frames.append(frames[i])
            seg_times.append(timestamps[i])

    segments.append((seg_frames, seg_times))
    return segments


def align_frames(
    frames: list[np.ndarray],
) -> list[np.ndarray]:
    """
    Apply phase-correlation sub-pixel alignment to keep all frames
    registered to the first frame in the sequence.
    """
    if len(frames) <= 1:
        return frames

    ref = cv2.cvtColor((frames[0] * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    aligned = [frames[0]]

    for i, frame in enumerate(frames[1:], start=1):
        gray = cv2.cvtColor((frame * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
        (shift, _) = cv2.phaseCorrelate(ref, gray)
        dx, dy = shift
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        h, w = frame.shape[:2]
        corrected = cv2.warpAffine(frame, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        aligned.append(corrected)

    return aligned


def detect_calibration_shift(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    threshold: float = 0.15,
) -> bool:
    """
    Rule DQ-03: Detect abrupt radiometric calibration shift between frames.
    A calibration event causes a sudden uniform brightness offset.
    Returns True if a shift is detected.
    """
    mean_a = frame_a.mean(axis=(0, 1))
    mean_b = frame_b.mean(axis=(0, 1))
    delta = float(np.abs(mean_b - mean_a).max())
    if delta > threshold:
        logger.warning(
            "DQ-03: Calibration shift detected",
            delta=round(delta, 4),
            threshold=threshold,
        )
        return True
    return False


def detect_terminator_crossing(
    timestamp: datetime,
    bbox: list[float],
) -> bool:
    """
    FR-PP-06: Detect if the solar terminator (day/night boundary) crosses
    the bbox during this timestamp (geometric heuristic based on solar declination).
    Returns True if the terminator is likely crossing the region.
    """
    # Compute approximate solar hour angle at bbox center
    center_lon = (bbox[0] + bbox[2]) / 2.0
    center_lat = (bbox[1] + bbox[3]) / 2.0

    # UTC decimal hour
    utc_hour = timestamp.hour + timestamp.minute / 60.0
    # Solar noon at longitude
    solar_hour = utc_hour + center_lon / 15.0

    # Approximate solar elevation using simplified formula
    day_of_year = timestamp.timetuple().tm_yday
    declination = 23.45 * math.sin(math.radians((360 / 365) * (day_of_year - 81)))
    hour_angle = (solar_hour - 12) * 15  # degrees
    lat_rad = math.radians(center_lat)
    dec_rad = math.radians(declination)
    ha_rad = math.radians(hour_angle)

    sin_elevation = (
        math.sin(lat_rad) * math.sin(dec_rad)
        + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(ha_rad)
    )
    elevation_deg = math.degrees(math.asin(max(-1, min(1, sin_elevation))))

    # Terminator is crossing when elevation is within ±10 degrees of horizon
    is_crossing = abs(elevation_deg) < 10.0
    if is_crossing:
        logger.info(
            "FR-PP-06: Terminator crossing detected",
            solar_elevation=round(elevation_deg, 2),
            center_lat=center_lat,
            center_lon=center_lon,
        )
    return is_crossing


def get_limb_mask(
    frame_shape: tuple[int, int],
    bbox: list[float],
) -> Optional[np.ndarray]:
    """
    FR-PP-05: Compute a mask for the Earth limb zone (sensor edge artifact).
    For regional BBOX this is typically not needed; returns None for small BBOX.
    For global/near-global BBOX (width > 150 deg), returns an elliptical mask.
    """
    lon_span = bbox[2] - bbox[0]
    lat_span = bbox[3] - bbox[1]
    if lon_span < 150 or lat_span < 120:
        return None  # Not a full-disk view; limb not relevant

    h, w = frame_shape
    Y, X = np.ogrid[:h, :w]
    cx, cy = w / 2, h / 2
    # Elliptical mask (95% of frame is valid, edges are limb)
    mask = ((X - cx) / (cx * 0.95)) ** 2 + ((Y - cy) / (cy * 0.95)) ** 2 <= 1
    return mask.astype(np.float32)


def preprocess_sequence(
    frames_raw: list[np.ndarray],
    timestamps: list[datetime],
    bbox: list[float],
) -> PreprocessedSequence:
    """
    Main preprocessing pipeline:
    1. Remove duplicates (hash comparison)
    2. Spatial alignment (phase correlation)
    3. Gap analysis and classification
    4. Calibration shift detection between adjacent pairs
    """
    if not frames_raw:
        raise ValueError("Empty frame sequence passed to preprocessor")

    # Step 1: Remove duplicate frames
    seen_hashes: set[int] = set()
    deduped_frames: list[np.ndarray] = []
    deduped_times: list[datetime] = []
    original_indices: list[int] = []

    for i, (f, t) in enumerate(zip(frames_raw, timestamps)):
        h = hash(f.tobytes())
        if h in seen_hashes:
            logger.info("Removing duplicate frame", index=i)
            continue
        seen_hashes.add(h)
        deduped_frames.append(f)
        deduped_times.append(t)
        original_indices.append(i)

    # Step 2: Align frames
    aligned = align_frames(deduped_frames)

    # Step 3: Gap analysis
    gaps = compute_temporal_gaps(deduped_times)

    # Step 4: Per-pair calibration shift detection
    frame_flags: dict[int, list[str]] = {}
    for i in range(len(aligned) - 1):
        if detect_calibration_shift(aligned[i], aligned[i + 1]):
            frame_flags.setdefault(i, []).append("CALIBRATION_SHIFT")
            frame_flags.setdefault(i + 1, []).append("CALIBRATION_SHIFT")

    # Step 5: Terminator detection per frame
    for i, ts in enumerate(deduped_times):
        if detect_terminator_crossing(ts, bbox):
            frame_flags.setdefault(i, []).append("TERMINATOR_CROSSING")

    logger.info(
        "Preprocessing complete",
        input_frames=len(frames_raw),
        output_frames=len(aligned),
        gaps=len(gaps),
        flagged_frames=len(frame_flags),
    )

    return PreprocessedSequence(
        frames=aligned,
        timestamps=deduped_times,
        gaps=gaps,
        original_indices=original_indices,
        flags=frame_flags,
    )
