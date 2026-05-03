from __future__ import annotations

import re
import time
import defusedxml.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from backend.app.config import get_settings
from backend.app.services.wms_client import BHUVAN_LAYERS, GIBS_LAYERS, INSAT_LAYERS
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

_CAPS_TTL_SECONDS = 30 * 60
_WMS_XML_CACHE: tuple[float, ET.Element] | None = None
_MOSDAC_XML_CACHE: tuple[float, ET.Element] | None = None
_DESCRIBE_DOMAINS_CACHE: dict[str, tuple[float, ET.Element]] = {}
_LAYER_CACHE: dict[str, tuple[float, 'ParsedLayerCapabilities']] = {}

# INSAT-3D launched Feb 2014; INSAT-3DR launched Sep 2016
_INSAT3D_EPOCH  = datetime(2014, 2, 15, tzinfo=timezone.utc)
_INSAT3DR_EPOCH = datetime(2016, 9, 28, tzinfo=timezone.utc)

_DURATION_RE = re.compile(
    r'^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$'
)


@dataclass
class ParsedLayerCapabilities:
    layer_id: str
    time_start: Optional[datetime]
    time_end: Optional[datetime]
    latest_available_time: Optional[datetime]
    suggested_time_start: Optional[datetime]
    suggested_time_end: Optional[datetime]
    step_minutes: Optional[int]
    temporal_resolution_minutes: float
    time_source_live: bool


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    value = value.strip()
    if not value:
        return None
    if len(value) == 10:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso_duration_to_minutes(value: str) -> Optional[int]:
    match = _DURATION_RE.match(value.strip())
    if not match:
        return None
    parts = {key: int(number or 0) for key, number in match.groupdict().items()}
    total_seconds = (
        parts['days'] * 86400
        + parts['hours'] * 3600
        + parts['minutes'] * 60
        + parts['seconds']
    )
    return max(1, round(total_seconds / 60)) if total_seconds else None


def _find_direct_child_text(element: ET.Element, local_name: str) -> Optional[str]:
    for child in element:
        if child.tag.endswith(local_name) and child.text:
            return child.text.strip()
    return None


async def _get_wms_capabilities_root() -> ET.Element:
    global _WMS_XML_CACHE
    now = time.time()
    if _WMS_XML_CACHE and now - _WMS_XML_CACHE[0] < _CAPS_TTL_SECONDS:
        return _WMS_XML_CACHE[1]

    params = {'SERVICE': 'WMS', 'REQUEST': 'GetCapabilities', 'VERSION': '1.1.1'}
    async with httpx.AsyncClient(timeout=settings.wms_timeout_seconds, follow_redirects=True) as client:
        response = await client.get(settings.nasa_gibs_base_url, params=params)
        response.raise_for_status()
        root = ET.fromstring(response.text)

    _WMS_XML_CACHE = (now, root)
    return root


async def _get_mosdac_capabilities_root() -> ET.Element:
    """
    Fetch and cache MOSDAC WMS GetCapabilities XML.

    MOSDAC endpoint: https://mosdac.gov.in/live/wms?SERVICE=WMS&REQUEST=GetCapabilities
    Each INSAT-3D/3DR layer advertises its time extent in a <Extent name="time">
    or <Dimension name="time"> element, parsed exactly the same way as GIBS.

    Cache TTL mirrors the GIBS TTL (30 min) — MOSDAC updates composites every 30 min.
    """
    global _MOSDAC_XML_CACHE
    now = time.time()
    if _MOSDAC_XML_CACHE and now - _MOSDAC_XML_CACHE[0] < _CAPS_TTL_SECONDS:
        return _MOSDAC_XML_CACHE[1]

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

    _MOSDAC_XML_CACHE = (now, root)
    return root


async def _find_mosdac_layer_element(layer_id: str) -> Optional[ET.Element]:
    """Find a named Layer element inside the MOSDAC GetCapabilities XML."""
    root = await _get_mosdac_capabilities_root()
    for layer in root.iter():
        if not layer.tag.endswith('Layer'):
            continue
        name = _find_direct_child_text(layer, 'Name')
        if name == layer_id:
            return layer
    return None


async def _find_nasa_layer_element(layer_id: str) -> Optional[ET.Element]:
    root = await _get_wms_capabilities_root()
    for layer in root.iter():
        if not layer.tag.endswith('Layer'):
            continue
        name = _find_direct_child_text(layer, 'Name')
        if name == layer_id:
            return layer
    return None


async def _get_describe_domains_root(layer_id: str) -> ET.Element:
    cached = _DESCRIBE_DOMAINS_CACHE.get(layer_id)
    now = time.time()
    if cached and now - cached[0] < _CAPS_TTL_SECONDS:
        return cached[1]

    params = {
        'SERVICE': 'WMTS',
        'REQUEST': 'DescribeDomains',
        'VERSION': '1.0.0',
        'LAYER': layer_id,
        'TILEMATRIXSET': '250m',
    }
    async with httpx.AsyncClient(timeout=settings.wms_timeout_seconds, follow_redirects=True) as client:
        response = await client.get(settings.nasa_gibs_wmts_url, params=params)
        response.raise_for_status()
        root = ET.fromstring(response.text)

    _DESCRIBE_DOMAINS_CACHE[layer_id] = (now, root)
    return root


def _extract_time_extent_text(layer: ET.Element) -> Optional[str]:
    for child in layer:
        if child.tag.endswith('Extent') or child.tag.endswith('Dimension'):
            name = child.attrib.get('name') or child.attrib.get('Name')
            if name and name.lower() == 'time' and child.text:
                return child.text.strip()
    return None


def _extract_describe_domains_text(root: ET.Element) -> Optional[str]:
    for element in root.iter():
        if element.tag.endswith('Domain') or element.tag.endswith('DimensionDomain'):
            name = element.attrib.get('name') or element.attrib.get('Identifier') or element.attrib.get('dimension')
            if name and 'time' not in name.lower():
                continue
            values = element.attrib.get('default')
            if values:
                return values.strip()
            if element.text and element.text.strip():
                return element.text.strip()
        if element.tag.endswith('Value') and element.text and element.text.strip():
            return element.text.strip()
    return None


def _round_down(dt: datetime, step_minutes: int) -> datetime:
    step_seconds = step_minutes * 60
    epoch = int(dt.timestamp())
    rounded = epoch - (epoch % step_seconds)
    return datetime.fromtimestamp(rounded, tz=timezone.utc)


def _suggest_window(end: datetime, step_minutes: int) -> tuple[datetime, datetime]:
    if step_minutes >= 180:
        suggested_end = end.replace(hour=0, minute=0, second=0, microsecond=0)
        suggested_start = suggested_end - timedelta(days=2)
        return suggested_start, suggested_end

    span_minutes = max(step_minutes * 6, 120)
    suggested_end = _round_down(end, step_minutes)
    suggested_start = suggested_end - timedelta(minutes=span_minutes)
    return suggested_start, suggested_end


def _parse_time_extent(extent_text: str, fallback_step_minutes: int) -> tuple[Optional[datetime], Optional[datetime], Optional[int]]:
    extent_text = extent_text.strip()
    if not extent_text:
        return None, None, fallback_step_minutes

    parts_to_parse = [item.strip() for item in extent_text.split(',') if item.strip()]
    all_starts: list[datetime] = []
    all_ends: list[datetime] = []
    all_steps: list[int] = []

    for part in parts_to_parse:
        if '/' in part:
            interval_parts = part.split('/')
            start = _parse_iso_datetime(interval_parts[0])
            end = _parse_iso_datetime(interval_parts[1])
            step = _parse_iso_duration_to_minutes(interval_parts[2]) if len(interval_parts) >= 3 else None
            
            if start: all_starts.append(start)
            if end: all_ends.append(end)
            if step: all_steps.append(step)
        else:
            dt = _parse_iso_datetime(part)
            if dt:
                all_starts.append(dt)
                all_ends.append(dt)

    if not all_starts or not all_ends:
        return None, None, fallback_step_minutes

    # Take the global min/max across all disjoint intervals
    final_start = min(all_starts)
    final_end = max(all_ends)
    
    # Take the step from the last interval (usually the most relevant) or fallback
    final_step = all_steps[-1] if all_steps else None
    
    # If no step found but we have multiple points, estimate it
    if final_step is None and len(all_starts) > 1:
        # Estimate from the last two points if they were discrete
        delta = (all_ends[-1] - all_starts[-1]).total_seconds()
        if delta > 0:
             final_step = max(1, round(delta / 60))

    return final_start, final_end, final_step or fallback_step_minutes


def _prefer_more_precise(
    describe_domains: tuple[Optional[datetime], Optional[datetime], Optional[int]],
    wms_caps: tuple[Optional[datetime], Optional[datetime], Optional[int]],
) -> tuple[Optional[datetime], Optional[datetime], Optional[int], bool]:
    dd_start, dd_end, dd_step = describe_domains
    wms_start, wms_end, wms_step = wms_caps

    if dd_end is not None:
        return dd_start or wms_start, dd_end, dd_step or wms_step, True
    return wms_start, wms_end, wms_step, False


def _insat_static_fallback(layer_id: str, info: dict) -> ParsedLayerCapabilities:
    """
    Return a static-epoch capability record for INSAT layers when MOSDAC
    is unreachable.  Uses known launch dates:
        INSAT-3D  → 2014-02-15
        INSAT-3DR → 2016-09-28
    The 'latest available' time is the most recent :00/:30 UTC slot, which
    is the correct format for MOSDAC GetMap TIME= requests.
    """
    step_minutes = 30  # INSAT-3D and 3DR both produce 30-min composites
    now_utc = datetime.now(timezone.utc)
    # Round down to most recent 30-min slot
    slot_minute = 0 if now_utc.minute < 30 else 30
    latest = now_utc.replace(minute=slot_minute, second=0, microsecond=0) - timedelta(minutes=30)

    epoch = _INSAT3DR_EPOCH if layer_id.startswith('INSAT3DR') else _INSAT3D_EPOCH
    suggested_start, suggested_end = _suggest_window(latest, step_minutes)

    return ParsedLayerCapabilities(
        layer_id=layer_id,
        time_start=epoch,
        time_end=latest,
        latest_available_time=latest,
        suggested_time_start=suggested_start,
        suggested_time_end=suggested_end,
        step_minutes=step_minutes,
        temporal_resolution_minutes=float(step_minutes),
        time_source_live=False,
    )


async def get_layer_capabilities_live(layer_id: str) -> ParsedLayerCapabilities:
    cached = _LAYER_CACHE.get(layer_id)
    now = time.time()
    if cached and now - cached[0] < _CAPS_TTL_SECONDS:
        return cached[1]

    # ── NASA GIBS layers ──────────────────────────────────────────────────────
    if layer_id in GIBS_LAYERS:
        info = GIBS_LAYERS[layer_id]
        try:
            layer = await _find_nasa_layer_element(layer_id)
            if layer is None:
                raise ValueError(f'Layer {layer_id} not present in live NASA capabilities')

            fallback_step = max(1, round(info['temporal_resolution_minutes']))
            wms_extent_text = _extract_time_extent_text(layer)
            wms_caps = _parse_time_extent(wms_extent_text or '', fallback_step)

            describe_domains_caps = (None, None, fallback_step)
            describe_domains_live = False
            try:
                describe_root = await _get_describe_domains_root(layer_id)
                describe_text = _extract_describe_domains_text(describe_root)
                if describe_text:
                    describe_domains_caps = _parse_time_extent(describe_text, fallback_step)
                    describe_domains_live = True
            except Exception as describe_exc:
                logger.info('DescribeDomains unavailable; falling back to WMS timing metadata', layer_id=layer_id, error=str(describe_exc))

            time_start, time_end, step_minutes, used_describe_domains = _prefer_more_precise(describe_domains_caps, wms_caps)
            latest_available_time = time_end
            suggested_time_start = None
            suggested_time_end = None
            if latest_available_time and step_minutes:
                suggested_time_start, suggested_time_end = _suggest_window(latest_available_time, step_minutes)

            parsed = ParsedLayerCapabilities(
                layer_id=layer_id,
                time_start=time_start,
                time_end=time_end,
                latest_available_time=latest_available_time,
                suggested_time_start=suggested_time_start,
                suggested_time_end=suggested_time_end,
                step_minutes=step_minutes,
                temporal_resolution_minutes=float(step_minutes or info['temporal_resolution_minutes']),
                time_source_live=used_describe_domains or describe_domains_live or (time_end is not None),
            )
            _LAYER_CACHE[layer_id] = (now, parsed)
            return parsed
        except Exception as exc:
            logger.warning('Live NASA GIBS time parsing failed; falling back to curated timing', layer_id=layer_id, error=str(exc))
            fallback_step = max(1, round(info['temporal_resolution_minutes']))
            fallback_end = _round_down(datetime.now(timezone.utc) - timedelta(minutes=max(fallback_step * 3, 30)), fallback_step)
            fallback_start, fallback_end = _suggest_window(fallback_end, fallback_step)
            parsed = ParsedLayerCapabilities(
                layer_id=layer_id,
                time_start=datetime(2000, 1, 1, tzinfo=timezone.utc),
                time_end=fallback_end,
                latest_available_time=fallback_end,
                suggested_time_start=fallback_start,
                suggested_time_end=fallback_end,
                step_minutes=fallback_step,
                temporal_resolution_minutes=float(info['temporal_resolution_minutes']),
                time_source_live=False,
            )
            _LAYER_CACHE[layer_id] = (now, parsed)
            return parsed

    # ── ISRO Bhuvan layers ────────────────────────────────────────────────────
    if layer_id in BHUVAN_LAYERS:
        info = BHUVAN_LAYERS[layer_id]
        step_minutes = max(1, round(info['temporal_resolution_minutes']))
        end = datetime.now(timezone.utc)
        start, end = _suggest_window(end, step_minutes)
        parsed = ParsedLayerCapabilities(
            layer_id=layer_id,
            time_start=datetime(2000, 1, 1, tzinfo=timezone.utc),
            time_end=end,
            latest_available_time=end,
            suggested_time_start=start,
            suggested_time_end=end,
            step_minutes=step_minutes,
            temporal_resolution_minutes=float(info['temporal_resolution_minutes']),
            time_source_live=False,
        )
        _LAYER_CACHE[layer_id] = (now, parsed)
        return parsed

    # ── INSAT / MOSDAC layers ─────────────────────────────────────────────────
    if layer_id in INSAT_LAYERS:
        info = INSAT_LAYERS[layer_id]
        try:
            layer_el = await _find_mosdac_layer_element(layer_id)

            if layer_el is not None:
                # MOSDAC responded — parse its time extent
                extent_text = _extract_time_extent_text(layer_el)
                time_start, time_end, step_minutes = _parse_time_extent(extent_text or '', 30)

                # Clamp start to known launch epoch
                epoch = _INSAT3DR_EPOCH if layer_id.startswith('INSAT3DR') else _INSAT3D_EPOCH
                if time_start is None or time_start < epoch:
                    time_start = epoch

                latest = time_end
                if latest is None:
                    # Capabilities present but no time extent — fall back to
                    # the most recent :00/:30 slot
                    now_utc = datetime.now(timezone.utc)
                    slot_minute = 0 if now_utc.minute < 30 else 30
                    latest = now_utc.replace(minute=slot_minute, second=0, microsecond=0) - timedelta(minutes=30)
                    time_end = latest

                step_minutes = step_minutes or 30
                suggested_start, suggested_end = _suggest_window(latest, step_minutes)

                parsed = ParsedLayerCapabilities(
                    layer_id=layer_id,
                    time_start=time_start,
                    time_end=time_end,
                    latest_available_time=latest,
                    suggested_time_start=suggested_start,
                    suggested_time_end=suggested_end,
                    step_minutes=step_minutes,
                    temporal_resolution_minutes=float(step_minutes),
                    time_source_live=True,
                )
            else:
                # Layer not found in MOSDAC capabilities — use known-good
                # static epoch with a warning
                logger.warning(
                    'INSAT layer not found in MOSDAC GetCapabilities; using static fallback',
                    layer_id=layer_id,
                    mosdac_url=settings.mosdac_wms_url,
                )
                parsed = _insat_static_fallback(layer_id, info)

            _LAYER_CACHE[layer_id] = (now, parsed)
            return parsed

        except Exception as exc:
            logger.warning(
                'MOSDAC GetCapabilities failed; using static launch-date fallback for INSAT layer',
                layer_id=layer_id,
                mosdac_url=settings.mosdac_wms_url,
                error=str(exc),
            )
            parsed = _insat_static_fallback(layer_id, info)
            _LAYER_CACHE[layer_id] = (now, parsed)
            return parsed

    raise KeyError(layer_id)
