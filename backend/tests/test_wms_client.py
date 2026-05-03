"""AetherGIS — Pytest tests: WMS client."""
from __future__ import annotations

import io
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from PIL import Image

from backend.app.services.wms_client import NASAGIBSClient


def make_png_bytes(w: int = 64, h: int = 64, fill: tuple = (100, 150, 200)) -> bytes:
    """Create a valid PNG image byte string."""
    img = Image.new("RGB", (w, h), color=fill)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestNASAGIBSClient:
    """Tests for the NASA GIBS WMS client."""

    def test_build_request_params(self):
        """Verify WMS request params are constructed correctly."""
        client = NASAGIBSClient()
        ts = datetime(2024, 1, 15, 6, 0, 0, tzinfo=timezone.utc)
        params = client._build_request_params(
            "GOES-East_ABI_Band2_Red_Visible_1km",
            [68.0, 8.0, 97.0, 37.0],
            ts,
            1024,
        )
        assert params["LAYERS"] == "GOES-East_ABI_Band2_Red_Visible_1km"
        assert params["WIDTH"] == "1024"
        assert params["TIME"] == "2024-01-15T06:00:00Z"
        assert "68.0,8.0,97.0,37.0" in params["BBOX"]

    def test_cache_key_consistent(self):
        """Same params must produce same cache key."""
        client = NASAGIBSClient()
        p1 = {"A": "1", "B": "2"}
        p2 = {"B": "2", "A": "1"}
        assert client._cache_key(p1) == client._cache_key(p2)

    def test_decode_image_shape(self):
        """Decoded image must be float32 [H, W, 3] in [0, 1]."""
        client = NASAGIBSClient()
        raw = make_png_bytes(64, 64)
        arr = client._decode_image(raw)
        assert arr.dtype == np.float32
        assert arr.shape == (64, 64, 3)
        assert arr.min() >= 0.0
        assert arr.max() <= 1.0

    def test_validate_frame_valid(self):
        """Normal image passes validation."""
        client = NASAGIBSClient()
        arr = np.random.uniform(0.2, 0.8, (64, 64, 3)).astype(np.float32)
        is_valid, flags = client._validate_frame(arr, "test_layer")
        assert is_valid

    def test_validate_frame_too_black(self):
        """Image with > 10% black pixels must fail DQ-01."""
        client = NASAGIBSClient()
        arr = np.zeros((64, 64, 3), dtype=np.float32)
        is_valid, flags = client._validate_frame(arr, "test_layer")
        assert not is_valid
        assert any("HIGH_BLACK_RATIO" in f for f in flags)

    def test_get_layer_info_all(self):
        """Layer info list must include all 5 GIBS layers."""
        info = NASAGIBSClient.get_layer_info()
        assert len(info) == 5

    def test_get_layer_info_specific(self):
        """Fetching a specific layer must return correct metadata."""
        info = NASAGIBSClient.get_layer_info("GOES-East_ABI_Band2_Red_Visible_1km")
        assert len(info) == 1
        assert info[0]["temporal_resolution_minutes"] == 10

    @pytest.mark.asyncio
    async def test_fetch_sequence_deduplicates(self):
        """Duplicate frames (same hash) must be deduplicated."""
        client = NASAGIBSClient()
        client._client = MagicMock()

        # All requests will return the same image bytes
        raw = make_png_bytes(64, 64, fill=(50, 100, 150))
        client._fetch_with_retry = AsyncMock(return_value=raw)
        client._rate_limit = AsyncMock()

        ts1 = datetime(2024, 1, 15, 6, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 15, 6, 10, 0, tzinfo=timezone.utc)
        ts3 = datetime(2024, 1, 15, 6, 20, 0, tzinfo=timezone.utc)

        frames = await client.fetch_sequence(
            "GOES-East_ABI_Band2_Red_Visible_1km",
            [68.0, 8.0, 97.0, 37.0],
            [ts1, ts2, ts3],
            resolution=64,
        )
        # All have same hash; only first should survive
        assert len(frames) == 1

    @pytest.mark.asyncio
    async def test_fetch_sequence_filters_invalid(self):
        """Invalid frames (black image) must be excluded."""
        client = NASAGIBSClient()
        client._client = MagicMock()
        client._rate_limit = AsyncMock()

        black_img = Image.new("RGB", (64, 64), color=(0, 0, 0))
        buf = io.BytesIO()
        black_img.save(buf, format="PNG")
        black_bytes = buf.getvalue()

        normal_img = make_png_bytes(64, 64, fill=(100, 150, 200))

        # Alternating: normal, black
        client._fetch_with_retry = AsyncMock(side_effect=[normal_img, black_bytes])

        ts1 = datetime(2024, 1, 15, 6, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 15, 6, 10, 0, tzinfo=timezone.utc)

        frames = await client.fetch_sequence(
            "GOES-East_ABI_Band2_Red_Visible_1km",
            [68.0, 8.0, 97.0, 37.0],
            [ts1, ts2],
            resolution=64,
        )
        assert len(frames) == 1  # Only the valid frame
        assert frames[0].is_valid
