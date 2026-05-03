from __future__ import annotations

import time
import defusedxml.ElementTree as ET
from typing import Any

import httpx

from backend.app.config import get_settings
from backend.app.services.wms_client import BHUVAN_LAYERS, GIBS_LAYERS, INSAT_LAYERS
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

_CACHE_TTL_SECONDS = 30 * 60
_capabilities_cache: dict[str, tuple[float, set[str]]] = {}


def _find_text(element: ET.Element, local_name: str) -> str | None:
    for child in element:
        if child.tag.endswith(local_name) and child.text:
            return child.text.strip()
    return None


async def fetch_live_gibs_layer_ids() -> set[str]:
    cached = _capabilities_cache.get('nasa_gibs')
    now = time.time()
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    params = {
        'SERVICE': 'WMS',
        'REQUEST': 'GetCapabilities',
        'VERSION': '1.1.1',
    }

    async with httpx.AsyncClient(timeout=settings.wms_timeout_seconds, follow_redirects=True) as client:
        response = await client.get(settings.nasa_gibs_base_url, params=params)
        response.raise_for_status()
        root = ET.fromstring(response.text)

    live_ids: set[str] = set()
    for layer in root.iter():
        if not layer.tag.endswith('Layer'):
            continue
        name = _find_text(layer, 'Name')
        if name:
            live_ids.add(name)

    _capabilities_cache['nasa_gibs'] = (now, live_ids)
    return live_ids


async def fetch_live_mosdac_layer_ids() -> set[str]:
    cached = _capabilities_cache.get('insat')
    now = time.time()
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    params: dict[str, str] = {
        'SERVICE': 'WMS',
        'REQUEST': 'GetCapabilities',
        'VERSION': '1.1.1',
    }
    if settings.mosdac_api_key:
        params['key'] = settings.mosdac_api_key

    async with httpx.AsyncClient(timeout=settings.wms_timeout_seconds, follow_redirects=True) as client:
        response = await client.get(settings.mosdac_wms_url, params=params)
        response.raise_for_status()
        root = ET.fromstring(response.text)

    live_ids: set[str] = set()
    for layer in root.iter():
        if not layer.tag.endswith('Layer'):
            continue
        name = _find_text(layer, 'Name')
        if name:
            live_ids.add(name)

    _capabilities_cache['insat'] = (now, live_ids)
    return live_ids


async def get_layer_catalog(data_source: str | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    if not data_source or data_source == 'nasa_gibs':
        try:
            live_ids = await fetch_live_gibs_layer_ids()
            curated_nasa = [
                {
                    'layer_id': layer_id,
                    'data_source': 'nasa_gibs',
                    'crs': 'EPSG:4326',
                    'availability_checked_live': True,
                    **meta,
                }
                for layer_id, meta in GIBS_LAYERS.items()
                if layer_id in live_ids
            ]

            missing = [layer_id for layer_id in GIBS_LAYERS if layer_id not in live_ids]
            if missing:
                logger.warning('Some curated NASA layers are not present in live GIBS capabilities', missing_layers=missing)

            records.extend(curated_nasa)
        except Exception as exc:
            logger.warning('Falling back to curated NASA layer registry because live capabilities fetch failed', error=str(exc))
            records.extend([
                {
                    'layer_id': layer_id,
                    'data_source': 'nasa_gibs',
                    'crs': 'EPSG:4326',
                    'availability_checked_live': False,
                    **meta,
                }
                for layer_id, meta in GIBS_LAYERS.items()
            ])

    if not data_source or data_source == 'isro_bhuvan':
        records.extend([
            {
                'layer_id': layer_id,
                'data_source': 'isro_bhuvan',
                'crs': 'EPSG:4326',
                'availability_checked_live': False,
                **meta,
            }
            for layer_id, meta in BHUVAN_LAYERS.items()
        ])

    if not data_source or data_source == 'insat':
        try:
            live_ids = await fetch_live_mosdac_layer_ids()
            curated_insat = [
                {
                    'layer_id': layer_id,
                    'data_source': 'insat',
                    'crs': 'EPSG:4326',
                    'availability_checked_live': True,
                    **meta,
                }
                for layer_id, meta in INSAT_LAYERS.items()
                if layer_id in live_ids
            ]

            missing = [layer_id for layer_id in INSAT_LAYERS if layer_id not in live_ids]
            if missing:
                logger.warning('Some curated INSAT layers are not present in live MOSDAC capabilities', missing_layers=missing)

            records.extend(curated_insat)
        except Exception as exc:
            logger.warning('Falling back to curated INSAT layer registry because live capabilities fetch failed', error=str(exc))
            records.extend([
                {
                    'layer_id': layer_id,
                    'data_source': 'insat',
                    'crs': 'EPSG:4326',
                    'availability_checked_live': False,
                    **meta,
                }
                for layer_id, meta in INSAT_LAYERS.items()
            ])

    return records
