"""AetherGIS - API routes: layers."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from backend.app.api.deps.identity import resolve_current_user_id

from backend.app.models.schemas import DataSource, LayerCapabilities, LayerInfo
from backend.app.services.layer_capabilities import get_layer_capabilities_live
from backend.app.services.layer_catalog import get_layer_catalog
from backend.app.services.wms_client import BHUVAN_LAYERS, GIBS_LAYERS, INSAT_LAYERS
from backend.app.utils.logging import get_logger

router = APIRouter(prefix='/layers', tags=['Layers'])
logger = get_logger(__name__)


@router.get('', response_model=list[LayerInfo])
async def list_layers(
    data_source: DataSource = DataSource.nasa_gibs,
    current_user_id: str = Depends(resolve_current_user_id),
) -> list[LayerInfo]:
    raw = await get_layer_catalog(data_source=data_source.value)
    return [
        LayerInfo(
            layer_id=layer['layer_id'],
            name=layer['name'],
            temporal_resolution_minutes=layer['temporal_resolution_minutes'],
            data_source=data_source,
            use_case=layer['use_case'],
            description=layer.get('description'),
            nadir_lon=layer.get('nadir_lon'),
            coverage_lon_min=layer.get('coverage_lon_min'),
            coverage_lon_max=layer.get('coverage_lon_max'),
            coverage_lat_min=layer.get('coverage_lat_min'),
            coverage_lat_max=layer.get('coverage_lat_max'),
            coverage_note=layer.get('coverage_note'),
            preset_regions=layer.get('preset_regions', {}),
            default_preset=layer.get('default_preset'),
            availability_checked_live=layer.get('availability_checked_live', False),
        )
        for layer in raw
    ]


@router.get('/{layer_id}/capabilities', response_model=LayerCapabilities)
async def get_layer_capabilities(
    layer_id: str,
    current_user_id: str = Depends(resolve_current_user_id),
) -> LayerCapabilities:
    if layer_id not in GIBS_LAYERS and layer_id not in BHUVAN_LAYERS and layer_id not in INSAT_LAYERS:
        raise HTTPException(status_code=404, detail=f'Layer {layer_id!r} not found')

    if layer_id in GIBS_LAYERS:
        info = GIBS_LAYERS[layer_id]
    elif layer_id in BHUVAN_LAYERS:
        info = BHUVAN_LAYERS[layer_id]
    else:
        info = INSAT_LAYERS[layer_id]

    live_caps = await get_layer_capabilities_live(layer_id)
    return LayerCapabilities(
        layer_id=layer_id,
        time_start=live_caps.time_start,
        time_end=live_caps.time_end,
        latest_available_time=live_caps.latest_available_time,
        suggested_time_start=live_caps.suggested_time_start,
        suggested_time_end=live_caps.suggested_time_end,
        step_minutes=live_caps.step_minutes,
        time_source_live=live_caps.time_source_live,
        temporal_resolution_minutes=live_caps.temporal_resolution_minutes,
        min_resolution=256,
        max_resolution=2048,
        bbox=[
            float(info.get('coverage_lon_min', -180.0)),
            float(info.get('coverage_lat_min', -90.0)),
            float(info.get('coverage_lon_max', 180.0)),
            float(info.get('coverage_lat_max', 90.0)),
        ],
        nadir_lon=info.get('nadir_lon'),
        coverage_lon_min=info.get('coverage_lon_min'),
        coverage_lon_max=info.get('coverage_lon_max'),
        coverage_lat_min=info.get('coverage_lat_min'),
        coverage_lat_max=info.get('coverage_lat_max'),
        coverage_note=info.get('coverage_note'),
        default_preset=info.get('default_preset'),
    )

