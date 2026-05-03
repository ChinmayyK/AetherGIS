"""AetherGIS — Pytest tests: MOSDAC / INSAT provider (end-to-end).

Covers:
  Unit tests
  ──────────
  - _round_to_insat_slot()                      slot rounding helper
  - INSAT_LAYERS registry                        completeness & metadata
  - INSATClient.__init__                         uses mosdac_wms_url, NOT gibs
  - INSATClient._build_request_params            correct LAYERS=, TIME=, BBOX=
  - INSATClient._fetch_with_retry                OGC exception handling,
                                                  non-image content-type rejection,
                                                  real image bytes accepted
  - INSATClient.fetch_frame                      full round-trip with mocked HTTP
  - INSATProvider.base_url                       property returns MOSDAC url
  - INSATProvider._round_to_insat_slot           mirrors wms_client helper
  - INSATProvider._build_params                  same correctness as client
  - INSATProvider.fetch_frame                    valid image, OGC error, timeout,
                                                  unknown layer rejection
  - _insat_static_fallback()                     correct epochs, 30-min step
  - get_layer_capabilities_live (INSAT)          live MOSDAC path, fallback path,
                                                  unknown-layer fallback

  Regression tests
  ────────────────
  - INSATClient does NOT proxy to NASA GIBS
  - INSATProvider LAYERS param never contains "Himawari"
  - INSAT_LAYERS count is 11 (not the old stub of 1)
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from PIL import Image

# ── helpers ────────────────────────────────────────────────────────────────────

def _make_png(w: int = 64, h: int = 64, fill: tuple = (100, 150, 200)) -> bytes:
    img = Image.new("RGB", (w, h), color=fill)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_mosdac_caps_xml(layer_id: str, time_extent: str) -> str:
    """Minimal WMS 1.1.1 GetCapabilities XML for a single INSAT layer."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE WMT_MS_Capabilities SYSTEM "http://schemas.opengis.net/wms/1.1.1/OGC-WMS.dtd">
<WMT_MS_Capabilities version="1.1.1">
  <Service><Name>WMS</Name></Service>
  <Capability>
    <Layer>
      <Layer queryable="0">
        <Name>{layer_id}</Name>
        <Title>INSAT-3D {layer_id} (MOSDAC Live)</Title>
        <Extent name="time" default="{time_extent.split(',')[-1] if ',' in time_extent else time_extent}">{time_extent}</Extent>
      </Layer>
    </Layer>
  </Capability>
</WMT_MS_Capabilities>"""


_OGC_SERVICE_EXCEPTION = (
    '<?xml version="1.0"?>'
    '<ServiceExceptionReport version="1.1.1">'
    '<ServiceException code="InvalidParameterValue">'
    'msWMSLoadGetMapParams(): WMS server error. Invalid layer(s) given in the LAYERS parameter.'
    '</ServiceException>'
    '</ServiceExceptionReport>'
)

NASA_GIBS_URL = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
MOSDAC_URL    = "https://mosdac.gov.in/live/wms"


# ══════════════════════════════════════════════════════════════════════════════
# 1. Slot-rounding helper
# ══════════════════════════════════════════════════════════════════════════════

class TestRoundToInsatSlot:
    """_round_to_insat_slot must always produce :00 or :30 UTC, with zero seconds."""

    def setup_method(self):
        from backend.app.services.wms_client import _round_to_insat_slot
        self.fn = _round_to_insat_slot

    def _ts(self, h: int, m: int, s: int = 0) -> datetime:
        return datetime(2024, 6, 1, h, m, s, tzinfo=timezone.utc)

    def test_exactly_on_zero_slot(self):
        assert self.fn(self._ts(12, 0)).minute == 0

    def test_exactly_on_thirty_slot(self):
        assert self.fn(self._ts(12, 30)).minute == 30

    def test_rounds_down_to_zero(self):
        result = self.fn(self._ts(14, 7, 33))
        assert result.minute == 0
        assert result.second == 0
        assert result.hour == 14

    def test_rounds_down_to_thirty(self):
        result = self.fn(self._ts(9, 45, 59))
        assert result.minute == 30
        assert result.second == 0
        assert result.hour == 9

    def test_just_before_thirty(self):
        result = self.fn(self._ts(6, 29, 59))
        assert result.minute == 0

    def test_exactly_at_thirty(self):
        result = self.fn(self._ts(6, 30, 0))
        assert result.minute == 30

    def test_just_before_hour(self):
        result = self.fn(self._ts(6, 59, 59))
        assert result.minute == 30

    def test_preserves_date_and_hour(self):
        ts = datetime(2023, 11, 30, 23, 47, tzinfo=timezone.utc)
        result = self.fn(ts)
        assert result.year == 2023
        assert result.month == 11
        assert result.day == 30
        assert result.hour == 23
        assert result.minute == 30

    def test_midnight_slot(self):
        result = self.fn(self._ts(0, 12))
        assert result.minute == 0
        assert result.hour == 0

    def test_microseconds_cleared(self):
        ts = datetime(2024, 1, 1, 8, 22, 11, 999999, tzinfo=timezone.utc)
        result = self.fn(ts)
        assert result.microsecond == 0


# ══════════════════════════════════════════════════════════════════════════════
# 2. INSAT_LAYERS registry
# ══════════════════════════════════════════════════════════════════════════════

class TestInsatLayersRegistry:
    """The INSAT_LAYERS dict must be fully populated with all 11 channels."""

    def setup_method(self):
        from backend.app.services.wms_client import INSAT_LAYERS
        self.layers = INSAT_LAYERS

    EXPECTED_LAYERS = [
        "INSAT3D_VIS",
        "INSAT3D_TIR1",
        "INSAT3D_TIR2",
        "INSAT3D_MIR",
        "INSAT3D_SWIR",
        "INSAT3D_WV",
        "INSAT3DR_VIS",
        "INSAT3DR_TIR1",
        "INSAT3DR_TIR2",
        "INSAT3DR_MIR",
        "INSAT3DR_WV",
    ]

    def test_total_count_is_eleven(self):
        assert len(self.layers) == 11, (
            f"Expected 11 INSAT layers, got {len(self.layers)}: {list(self.layers)}"
        )

    def test_regression_not_old_stub_of_one(self):
        assert len(self.layers) > 1, "INSAT_LAYERS is still the old 1-entry stub"

    @pytest.mark.parametrize("layer_id", EXPECTED_LAYERS)
    def test_layer_present(self, layer_id: str):
        assert layer_id in self.layers, f"{layer_id} missing from INSAT_LAYERS"

    @pytest.mark.parametrize("layer_id", EXPECTED_LAYERS)
    def test_required_fields_present(self, layer_id: str):
        info = self.layers[layer_id]
        for field in ("name", "temporal_resolution_minutes", "use_case", "description",
                      "nadir_lon", "coverage_lon_min", "coverage_lon_max",
                      "coverage_lat_min", "coverage_lat_max",
                      "preset_regions", "default_preset"):
            assert field in info, f"{layer_id} is missing field {field!r}"

    @pytest.mark.parametrize("layer_id", EXPECTED_LAYERS)
    def test_temporal_resolution_is_30_minutes(self, layer_id: str):
        assert self.layers[layer_id]["temporal_resolution_minutes"] == 30, (
            f"{layer_id} temporal_resolution_minutes should be 30"
        )

    def test_insat3d_nadir_lon(self):
        for lid in ["INSAT3D_VIS", "INSAT3D_TIR1", "INSAT3D_TIR2",
                    "INSAT3D_MIR", "INSAT3D_SWIR", "INSAT3D_WV"]:
            assert self.layers[lid]["nadir_lon"] == 82.0, (
                f"{lid} nadir_lon should be 82.0°E"
            )

    def test_insat3dr_nadir_lon(self):
        for lid in ["INSAT3DR_VIS", "INSAT3DR_TIR1", "INSAT3DR_TIR2",
                    "INSAT3DR_MIR", "INSAT3DR_WV"]:
            assert self.layers[lid]["nadir_lon"] == 83.0, (
                f"{lid} nadir_lon should be 83.0°E (INSAT-3DR)"
            )

    def test_coverage_bounds(self):
        for lid, info in self.layers.items():
            assert info["coverage_lon_min"] < info["coverage_lon_max"]
            assert info["coverage_lat_min"] < info["coverage_lat_max"]

    def test_preset_regions_non_empty(self):
        for lid, info in self.layers.items():
            assert info["preset_regions"], f"{lid} has empty preset_regions"

    def test_default_preset_exists_in_preset_regions(self):
        for lid, info in self.layers.items():
            assert info["default_preset"] in info["preset_regions"], (
                f"{lid}: default_preset {info['default_preset']!r} not in preset_regions"
            )


# ══════════════════════════════════════════════════════════════════════════════
# 3. INSATClient — unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestInsatClient:

    def setup_method(self):
        from backend.app.services.wms_client import INSATClient
        self.Client = INSATClient

    # ── 3a. Endpoint ──────────────────────────────────────────────────────────

    def test_base_url_is_mosdac_not_gibs(self):
        """CRITICAL: INSATClient must use MOSDAC endpoint, not NASA GIBS."""
        client = self.Client()
        assert client.base_url == MOSDAC_URL, (
            f"INSATClient.base_url is {client.base_url!r}; "
            f"expected MOSDAC URL {MOSDAC_URL!r}. "
            "The client was still proxying to NASA GIBS!"
        )
        assert NASA_GIBS_URL not in client.base_url, (
            "INSATClient is still using NASA GIBS URL — this is the core bug"
        )

    # ── 3b. _build_request_params ─────────────────────────────────────────────

    def test_params_layers_equals_layer_id(self):
        """LAYERS param must be the actual INSAT layer ID, not 'Himawari...'."""
        client = self.Client()
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        for layer_id in ["INSAT3D_VIS", "INSAT3D_TIR1", "INSAT3DR_WV"]:
            params = client._build_request_params(layer_id, [65.0, 5.0, 100.0, 38.0], ts, 512)
            assert params["LAYERS"] == layer_id, (
                f"LAYERS should be {layer_id!r}, got {params['LAYERS']!r}"
            )

    def test_params_no_himawari_leak(self):
        """Himawari layer name must NEVER appear in a MOSDAC request."""
        client = self.Client()
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        params = client._build_request_params("INSAT3D_VIS", [65.0, 5.0, 100.0, 38.0], ts, 512)
        assert "Himawari" not in params["LAYERS"], (
            f"Himawari layer leaked into MOSDAC params: {params['LAYERS']!r}"
        )

    def test_params_time_rounded_to_slot(self):
        """TIME param must be rounded to nearest :00/:30 UTC slot."""
        client = self.Client()
        ts = datetime(2024, 6, 1, 14, 22, 55, tzinfo=timezone.utc)  # off-slot
        params = client._build_request_params("INSAT3D_TIR1", [65.0, 5.0, 100.0, 38.0], ts, 256)
        assert params["TIME"] == "2024-06-01T14:00:00Z", (
            f"Expected rounded slot 14:00:00, got {params['TIME']!r}"
        )

    def test_params_time_thirty_slot(self):
        """Times past :30 should round to :30."""
        client = self.Client()
        ts = datetime(2024, 6, 1, 7, 48, tzinfo=timezone.utc)
        params = client._build_request_params("INSAT3D_WV", [65.0, 5.0, 100.0, 38.0], ts, 256)
        assert params["TIME"] == "2024-06-01T07:30:00Z"

    def test_params_bbox_correct_order(self):
        """BBOX must be minlon,minlat,maxlon,maxlat for MOSDAC WMS 1.1.1."""
        client = self.Client()
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        bbox = [65.0, 5.0, 100.0, 38.0]
        params = client._build_request_params("INSAT3D_VIS", bbox, ts, 256)
        assert params["BBOX"] == "65.0,5.0,100.0,38.0"

    def test_params_wms_service_version(self):
        client = self.Client()
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        params = client._build_request_params("INSAT3D_VIS", [65.0, 5.0, 100.0, 38.0], ts, 256)
        assert params["SERVICE"] == "WMS"
        assert params["VERSION"] == "1.1.1"
        assert params["REQUEST"] == "GetMap"

    def test_params_unknown_layer_raises(self):
        from backend.app.services.wms_client import WMSClientError
        client = self.Client()
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        with pytest.raises(WMSClientError, match="Unknown INSAT layer"):
            client._build_request_params("NOT_A_REAL_LAYER", [65.0, 5.0, 100.0, 38.0], ts, 256)

    def test_params_api_key_injected_when_set(self):
        client = self.Client()
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(client.settings, "mosdac_api_key", "test-api-key-123"):
            params = client._build_request_params("INSAT3D_VIS", [65.0, 5.0, 100.0, 38.0], ts, 256)
        assert params.get("key") == "test-api-key-123"

    def test_params_no_api_key_when_empty(self):
        client = self.Client()
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(client.settings, "mosdac_api_key", ""):
            params = client._build_request_params("INSAT3D_VIS", [65.0, 5.0, 100.0, 38.0], ts, 256)
        assert "key" not in params

    # ── 3c. _fetch_with_retry ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_fetch_accepts_valid_image(self):
        """Valid image/png response must be returned as bytes."""
        client = self.Client()
        client._client = MagicMock()
        client._rate_limit = AsyncMock()

        png_bytes = _make_png(64, 64)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.content = png_bytes
        mock_resp.raise_for_status = MagicMock()
        client._client.get = AsyncMock(return_value=mock_resp)

        params = {"LAYERS": "INSAT3D_VIS", "TIME": "2024-06-01T12:00:00Z"}
        result = await client._fetch_with_retry(params)
        assert result == png_bytes

    @pytest.mark.asyncio
    async def test_fetch_rejects_ogc_exception(self):
        """MOSDAC OGC ServiceException (200 + XML) must raise WMSClientError."""
        from backend.app.services.wms_client import WMSClientError
        client = self.Client()
        client._client = MagicMock()
        client._rate_limit = AsyncMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/vnd.ogc.se_xml"}
        mock_resp.text = _OGC_SERVICE_EXCEPTION
        mock_resp.raise_for_status = MagicMock()
        client._client.get = AsyncMock(return_value=mock_resp)

        with pytest.raises(WMSClientError, match="MOSDAC WMS.*failed"):
            await client._fetch_with_retry({"LAYERS": "INSAT3D_VIS", "TIME": "2024-06-01T12:00:00Z"})

    @pytest.mark.asyncio
    async def test_fetch_rejects_text_xml_exception(self):
        """text/xml ServiceException must also be rejected."""
        from backend.app.services.wms_client import WMSClientError
        client = self.Client()
        client._client = MagicMock()
        client._rate_limit = AsyncMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/xml; charset=utf-8"}
        mock_resp.text = _OGC_SERVICE_EXCEPTION
        mock_resp.raise_for_status = MagicMock()
        client._client.get = AsyncMock(return_value=mock_resp)

        with pytest.raises(WMSClientError):
            await client._fetch_with_retry({"LAYERS": "INSAT3D_VIS", "TIME": "2024-06-01T12:00:00Z"})

    @pytest.mark.asyncio
    async def test_fetch_rejects_unexpected_content_type(self):
        """Responses with content-type other than image/* must raise WMSClientError."""
        from backend.app.services.wms_client import WMSClientError
        client = self.Client()
        client._client = MagicMock()
        client._rate_limit = AsyncMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.text = "<html>Gateway Error</html>"
        mock_resp.raise_for_status = MagicMock()
        client._client.get = AsyncMock(return_value=mock_resp)

        with pytest.raises(WMSClientError):
            await client._fetch_with_retry({"LAYERS": "INSAT3D_VIS", "TIME": "2024-06-01T12:00:00Z"})

    @pytest.mark.asyncio
    async def test_fetch_uses_mosdac_url_not_gibs(self):
        """_fetch_with_retry must POST to MOSDAC URL, NOT NASA GIBS."""
        client = self.Client()
        client._client = MagicMock()
        client._rate_limit = AsyncMock()

        png_bytes = _make_png(32, 32)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.content = png_bytes
        mock_resp.raise_for_status = MagicMock()
        client._client.get = AsyncMock(return_value=mock_resp)

        params = {"LAYERS": "INSAT3D_VIS", "TIME": "2024-06-01T12:00:00Z"}
        await client._fetch_with_retry(params)

        call_url = client._client.get.call_args[0][0]
        assert call_url == MOSDAC_URL, (
            f"Request went to {call_url!r} instead of MOSDAC URL {MOSDAC_URL!r}"
        )
        assert NASA_GIBS_URL not in call_url

    # ── 3d. full fetch_frame round-trip ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_fetch_frame_valid_image_returns_frame(self):
        """fetch_frame with a valid PNG must return a SatelliteFrame."""
        from backend.app.services.wms_client import INSATClient, SatelliteFrame
        client = INSATClient()
        client._client = MagicMock()
        client._rate_limit = AsyncMock()

        png_bytes = _make_png(64, 64, fill=(80, 120, 160))
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.content = png_bytes
        mock_resp.raise_for_status = MagicMock()
        client._client.get = AsyncMock(return_value=mock_resp)

        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        async with client:
            client._client = MagicMock()
            client._client.get = AsyncMock(return_value=mock_resp)
            client._rate_limit = AsyncMock()

            # patch aclose so context manager doesn't error
            client._client.aclose = AsyncMock()

            frame = await client.fetch_frame("INSAT3D_VIS", [65.0, 5.0, 100.0, 38.0], ts, 64)

        assert isinstance(frame, SatelliteFrame)
        assert frame.layer_id == "INSAT3D_VIS"
        assert frame.image.dtype == np.float32
        assert frame.image.shape == (64, 64, 3)
        assert frame.source == "nasa_gibs"  # SatelliteFrame.source default

    @pytest.mark.asyncio
    async def test_fetch_frame_unknown_layer_raises(self):
        from backend.app.services.wms_client import INSATClient, WMSClientError
        client = INSATClient()

        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        with pytest.raises(WMSClientError, match="Unknown layer"):
            async with client:
                client._client.aclose = AsyncMock()
                await client.fetch_frame("FAKE_LAYER_XYZ", [65.0, 5.0, 100.0, 38.0], ts, 64)


# ══════════════════════════════════════════════════════════════════════════════
# 4. INSATProvider (satellite_providers.py)
# ══════════════════════════════════════════════════════════════════════════════

class TestInsatProvider:

    def setup_method(self):
        from backend.app.services.satellite_providers import INSATProvider
        self.provider = INSATProvider()

    def test_name_is_insat(self):
        assert self.provider.name == "insat"

    def test_priority_is_one(self):
        assert self.provider.priority == 1

    def test_base_url_is_mosdac_not_gibs(self):
        """CRITICAL: INSATProvider must use MOSDAC URL."""
        url = self.provider.base_url
        assert url == MOSDAC_URL, (
            f"INSATProvider.base_url is {url!r} — expected MOSDAC URL, not NASA GIBS"
        )
        assert NASA_GIBS_URL not in url

    def test_build_params_layers_not_himawari(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        params = self.provider._build_params("INSAT3D_VIS", [65.0, 5.0, 100.0, 38.0], ts, 512)
        assert "Himawari" not in params["LAYERS"], (
            f"Himawari leaked into MOSDAC LAYERS param: {params['LAYERS']!r}"
        )

    def test_build_params_layers_equals_layer_id(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        for lid in ["INSAT3D_TIR1", "INSAT3DR_WV", "INSAT3D_MIR"]:
            params = self.provider._build_params(lid, [65.0, 5.0, 100.0, 38.0], ts, 256)
            assert params["LAYERS"] == lid

    def test_build_params_time_rounded(self):
        ts = datetime(2024, 6, 1, 14, 47, 12, tzinfo=timezone.utc)  # off-slot
        params = self.provider._build_params("INSAT3D_TIR1", [65.0, 5.0, 100.0, 38.0], ts, 256)
        assert params["TIME"] == "2024-06-01T14:30:00Z"

    def test_provider_slot_rounding_mirrors_wms_client(self):
        """Both helpers must produce identical results."""
        from backend.app.services.wms_client import _round_to_insat_slot
        ts = datetime(2024, 3, 15, 11, 23, 45, tzinfo=timezone.utc)
        assert self.provider._round_to_insat_slot(ts) == _round_to_insat_slot(ts)

    @pytest.mark.asyncio
    async def test_fetch_frame_valid_image(self):
        import httpx
        png_bytes = _make_png(64, 64, fill=(60, 90, 120))
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.content = png_bytes
        mock_resp.url = MOSDAC_URL + "?LAYERS=INSAT3D_VIS"

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)

        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        from backend.app.services.satellite_providers import ProviderFrame
        frame = await self.provider.fetch_frame("INSAT3D_VIS", [65.0, 5.0, 100.0, 38.0], ts, 64, mock_client)

        assert frame is not None
        assert isinstance(frame, ProviderFrame)
        assert frame.source == "insat"
        assert frame.layer == "INSAT3D_VIS"
        assert frame.image.shape == (64, 64, 3)
        assert frame.image.dtype == np.float32

    @pytest.mark.asyncio
    async def test_fetch_frame_requests_mosdac_url(self):
        """fetch_frame must call the MOSDAC endpoint, not NASA GIBS."""
        import httpx
        png_bytes = _make_png(32, 32)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.content = png_bytes
        mock_resp.url = MOSDAC_URL

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)

        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        await self.provider.fetch_frame("INSAT3D_VIS", [65.0, 5.0, 100.0, 38.0], ts, 32, mock_client)

        call_url = mock_client.get.call_args[0][0]
        assert call_url == MOSDAC_URL, f"Request went to {call_url!r}, not MOSDAC"
        assert NASA_GIBS_URL not in call_url

    @pytest.mark.asyncio
    async def test_fetch_frame_ogc_exception_returns_none(self):
        """OGC ServiceException from MOSDAC must return None (not raise)."""
        import httpx
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/vnd.ogc.se_xml"}
        mock_resp.text = _OGC_SERVICE_EXCEPTION

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)

        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        frame = await self.provider.fetch_frame("INSAT3D_VIS", [65.0, 5.0, 100.0, 38.0], ts, 64, mock_client)
        assert frame is None

    @pytest.mark.asyncio
    async def test_fetch_frame_non_200_returns_none(self):
        import httpx
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.headers = {"content-type": "text/plain"}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)

        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        frame = await self.provider.fetch_frame("INSAT3D_TIR1", [65.0, 5.0, 100.0, 38.0], ts, 64, mock_client)
        assert frame is None

    @pytest.mark.asyncio
    async def test_fetch_frame_timeout_returns_none(self):
        import httpx
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        frame = await self.provider.fetch_frame("INSAT3D_VIS", [65.0, 5.0, 100.0, 38.0], ts, 64, mock_client)
        assert frame is None

    @pytest.mark.asyncio
    async def test_fetch_frame_unknown_layer_returns_none(self):
        """Unknown layer IDs must be silently rejected (not raise)."""
        import httpx
        mock_client = AsyncMock(spec=httpx.AsyncClient)

        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        frame = await self.provider.fetch_frame("NOT_AN_INSAT_LAYER", [65.0, 5.0, 100.0, 38.0], ts, 64, mock_client)
        assert frame is None
        mock_client.get.assert_not_called()  # must not hit the network

    @pytest.mark.asyncio
    async def test_fetch_frame_layers_param_sent_correctly(self):
        """The LAYERS= WMS param in the actual HTTP call must be the layer_id."""
        import httpx
        png_bytes = _make_png(32, 32)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.content = png_bytes
        mock_resp.url = MOSDAC_URL

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)

        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        await self.provider.fetch_frame("INSAT3DR_TIR1", [65.0, 5.0, 100.0, 38.0], ts, 32, mock_client)

        call_kwargs = mock_client.get.call_args[1]
        sent_params = call_kwargs.get("params", {})
        assert sent_params.get("LAYERS") == "INSAT3DR_TIR1", (
            f"Expected LAYERS=INSAT3DR_TIR1 in HTTP call, got {sent_params.get('LAYERS')!r}"
        )
        assert "Himawari" not in str(sent_params), "Himawari layer leaked into MOSDAC request"


# ══════════════════════════════════════════════════════════════════════════════
# 5. layer_capabilities — INSAT paths
# ══════════════════════════════════════════════════════════════════════════════

class TestInsatLayerCapabilities:

    @pytest.mark.asyncio
    async def test_static_fallback_insat3d_epoch(self):
        """Static fallback must use 2014-02-15 for INSAT-3D layers."""
        from backend.app.services.layer_capabilities import _insat_static_fallback, _INSAT3D_EPOCH
        from backend.app.services.wms_client import INSAT_LAYERS

        fb = _insat_static_fallback("INSAT3D_VIS", INSAT_LAYERS["INSAT3D_VIS"])
        assert fb.time_start == _INSAT3D_EPOCH
        assert fb.step_minutes == 30
        assert fb.temporal_resolution_minutes == 30.0
        assert fb.time_source_live is False

    @pytest.mark.asyncio
    async def test_static_fallback_insat3dr_epoch(self):
        """Static fallback must use 2016-09-28 for INSAT-3DR layers."""
        from backend.app.services.layer_capabilities import _insat_static_fallback, _INSAT3DR_EPOCH
        from backend.app.services.wms_client import INSAT_LAYERS

        fb = _insat_static_fallback("INSAT3DR_WV", INSAT_LAYERS["INSAT3DR_WV"])
        assert fb.time_start == _INSAT3DR_EPOCH

    @pytest.mark.asyncio
    async def test_static_fallback_latest_is_on_30_min_slot(self):
        """latest_available_time must always be a :00 or :30 UTC slot."""
        from backend.app.services.layer_capabilities import _insat_static_fallback
        from backend.app.services.wms_client import INSAT_LAYERS

        fb = _insat_static_fallback("INSAT3D_TIR1", INSAT_LAYERS["INSAT3D_TIR1"])
        assert fb.latest_available_time is not None
        assert fb.latest_available_time.minute in (0, 30)
        assert fb.latest_available_time.second == 0

    @pytest.mark.asyncio
    async def test_live_capabilities_uses_mosdac_xml(self):
        """
        When MOSDAC GetCapabilities returns a layer element with a time extent,
        get_layer_capabilities_live must parse it and return time_source_live=True.
        """
        from backend.app.services import layer_capabilities as lc

        time_extent = "2024-01-01T00:00:00Z/2024-06-01T12:00:00Z/PT30M"
        caps_xml = _make_mosdac_caps_xml("INSAT3D_VIS", time_extent)

        # Clear the module cache
        lc._LAYER_CACHE.clear()
        lc._MOSDAC_XML_CACHE = None

        async def fake_mosdac_caps():
            return ET.fromstring(caps_xml)

        with patch.object(lc, "_get_mosdac_capabilities_root", fake_mosdac_caps):
            result = await lc.get_layer_capabilities_live("INSAT3D_VIS")

        assert result.time_source_live is True
        assert result.step_minutes == 30
        assert result.time_start is not None
        assert result.time_end is not None
        assert result.time_start.year == 2024
        assert result.time_start.month == 1

    @pytest.mark.asyncio
    async def test_live_capabilities_fallback_when_mosdac_down(self):
        """
        When MOSDAC GetCapabilities raises, must fall back gracefully to static
        epoch with time_source_live=False.
        """
        from backend.app.services import layer_capabilities as lc

        lc._LAYER_CACHE.clear()
        lc._MOSDAC_XML_CACHE = None

        async def failing_mosdac():
            raise ConnectionError("MOSDAC unreachable")

        with patch.object(lc, "_get_mosdac_capabilities_root", failing_mosdac):
            result = await lc.get_layer_capabilities_live("INSAT3DR_TIR1")

        assert result.time_source_live is False
        assert result.step_minutes == 30
        assert result.latest_available_time is not None

    @pytest.mark.asyncio
    async def test_live_capabilities_unknown_layer_raises_key_error(self):
        from backend.app.services import layer_capabilities as lc
        with pytest.raises(KeyError):
            await lc.get_layer_capabilities_live("COMPLETELY_FAKE_LAYER_XYZ")

    @pytest.mark.asyncio
    async def test_live_capabilities_cache_hit(self):
        """Second call for same layer must return cached result without hitting MOSDAC."""
        from backend.app.services import layer_capabilities as lc

        lc._LAYER_CACHE.clear()
        lc._MOSDAC_XML_CACHE = None

        call_count = 0

        async def mock_mosdac_caps():
            nonlocal call_count
            call_count += 1
            return ET.fromstring(_make_mosdac_caps_xml(
                "INSAT3D_VIS",
                "2024-01-01T00:00:00Z/2024-06-01T12:00:00Z/PT30M"
            ))

        with patch.object(lc, "_get_mosdac_capabilities_root", mock_mosdac_caps):
            r1 = await lc.get_layer_capabilities_live("INSAT3D_VIS")
            r2 = await lc.get_layer_capabilities_live("INSAT3D_VIS")

        # Both calls return the same object (cache hit on second)
        assert r1.layer_id == r2.layer_id == "INSAT3D_VIS"
        assert call_count == 1, "MOSDAC capabilities were fetched twice — cache is not working"

    @pytest.mark.asyncio
    async def test_live_capabilities_layer_missing_from_mosdac_uses_static(self):
        """
        If MOSDAC GetCapabilities succeeds but doesn't contain the requested
        layer, fall back to static epoch with a warning (not an error).
        """
        from backend.app.services import layer_capabilities as lc

        lc._LAYER_CACHE.clear()
        lc._MOSDAC_XML_CACHE = None

        # Return a valid XML but with a *different* layer inside
        caps_xml = _make_mosdac_caps_xml("INSAT3D_TIR2", "2024-01-01T00:00:00Z/2024-06-01T12:00:00Z/PT30M")

        async def mock_caps():
            return ET.fromstring(caps_xml)

        with patch.object(lc, "_get_mosdac_capabilities_root", mock_caps):
            # Ask for INSAT3D_VIS which is NOT in the xml
            result = await lc.get_layer_capabilities_live("INSAT3D_VIS")

        assert result.time_source_live is False   # static fallback
        assert result.step_minutes == 30


# ══════════════════════════════════════════════════════════════════════════════
# 6. Regression: MOSDAC never proxies to NASA GIBS
# ══════════════════════════════════════════════════════════════════════════════

class TestNoGibsProxyRegression:
    """
    These tests guard against the original bugs being silently re-introduced:

      Bug 1: INSATClient.base_url pointed to NASA GIBS
      Bug 2: INSATClient._build_request_params sent 'Himawari_AHI_Band3_Red_Visible_1km'
      Bug 3: INSATProvider.base_url pointed to NASA GIBS
      Bug 4: INSATProvider.fetch_frame sent 'Himawari_AHI_Band3_Red_Visible_1km'
    """

    def test_insat_client_url_is_not_nasa_gibs(self):
        from backend.app.services.wms_client import INSATClient
        client = INSATClient()
        assert "gibs.earthdata.nasa.gov" not in client.base_url
        assert "mosdac.gov.in" in client.base_url

    def test_insat_client_params_have_no_himawari(self):
        from backend.app.services.wms_client import INSATClient
        client = INSATClient()
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        for lid in ["INSAT3D_VIS", "INSAT3D_TIR1", "INSAT3D_TIR2",
                    "INSAT3D_MIR", "INSAT3D_SWIR", "INSAT3D_WV",
                    "INSAT3DR_VIS", "INSAT3DR_TIR1", "INSAT3DR_TIR2",
                    "INSAT3DR_MIR", "INSAT3DR_WV"]:
            params = client._build_request_params(lid, [65.0, 5.0, 100.0, 38.0], ts, 256)
            assert "Himawari" not in params["LAYERS"], (
                f"Layer {lid}: Himawari leaked into LAYERS param: {params['LAYERS']!r}"
            )
            assert "Himawari" not in params.get("LAYERS", "")

    def test_insat_provider_url_is_not_nasa_gibs(self):
        from backend.app.services.satellite_providers import INSATProvider
        provider = INSATProvider()
        assert "gibs.earthdata.nasa.gov" not in provider.base_url
        assert "mosdac.gov.in" in provider.base_url

    @pytest.mark.asyncio
    async def test_insat_provider_http_call_goes_to_mosdac_not_gibs(self):
        """Any HTTP call made by INSATProvider.fetch_frame must go to MOSDAC, not GIBS."""
        import httpx
        from backend.app.services.satellite_providers import INSATProvider

        captured_urls: list[str] = []

        async def mock_get(url: str, **kwargs):
            captured_urls.append(url)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {"content-type": "image/png"}
            mock_resp.content = _make_png(32, 32)
            mock_resp.url = url
            return mock_resp

        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=mock_get)

        provider = INSATProvider()
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        await provider.fetch_frame("INSAT3D_VIS", [65.0, 5.0, 100.0, 38.0], ts, 32, mock_client)

        assert len(captured_urls) == 1
        assert "mosdac.gov.in" in captured_urls[0], (
            f"HTTP request went to {captured_urls[0]!r} instead of mosdac.gov.in"
        )
        assert "gibs.earthdata.nasa.gov" not in captured_urls[0], (
            "HTTP request proxied to NASA GIBS — the original bug is back!"
        )
