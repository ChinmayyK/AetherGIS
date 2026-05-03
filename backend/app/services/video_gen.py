"""AetherGIS — FFmpeg-based video generation module.

Implements:
  - Dual MP4 output (original-only + interpolated)
  - AI watermark burn-in (Rule OI-02)
  - Frame metadata overlay (timestamp, confidence class, model)
  - Frame-level JSON sidecar generation
  - H.264 encoding at configurable fps
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from backend.app.config import get_settings
from backend.app.models.schemas import ConfidenceClass, FrameMetadata
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Confidence badge colors (BGR for OpenCV, RGB for PIL)
CONFIDENCE_COLORS_BGR = {
    ConfidenceClass.high:     (0, 200, 50),    # Green
    ConfidenceClass.medium:   (0, 165, 255),   # Amber
    ConfidenceClass.low:      (0, 0, 220),     # Red
    ConfidenceClass.rejected: (128, 0, 128),   # Purple
}
CONFIDENCE_COLORS_RGB = {
    ConfidenceClass.high:     (50, 200, 0),
    ConfidenceClass.medium:   (255, 165, 0),
    ConfidenceClass.low:      (220, 0, 0),
    ConfidenceClass.rejected: (128, 0, 128),
}


def _burn_overlay(
    frame_bgr: np.ndarray,
    metadata: FrameMetadata,
    show_overlay: bool = True,
) -> np.ndarray:
    """
    Burn text overlay and watermark onto a frame in-place.
    Rule OI-02: AI-generated frames must carry a visible watermark.
    """
    h, w = frame_bgr.shape[:2]

    if not show_overlay and not metadata.is_interpolated:
        return frame_bgr

    # Semi-transparent top bar
    overlay = frame_bgr.copy()
    cv2.rectangle(overlay, (0, 0), (w, 36), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, frame_bgr, 0.45, 0, frame_bgr)

    # Timestamp
    ts_str = metadata.timestamp.strftime("%Y-%m-%d %H:%M UTC")
    cv2.putText(
        frame_bgr, ts_str,
        (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1, cv2.LINE_AA,
    )

    if metadata.is_interpolated:
        # Red AI watermark — Rule OI-02
        ai_label = "AI-GENERATED"
        if metadata.confidence_class == ConfidenceClass.low:
            ai_label = "AI — LOW CONFIDENCE"

        # Bottom-center watermark
        text_size = cv2.getTextSize(ai_label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0]
        tx = (w - text_size[0]) // 2
        ty = h - 14

        cv2.putText(
            frame_bgr, ai_label,
            (tx + 1, ty + 1), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2, cv2.LINE_AA,
        )
        cv2.putText(
            frame_bgr, ai_label,
            (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 240), 2, cv2.LINE_AA,
        )

        if show_overlay and metadata.confidence_class is not None:
            # Confidence badge (top-right)
            badge_color = CONFIDENCE_COLORS_BGR.get(metadata.confidence_class, (128, 128, 128))
            badge_label = f"{metadata.confidence_class.value}"
            cs_str = f"CS:{metadata.confidence_score:.2f}" if metadata.confidence_score else ""
            badge_text = f"  {badge_label}  {cs_str}"
            bw = cv2.getTextSize(badge_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)[0][0] + 8
            cv2.rectangle(frame_bgr, (w - bw - 4, 4), (w - 4, 30), badge_color, -1)
            cv2.putText(
                frame_bgr, badge_text,
                (w - bw, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA,
            )

            # Model tag
            if metadata.model_used:
                model_text = f"Model: {metadata.model_used}"
                cv2.putText(
                    frame_bgr, model_text,
                    (w - 160, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180),
                    1, cv2.LINE_AA,
                )

    return frame_bgr


def frames_to_video(
    frames: list[np.ndarray],           # float32 RGB [H, W, 3]
    metadata_list: list[FrameMetadata],
    output_path: Path,
    fps: int = 10,
    show_overlay: bool = True,
) -> Path:
    """
    Write frames to an MP4 video using OpenCV.
    Falls back gracefully if FFmpeg is not available.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not frames:
        raise ValueError("Cannot generate video from empty frame list")

    h, w = frames[0].shape[:2]

    # Try FFmpeg first for best quality
    try:
        _frames_to_video_ffmpeg(frames, metadata_list, output_path, fps, show_overlay, h, w)
    except Exception as e:
        logger.warning("FFmpeg failed, falling back to OpenCV writer", error=str(e))
        _frames_to_video_opencv(frames, metadata_list, output_path, fps, show_overlay, h, w)

    logger.info("Video generated", path=str(output_path), frames=len(frames), fps=fps)
    return output_path


def _frames_to_video_ffmpeg(
    frames: list[np.ndarray],
    metadata_list: list[FrameMetadata],
    output_path: Path,
    fps: int,
    show_overlay: bool,
    h: int,
    w: int,
) -> None:
    """Write video via FFmpeg pipe for H.264 encoding."""
    import imageio_ffmpeg
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    cmd = [
        ffmpeg_exe, "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{w}x{h}",
        "-pix_fmt", "bgr24",
        "-r", str(fps),
        "-i", "pipe:0",
        "-vcodec", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "fast",
        "-crf", "22",
        str(output_path),
    ]


    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    stdin = process.stdin
    if stdin is not None:
        for frame_f32, meta in zip(frames, metadata_list):
            frame_bgr = cv2.cvtColor(
                (frame_f32 * 255).clip(0, 255).astype(np.uint8),
                cv2.COLOR_RGB2BGR,
            )
            frame_bgr = _burn_overlay(frame_bgr, meta, show_overlay)
            stdin.write(frame_bgr.tobytes())
        stdin.close()
    _, stderr_raw = process.communicate()
    if process.returncode != 0:
        stderr_text = stderr_raw.decode("utf-8", errors="replace") if isinstance(stderr_raw, bytes) else str(stderr_raw)
        err_excerpt = stderr_text[:500]
        raise RuntimeError(f"FFmpeg error: {err_excerpt}")


def _frames_to_video_opencv(
    frames: list[np.ndarray],
    metadata_list: list[FrameMetadata],
    output_path: Path,
    fps: int,
    show_overlay: bool,
    h: int,
    w: int,
) -> None:
    """Write video via OpenCV VideoWriter (fallback)."""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_path), fourcc, fps, (w, h))

    for frame_f32, meta in zip(frames, metadata_list):
        frame_bgr = cv2.cvtColor(
            (frame_f32 * 255).clip(0, 255).astype(np.uint8),
            cv2.COLOR_RGB2BGR,
        )
        frame_bgr = _burn_overlay(frame_bgr, meta, show_overlay)
        writer.write(frame_bgr)

    writer.release()


def write_metadata_sidecar(
    metadata_list: list[FrameMetadata],
    output_path: Path,
) -> Path:
    """Write frame-level JSON metadata sidecar file (PRD §6.5)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "frame_count": len(metadata_list),
        "interpolated_count": sum(1 for m in metadata_list if m.is_interpolated),
        "observed_count": sum(1 for m in metadata_list if not m.is_interpolated),
        "frames": [m.model_dump(mode="json") for m in metadata_list],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info("Metadata sidecar written", path=str(output_path))
    return output_path


def save_frame_optimized(
    frame: np.ndarray,
    path: Path,
    metadata: Optional[FrameMetadata] = None,
    burn_overlay: bool = False,
    quality: int = 85,
) -> Path:
    """
    Save an individual frame using WebP for production-grade compression.
    Falls back to PNG if WebP fails or is not desired.

    Args:
        frame:        float32 RGB [H, W, 3] array in [0, 1] range.
        path:         Destination file path. If it ends in .png it will save as PNG,
                      otherwise defaults to .webp for best compression.
        metadata:     Optional frame metadata for overlay.
        burn_overlay: If True, burn the timestamp/AI watermark.
        quality:      WebP quality (0-100). Default 85 is great for satellite data.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Ensure path is Path object
    path = Path(path)
    
    frame_bgr = cv2.cvtColor(
        (frame * 255).clip(0, 255).astype(np.uint8),
        cv2.COLOR_RGB2BGR,
    )
    
    if burn_overlay and metadata:
        frame_bgr = _burn_overlay(frame_bgr, metadata, show_overlay=True)

    if path.suffix.lower() == '.webp':
        # WebP optimization: reduces size by ~50-70% compared to PNG
        encode_param = [int(cv2.IMWRITE_WEBP_QUALITY), quality]
        success = cv2.imwrite(str(path), frame_bgr, encode_param)
        if not success:
            logger.warning("WebP save failed, falling back to PNG", path=str(path))
            path = path.with_suffix('.png')
            cv2.imwrite(str(path), frame_bgr)
    else:
        cv2.imwrite(str(path), frame_bgr)
        
    return path
