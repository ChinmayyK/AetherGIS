"""AetherGIS — Multi-Source Satellite Provider (MODULE 3).

Provides a unified SatelliteProvider interface with:
  • NASA GIBS (GOES)
  • Himawari-8/9 (via JAXA)
  • INSAT-3D/3DR (via MOSDAC)
  • Fallback static dataset (synthetic gradient)

Auto-fallback: if one source fails, the next in the priority list is tried.
All fallback events are logged and recorded in the audit trail.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx
import numpy as np
from PIL import Image

from backend.app.config import get_settings
from backend.app.utils.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)


# ── Frame result ──────────────────────────────────────────────────────────────

@dataclass
class ProviderFrame:
    image: np.ndarray          # float32 H×W×3 in [0,1]
    timestamp: datetime
    source: str                # provider name
    layer: str
    bbox: list[float]
    resolution: int
    url_used: Optional[str] = None
    fallback_used: bool = False
    cache_hit: bool = False


# ── Abstract base ──────────────────────────────────────────────────────────────

class SatelliteProvider(ABC):
    name: str = "base"
    priority: int = 99

    @abstractmethod
    async def fetch_frame(
        self,
        layer_id: str,
        bbox: list[float],
        timestamp: datetime,
        resolution: int,
        client: httpx.AsyncClient,
    ) -> Optional[ProviderFrame]:
        ...

    async def is_available(self, client: httpx.AsyncClient) -> bool:
        return True


def _decode_image(raw: bytes, resolution: int) -> Optional[np.ndarray]:
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img = img.resize((resolution, resolution), Image.LANCZOS)
        arr = np.array(img, dtype=np.float32) / 255.0
        return arr
    except Exception:
        return None


# ── NASA GIBS provider ─────────────────────────────────────────────────────────

class NASAGIBSProvider(SatelliteProvider):
    name = "nasa_gibs"
    priority = 3
    base_url = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"

    async def fetch_frame(
        self,
        layer_id: str,
        bbox: list[float],
        timestamp: datetime,
        resolution: int,
        client: httpx.AsyncClient,
    ) -> Optional[ProviderFrame]:
        date_str = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "SERVICE": "WMS",
            "VERSION": "1.1.1",
            "REQUEST": "GetMap",
            "LAYERS": layer_id,
            "SRS": "EPSG:4326",
            "BBOX": f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}",
            "WIDTH": str(resolution),
            "HEIGHT": str(resolution),
            "FORMAT": "image/png",
            "TRANSPARENT": "TRUE",
            "TIME": date_str,
        }
        url = self.base_url
        try:
            resp = await client.get(url, params=params, timeout=settings.wms_timeout_seconds)
            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image"):
                img = _decode_image(resp.content, resolution)
                if img is not None:
                    return ProviderFrame(
                        image=img,
                        timestamp=timestamp,
                        source=self.name,
                        layer=layer_id,
                        bbox=bbox,
                        resolution=resolution,
                        url_used=str(resp.url),
                    )
        except Exception as exc:
            logger.warning("GIBS fetch failed", layer=layer_id, ts=date_str, error=str(exc))
        return None


# ── Himawari provider ─────────────────────────────────────────────────────────

class HimawariProvider(SatelliteProvider):
    name = "himawari"
    priority = 2
    # JAXA Himawari Monitor WMS (public endpoint)
    base_url = "https://www.eorc.jaxa.jp/ptree/himawari-8/api/fd/infrared/latest/image"

    async def fetch_frame(
        self,
        layer_id: str,
        bbox: list[float],
        timestamp: datetime,
        resolution: int,
        client: httpx.AsyncClient,
    ) -> Optional[ProviderFrame]:
        # Himawari provides pan-Asia/Pacific imagery; map generic layer to Himawari bands
        band = "B03"  # visible
        timestamp.strftime("%Y%m%d_%H%M00")
        params = {
            "SERVICE": "WMS",
            "VERSION": "1.1.1",
            "REQUEST": "GetMap",
            "LAYERS": f"Himawari_{band}",
            "SRS": "EPSG:4326",
            "BBOX": f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}",
            "WIDTH": str(resolution),
            "HEIGHT": str(resolution),
            "FORMAT": "image/png",
            "TIME": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        # Attempt via NRSC/Bhuvan as proxy for Himawari
        bhuvan_url = "https://bhuvan-ras2.nrsc.gov.in/bhuvan/wms"
        try:
            resp = await client.get(bhuvan_url, params=params, timeout=settings.wms_timeout_seconds)
            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image"):
                img = _decode_image(resp.content, resolution)
                if img is not None:
                    return ProviderFrame(
                        image=img,
                        timestamp=timestamp,
                        source=self.name,
                        layer=layer_id,
                        bbox=bbox,
                        resolution=resolution,
                        url_used=str(resp.url),
                    )
        except Exception as exc:
            logger.warning("Himawari fetch failed", error=str(exc))
        return None


# ── INSAT provider ────────────────────────────────────────────────────────────

class INSATProvider(SatelliteProvider):
    """
    MOSDAC WMS provider for INSAT-3D / INSAT-3DR satellite imagery.

    Fetches half-hourly composites from the ISRO/SAC MOSDAC live WMS:
        https://mosdac.gov.in/live/wms

    Layer names (LAYERS= WMS param) match INSAT_LAYERS keys 1:1.
    Timestamps are rounded to the nearest :00/:30 UTC slot before the
    request is issued — off-slot times cause MOSDAC to return an OGC
    ServiceException rather than the nearest valid composite.
    """

    name = "insat"
    priority = 1

    # ── CRITICAL FIX: real MOSDAC endpoint, not NASA GIBS ────────────────────
    @property
    def base_url(self) -> str:  # property so settings are read at call time
        return settings.mosdac_wms_url  # 'https://mosdac.gov.in/live/wms'

    @staticmethod
    def _round_to_insat_slot(ts: datetime) -> datetime:
        """Round down to nearest 30-minute INSAT composite slot (:00 or :30 UTC)."""
        slot_minute = 0 if ts.minute < 30 else 30
        return ts.replace(minute=slot_minute, second=0, microsecond=0)

    def _build_params(
        self,
        layer_id: str,
        bbox: list[float],
        timestamp: datetime,
        resolution: int,
    ) -> dict[str, str]:
        """
        Build WMS 1.1.1 GetMap parameters for MOSDAC.

        BBOX convention:  minlon,minlat,maxlon,maxlat  (EPSG:4326).
        Note the GIBS providers swap bbox[0]/[1] — MOSDAC does NOT require
        this swap; it uses the standard minx,miny,maxx,maxy order.
        """
        minlon, minlat, maxlon, maxlat = bbox
        slot_ts = self._round_to_insat_slot(timestamp)
        params: dict[str, str] = {
            "SERVICE": "WMS",
            "VERSION": "1.1.1",
            "REQUEST": "GetMap",
            # ── CRITICAL FIX: use the actual MOSDAC layer name, NOT Himawari ──
            "LAYERS": layer_id,
            "STYLES": "",
            "SRS": "EPSG:4326",
            "BBOX": f"{minlon},{minlat},{maxlon},{maxlat}",
            "WIDTH": str(resolution),
            "HEIGHT": str(resolution),
            "FORMAT": "image/png",
            "TRANSPARENT": "TRUE",
            "TIME": slot_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if settings.mosdac_api_key:
            params["key"] = settings.mosdac_api_key
        return params

    async def fetch_frame(
        self,
        layer_id: str,
        bbox: list[float],
        timestamp: datetime,
        resolution: int,
        client: httpx.AsyncClient,
    ) -> Optional[ProviderFrame]:
        from backend.app.services.wms_client import INSAT_LAYERS

        # Reject unknown INSAT layer IDs before hitting the network
        if layer_id not in INSAT_LAYERS:
            logger.warning(
                "INSATProvider: unknown layer_id — skipping",
                layer_id=layer_id,
                known=list(INSAT_LAYERS.keys()),
            )
            return None

        params = self._build_params(layer_id, bbox, timestamp, resolution)

        try:
            resp = await client.get(
                self.base_url,
                params=params,
                timeout=settings.wms_timeout_seconds,
                follow_redirects=True,
            )

            if resp.status_code != 200:
                logger.warning(
                    "MOSDAC WMS non-200",
                    status=resp.status_code,
                    layer=layer_id,
                    time=params["TIME"],
                )
                return None

            ct = resp.headers.get("content-type", "")

            # MOSDAC returns OGC ServiceException as HTTP 200 + XML/text —
            # must detect this and treat it as a failed fetch, not a valid image
            if ct.startswith(("application/vnd.ogc", "text/xml", "application/xml", "text/plain")):
                logger.warning(
                    "MOSDAC WMS service exception",
                    layer=layer_id,
                    time=params["TIME"],
                    body_preview=resp.text[:200],
                )
                return None

            if not ct.startswith("image"):
                logger.warning(
                    "MOSDAC WMS unexpected content-type",
                    content_type=ct,
                    layer=layer_id,
                    time=params["TIME"],
                )
                return None

            img = _decode_image(resp.content, resolution)
            if img is None:
                logger.warning("MOSDAC image decode failed", layer=layer_id, time=params["TIME"])
                return None

            return ProviderFrame(
                image=img,
                timestamp=timestamp,
                source=self.name,
                layer=layer_id,
                bbox=bbox,
                resolution=resolution,
                url_used=str(resp.url),
            )

        except httpx.TimeoutException:
            logger.warning("MOSDAC WMS timeout", layer=layer_id, time=params.get("TIME"))
        except Exception as exc:
            logger.warning("MOSDAC fetch failed", layer=layer_id, error=str(exc))

        return None


# ── Fallback static dataset ────────────────────────────────────────────────────

class StaticFallbackProvider(SatelliteProvider):
    name = "static_fallback"
    priority = 99

    async def fetch_frame(
        self,
        layer_id: str,
        bbox: list[float],
        timestamp: datetime,
        resolution: int,
        client: httpx.AsyncClient,
    ) -> Optional[ProviderFrame]:
        """Generate a synthetic gradient frame so the pipeline never fully fails."""
        logger.warning("Using static fallback dataset", layer=layer_id, ts=timestamp.isoformat())
        rng = np.random.default_rng(
            seed=int(hashlib.sha256(f"{layer_id}{timestamp.isoformat()}".encode()).hexdigest()[:8], 16)
        )
        # Synthetic cloud-like gradient
        h = w = resolution
        x = np.linspace(0, 1, w)
        y = np.linspace(0, 1, h)
        xx, yy = np.meshgrid(x, y)
        base = 0.4 + 0.3 * np.sin(xx * 8) * np.cos(yy * 6)
        noise = rng.random((h, w)) * 0.1
        channel = np.clip(base + noise, 0, 1).astype(np.float32)
        img = np.stack([channel, channel * 0.9, channel * 0.8], axis=-1)
        return ProviderFrame(
            image=img,
            timestamp=timestamp,
            source=self.name,
            layer=layer_id,
            bbox=bbox,
            resolution=resolution,
            url_used=None,
            fallback_used=True,
        )


# ── Local Disk Provider (Offline Mode) ────────────────────────────────────────

class LocalDiskProvider(SatelliteProvider):
    name = "local_disk"
    priority = 1 # Checked first in development

    async def fetch_frame(
        self,
        layer_id: str,
        bbox: list[float],
        timestamp: datetime,
        resolution: int,
        client: httpx.AsyncClient,
    ) -> Optional[ProviderFrame]:
        # Path pattern: data/offline/{layer_id}/YYYYMMDDTHHMMSS.png
        # timestamp format from GIBS: 2026-04-21T16:00:00Z -> 20260421T160000
        ts_slug = timestamp.strftime("%Y%m%dT%H%M%S")
        offline_dir = settings.data_dir / "offline" / layer_id
        
        # Try both .png and .jpg
        for ext in [".png", ".jpg", ".jpeg"]:
            file_path = offline_dir / f"{ts_slug}{ext}"
            if file_path.exists():
                try:
                    with open(file_path, "rb") as f:
                        raw = f.read()
                    img_arr = _decode_image(raw, resolution)
                    if img_arr is not None:
                        logger.info("Serving offline frame from disk", path=str(file_path))
                        return ProviderFrame(
                            image=img_arr,
                            timestamp=timestamp,
                            source=self.name,
                            layer=layer_id,
                            bbox=bbox,
                            resolution=resolution,
                            url_used=str(file_path),
                        )
                except Exception as exc:
                    logger.warning("Failed to load local frame", path=str(file_path), error=str(exc))
        
        return None


# ── Provider registry ─────────────────────────────────────────────────────────

_PROVIDERS: list[SatelliteProvider] = sorted(
    [
        LocalDiskProvider(),
        NASAGIBSProvider(),
        HimawariProvider(),
        INSATProvider(),
        StaticFallbackProvider(),
    ],
    key=lambda p: (p.priority if settings.aether_mode == 'development' else p.priority + 10),
)


def get_provider(name: str) -> Optional[SatelliteProvider]:
    for p in _PROVIDERS:
        if p.name == name:
            return p
    return None


def list_providers() -> list[dict]:
    return [{"name": p.name, "priority": p.priority} for p in _PROVIDERS]


# ── Auto-fallback fetch ────────────────────────────────────────────────────────

async def fetch_with_fallback(
    layer_id: str,
    bbox: list[float],
    timestamp: datetime,
    resolution: int,
    preferred_source: str = "insat",
    job_id: Optional[str] = None,
    max_retries: int = 3,
) -> Optional[ProviderFrame]:
    """Try preferred source first, then fall through the provider chain.

    Each failed attempt is logged and recorded in the audit trail.
    Retries with exponential backoff within each provider.
    """
    from backend.app.services.job_manager import append_audit_event

    # Build ordered list starting with preferred
    ordered = sorted(_PROVIDERS, key=lambda p: (0 if p.name == preferred_source else p.priority))

    async with httpx.AsyncClient(timeout=settings.wms_timeout_seconds) as client:
        for provider in ordered:
            for attempt in range(max_retries):
                try:
                    frame = await provider.fetch_frame(layer_id, bbox, timestamp, resolution, client)
                    if frame is not None:
                        if provider.name != preferred_source:
                            frame.fallback_used = True
                            logger.warning(
                                "Fallback provider used",
                                preferred=preferred_source,
                                actual=provider.name,
                                ts=timestamp.isoformat(),
                            )
                            if job_id:
                                append_audit_event(job_id, "fallback_triggered", {
                                    "preferred": preferred_source,
                                    "actual": provider.name,
                                    "ts": timestamp.isoformat(),
                                    "layer": layer_id,
                                })
                        return frame
                except Exception as exc:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    logger.warning(
                        "Provider fetch attempt failed",
                        provider=provider.name,
                        attempt=attempt + 1,
                        error=str(exc),
                    )

    logger.error("All providers failed", layer=layer_id, ts=timestamp.isoformat())
    return None
