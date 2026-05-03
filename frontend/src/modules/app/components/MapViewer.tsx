import React, { useEffect, useRef, useState } from 'react';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import ImageLayer from 'ol/layer/Image';
import OSM from 'ol/source/OSM';
import XYZ from 'ol/source/XYZ';
import Static from 'ol/source/ImageStatic';
import { fromLonLat } from 'ol/proj';

import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import Feature, { type FeatureLike } from 'ol/Feature';
import { Polygon } from 'ol/geom';
import { Fill, Stroke, Style, Text } from 'ol/style';
import { boundingExtent, getCenter } from 'ol/extent';

import { useLayerCapabilities } from '@shared/api/client';
import { useStore } from '@app/store/useStore';
import PipelineProgress from './PipelineProgress';
import { applyLayerPreset } from '@shared/utils/layerDefaults';
import { useFramePreloader } from '@shared/hooks/useFramePreloader';

import 'ol/ol.css';
import { Eye, EyeOff, Square, Layers, Info, Plus, Minus, Map as MapIcon, Satellite } from 'lucide-react';

// ─── Styles & Helpers ─────────────────────────────────────────────────────────

function domainStyle(feature: FeatureLike) {
  return new Style({
    stroke: new Stroke({ color: '#00d1b2', width: 2, lineDash: [4, 4] }),
    fill: new Fill({ color: 'rgba(0, 209, 178, 0.15)' }),
    text: new Text({
      text: String(feature.get('label') || ''),
      font: 'bold 11px sans-serif',
      fill: new Fill({ color: '#fff' }),
      stroke: new Stroke({ color: '#000', width: 2 }),
      overflow: true,
    }),
  });
}

function bboxToOlCoords(bbox: [number, number, number, number]) {
  const [minLon, minLat, maxLon, maxLat] = bbox;
  return [
    fromLonLat([minLon, minLat]),
    fromLonLat([minLon, maxLat]),
    fromLonLat([maxLon, maxLat]),
    fromLonLat([maxLon, minLat]),
    fromLonLat([minLon, minLat]),
  ];
}

function clampLon(lon: number) {
  while (lon > 180) lon -= 360;
  while (lon < -180) lon += 360;
  return lon;
}

function fitExtentFromBbox(bbox: [number, number, number, number]) {
  const [minLon, minLat, maxLon, maxLat] = bbox;
  const extent = boundingExtent([fromLonLat([minLon, minLat]), fromLonLat([maxLon, maxLat])]);
  const center = getCenter(extent);
  const span = Math.max(Math.abs(maxLon - minLon), Math.abs(maxLat - minLat));
  const zoom = span > 60 ? 3 : span > 30 ? 4 : span > 15 ? 5 : span > 7 ? 6 : 7;
  return { center, zoom };
}

function aoiStyle() {
  return new Style({
    fill: new Fill({ color: 'rgba(80,180,255,0.06)' }),
    stroke: new Stroke({ color: '#40b4ff', width: 1.25, lineDash: [4, 4] }),
  });
}

function footprintStyle() {
  return new Style({
    fill: new Fill({ color: 'rgba(100,255,180,0.03)' }),
    stroke: new Stroke({ color: 'rgba(100,255,180,0.20)', width: 1, lineDash: [6, 6] }),
  });
}

function getLayerLegendText(layerName: string | undefined): React.ReactNode {
  if (!layerName) return null;
  const lower = layerName.toLowerCase();
  
  if (lower.includes('infrared')) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div style={{ fontWeight: 600, color: '#fff' }}>Thermal Infrared (10.4µm)</div>
        <div style={{ fontSize: 8, color: '#ccc' }}>Brightness Temperature / Cloud Top Altitude</div>
        <div style={{ height: 6, width: '100%', background: 'linear-gradient(to right, #000, #555, #ccc, #fff)', border: '1px solid #333', marginTop: 2 }} />
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 7, color: '#888', marginTop: 1 }}>
          <span>Warm (Surface)</span>
          <span>Cool (Mid-Level)</span>
          <span>Cold (-80°C, Intense Cb)</span>
        </div>
      </div>
    );
  }
  if (lower.includes('visible') || lower.includes('true color')) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div style={{ fontWeight: 600, color: '#fff' }}>Visible Reflectance</div>
        <div style={{ fontSize: 8, color: '#ccc' }}>Daytime Cloud Cover & Surface Albedo</div>
        <div style={{ height: 6, width: '100%', background: 'linear-gradient(to right, #001833, #3b5f00, #e0e0e0, #fff)', border: '1px solid #333', marginTop: 2 }} />
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 7, color: '#888', marginTop: 1 }}>
          <span>Ocean/Dark</span>
          <span>Land</span>
          <span>Deep Convection / Snow</span>
        </div>
      </div>
    );
  }
  return null;
}

// ─── Internal Confirmation Dialog ─────────────────────────────────────────────
function ConfirmDialog({
  title,
  message,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="cdlg-backdrop cdlg-backdrop-map" style={{ zIndex: 4000 }}>
      <div className="cdlg-box cdlg-box-light" style={{ maxWidth: 320 }}>
        <div className="cdlg-header cdlg-header-danger">
          <span className="cdlg-icon" style={{ color: 'var(--red)' }}>⚠</span>
          <span className="cdlg-title">{title}</span>
        </div>
        <div className="cdlg-body cdlg-body-light">{message}</div>
        <div className="cdlg-footer">
          <button className="cdlg-btn cancel" onClick={onCancel}>Cancel</button>
          <button className="cdlg-btn confirm cdlg-btn-danger" onClick={onConfirm}>Discard</button>
        </div>
      </div>
    </div>
  );
}

import MapOverlayManager from './MapOverlayManager';

// ─── Seek-to-frame handler (wired from alerts / consistency panels) ────────────
function SeekHandler() {
  const { pipelineResult, setCurrentFrameIndex, setIsPlaying } = useStore();
  useEffect(() => {
    const handler = (e: Event) => {
      const frameIndex: number = (e as CustomEvent).detail?.frameIndex ?? 0;
      if (!pipelineResult) return;
      const clamped = Math.max(0, Math.min(frameIndex, pipelineResult.frames.length - 1));
      setIsPlaying(false);
      setCurrentFrameIndex(clamped);
    };
    window.addEventListener('aethergis:seekToFrame', handler);
    return () => window.removeEventListener('aethergis:seekToFrame', handler);
  }, [pipelineResult, setCurrentFrameIndex, setIsPlaying]);
  return null;
}

export default function MapViewer() {
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstance = useRef<Map | null>(null);
  const aoiSource = useRef<VectorSource | null>(null);
  const footprintSource = useRef<VectorSource | null>(null);
  const domainSource = useRef<VectorSource | null>(null);
  const overlayLayer = useRef<ImageLayer<Static> | null>(null);
  const baseLayerRef = useRef<TileLayer | null>(null);
  const lastFocusedRunRef = useRef<string | null>(null);

  // Base layer state: 'osm' | 'satellite'
  const [baseLayerType, setBaseLayerType] = useState<'osm' | 'satellite'>('osm');

  const {
    bbox,
    setBbox,
    layers,
    selectedLayer,
    selectedPresetKey,
    setSelectedPresetKey,
    pipelineResult,
    currentFrameIndex,
    playbackSpeed,
    showMetadataOverlay,
    setShowMetadataOverlay,
  } = useStore();
  const { data: layerCapabilities } = useLayerCapabilities(selectedLayer);

  const [showAoi, setShowAoi] = React.useState(true);
  const [showFootprint, setShowFootprint] = React.useState(true);
  const [showHUD, setShowHUD] = React.useState(true);
  const [confirmDiscard, setConfirmDiscard] = React.useState(false);

  // Pre-warm browser image cache for upcoming frames (blob URL based — zero network latency)
  const { getFrameUrl } = useFramePreloader(
    pipelineResult?.job_id ?? null,
    pipelineResult?.frames.length ?? 0,
    currentFrameIndex,
    playbackSpeed,
    pipelineResult?.status,
  );

  const selectedLayerInfo = layers.find((layer) => layer.layer_id === selectedLayer) ?? null;

  useEffect(() => {
    if (!mapRef.current || mapInstance.current) return;

    const footprintSrc = new VectorSource();
    footprintSource.current = footprintSrc;
    const footprintLayer = new VectorLayer({ source: footprintSrc, style: footprintStyle(), zIndex: 1, properties: { type: 'footprint' } });

    const presetSrc = new VectorSource();
    domainSource.current = presetSrc;
    const presetLayer = new VectorLayer({ source: presetSrc, style: domainStyle, zIndex: 2, properties: { name: 'domain-layer' } });

    const aoiSrc = new VectorSource();
    aoiSource.current = aoiSrc;
    const aoiLayer = new VectorLayer({ source: aoiSrc, style: aoiStyle(), zIndex: 3, properties: { type: 'aoi' } });

    const pipelineLayer = new ImageLayer<Static>({ opacity: 0.95, zIndex: 4 });
    overlayLayer.current = pipelineLayer;

    // Create base layer based on current selection
    const baseLayer = new TileLayer({
      source: baseLayerType === 'satellite' 
        ? new XYZ({
            // NASA GIBS - Blue Marble Next Generation (free, no API key)
            url: 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default//GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg',
            maxZoom: 8,
            attributions: 'NASA GIBS / Blue Marble'
          })
        : new OSM()
    });
    baseLayerRef.current = baseLayer;

    const map = new Map({
      target: mapRef.current,
      layers: [baseLayer, footprintLayer, presetLayer, pipelineLayer, aoiLayer],
      view: new View({ center: fromLonLat([80, 15]), zoom: 5, minZoom: 2, maxZoom: 14 }),
      controls: [], // Disable ALL default controls to prevent duplication/clutter
    });

    map.on('singleclick', (evt) => {
      map.forEachFeatureAtPixel(evt.pixel, (feature) => {
        if (!feature.get('isPreset')) return false;
        const presetKey = String(feature.get('presetKey'));
        const { layers, selectedLayer } = useStore.getState();
        const layer = layers.find((item) => item.layer_id === selectedLayer) ?? null;
        applyLayerPreset(layer, presetKey, {
          setSelectedPresetKey: useStore.getState().setSelectedPresetKey,
          setBbox: useStore.getState().setBbox,
          setTimeStart: useStore.getState().setTimeStart,
          setTimeEnd: useStore.getState().setTimeEnd,
        }, layerCapabilities ?? undefined);
        return true;
      });
    });

    map.on('pointermove', (evt) => {
      const hit = map.hasFeatureAtPixel(evt.pixel, {
        layerFilter: (layer) => layer.get('name') === 'domain-layer',
      });
      map.getTargetElement().style.cursor = hit ? 'pointer' : '';
    });

    mapInstance.current = map;

    // Notify MapOverlayManager that the map DOM container is available
    setTimeout(() => {
      window.dispatchEvent(new CustomEvent('aethergis:mapReady', { detail: { mapEl: mapRef.current } }));
    }, 100);

    return () => {
      map.setTarget(undefined);
      mapInstance.current = null;
    };
  }, [layerCapabilities, baseLayerType]);

  // Toggle Layer Visibility
  useEffect(() => {
    if (!mapInstance.current) return;
    mapInstance.current.getLayers().forEach((layer) => {
      if (layer instanceof VectorLayer) {
        if (layer.get('type') === 'aoi') layer.setVisible(showAoi);
        if (layer.get('type') === 'footprint') layer.setVisible(showFootprint);
      }
    });
  }, [showAoi, showFootprint]);

  // Switch base layer when type changes
  useEffect(() => {
    if (!mapInstance.current || !baseLayerRef.current) return;
    
    const baseLayer = baseLayerRef.current;
    const newSource = baseLayerType === 'satellite'
      ? new XYZ({
          // NASA GIBS - Blue Marble Next Generation (free, no API key)
          url: 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default//GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg',
          maxZoom: 8,
          attributions: 'NASA GIBS / Blue Marble'
        })
      : new OSM();
    
    baseLayer.setSource(newSource);
  }, [baseLayerType]);

  useEffect(() => {
    if (!aoiSource.current || !mapInstance.current) return;
    aoiSource.current.clear();
    if (!bbox) return;

    aoiSource.current.addFeature(new Feature(new Polygon([bboxToOlCoords(bbox)])));
    const { center, zoom } = fitExtentFromBbox(bbox);
    mapInstance.current.getView().animate({ center, zoom, duration: 600 });
  }, [bbox]);

  useEffect(() => {
    if (!footprintSource.current || !domainSource.current) return;

    footprintSource.current.clear();
    domainSource.current.clear();
    if (!selectedLayerInfo) return;

    const lonMin = selectedLayerInfo.coverage_lon_min;
    const lonMax = selectedLayerInfo.coverage_lon_max;
    const latMin = selectedLayerInfo.coverage_lat_min ?? -60;
    const latMax = selectedLayerInfo.coverage_lat_max ?? 60;

    if (lonMin != null && lonMax != null) {
      const effectiveLonMax = lonMax > 180 ? 180 : lonMax;
      const ring = [
        fromLonLat([clampLon(lonMin), latMin]),
        fromLonLat([clampLon(lonMin), latMax]),
        fromLonLat([clampLon(effectiveLonMax), latMax]),
        fromLonLat([clampLon(effectiveLonMax), latMin]),
        fromLonLat([clampLon(lonMin), latMin]),
      ];
      footprintSource.current.addFeature(new Feature(new Polygon([ring])));
    }

    if (selectedLayerInfo.preset_regions) {
      Object.entries(selectedLayerInfo.preset_regions).forEach(([key, preset]) => {
        const region = preset as { bbox: [number, number, number, number]; label: string };
        const ring = bboxToOlCoords(region.bbox);
        const feature = new Feature(new Polygon([ring]));
        feature.set('isPreset', true);
        feature.set('presetKey', key);
        feature.set('label', region.label);
        domainSource.current?.addFeature(feature);
      });
    }
  }, [selectedLayerInfo]);

  useEffect(() => {
    if (!mapInstance.current || !pipelineResult?.bbox || !pipelineResult?.job_id) return;
    if (lastFocusedRunRef.current === pipelineResult.job_id) return;

    const { center, zoom } = fitExtentFromBbox(pipelineResult.bbox);
    mapInstance.current.getView().animate({ center, zoom, duration: 700 });
    lastFocusedRunRef.current = pipelineResult.job_id;
  }, [pipelineResult?.job_id, pipelineResult?.bbox]);

  useEffect(() => {
    if (!overlayLayer.current || !pipelineResult || pipelineResult.frames.length === 0 || !pipelineResult.bbox) {
      overlayLayer.current?.setSource(null);
      return;
    }

    const frame = pipelineResult.frames[currentFrameIndex];
    if (!frame) return;

    const resultBbox = pipelineResult.bbox as [number, number, number, number];
    // Use blob URL from preloader cache (zero-latency) or fall back to API URL
    const frameUrl = getFrameUrl(frame.frame_index);

    overlayLayer.current.setSource(new Static({
      url: frameUrl,
      imageExtent: resultBbox,
      projection: 'EPSG:4326',
    }));
  }, [pipelineResult, currentFrameIndex, getFrameUrl]);

  const handleZoom = (delta: number) => {
    if (!mapInstance.current) return;
    const view = mapInstance.current.getView();
    const current = view.getZoom() || 5;
    view.animate({ zoom: current + delta, duration: 250 });
  };

  const handleDiscardAOI = () => {
    setSelectedPresetKey(null);
    setBbox(null);
    aoiSource.current?.clear();
    setConfirmDiscard(false);
  };

  const currentFrame = pipelineResult?.frames[currentFrameIndex];
  const layerLegendText = getLayerLegendText(selectedLayerInfo?.name);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden' }}>
      <SeekHandler />
      <MapOverlayManager />
      <div ref={mapRef} className="ol-map" style={{ width: '100%', height: '100%' }} />

      {/* TOP RIGHT: COMMAND RAIL (Visibility & Layers) */}
      <div className="command-dock">
        <div className="cd-group">
          <button className={`cd-btn ${showHUD ? 'active' : ''}`} onClick={() => setShowHUD(!showHUD)} title="Shift HUD Data Display">
            <Info />
          </button>
          <button className={`cd-btn ${showAoi ? 'active' : ''}`} onClick={() => setShowAoi(!showAoi)} title="Toggle AOI Geometric Overlay">
            <Square />
          </button>
          <button className={`cd-btn ${showFootprint ? 'active' : ''}`} onClick={() => setShowFootprint(!showFootprint)} title="Toggle Sensor Coverage Mask">
            <Layers />
          </button>
          <button className={`cd-btn ${showMetadataOverlay ? 'active' : ''}`} onClick={() => setShowMetadataOverlay(!showMetadataOverlay)} title="Toggle Telemetry Text Overlay">
            {showMetadataOverlay ? <Eye /> : <EyeOff />}
          </button>
        </div>
        
        {/* Base Layer Switcher */}
        <div className="cd-group" style={{ marginTop: '12px' }}>
          <button 
            className={`cd-btn ${baseLayerType === 'osm' ? 'active' : ''}`} 
            onClick={() => setBaseLayerType('osm')} 
            title="Street Map (OpenStreetMap)"
          >
            <MapIcon size={16} />
          </button>
          <button 
            className={`cd-btn ${baseLayerType === 'satellite' ? 'active' : ''}`} 
            onClick={() => setBaseLayerType('satellite')} 
            title="Satellite Imagery (Esri)"
          >
            <Satellite size={16} />
          </button>
        </div>
      </div>

      {/* RIGHT MIDDLE: NAVIGATION RAIL (Zoom / Arrows) */}
      <div className="navigation-dock">
        <div className="cd-group">
          <button className="cd-btn" onClick={() => handleZoom(1)} title="Zoom In Telemetry">
            <Plus />
          </button>
          <button className="cd-btn" onClick={() => handleZoom(-1)} title="Zoom Out Telemetry">
            <Minus />
          </button>
        </div>
      </div>

      {/* SCIENTIFIC HUD: TOP LEFT */}
      {showHUD && (
        <div className="map-hud">
          {bbox && selectedLayerInfo && (
            <div className="hud-panel">
              <div className="hud-title">{selectedLayerInfo.name}</div>
              <div className="hud-subtitle">{selectedPresetKey ? `Domain: ${selectedPresetKey}` : 'Custom AOI (Manual)'}</div>
            </div>
          )}

          {bbox && layerLegendText && (
            <div className="hud-panel hud-panel-legend">
              {layerLegendText}
            </div>
          )}

        </div>
      )}

      {showMetadataOverlay && currentFrame && (
        <div className="map-overlay map-overlay-bottom-left">
          <div className="mo-row"><span className="mo-key">Frame Capture</span><span className="mo-val">Index {currentFrameIndex + 1}/{pipelineResult!.frames.length}</span></div>
          <div className="mo-row"><span className="mo-key">Timestamp (Z)</span><span className="mo-val">{new Date(currentFrame.timestamp).toISOString().slice(0, 16).replace('T', ' ')}</span></div>
          <div className="mo-row"><span className="mo-key">Data Source</span><span className={`mo-val ${currentFrame.is_interpolated ? 'warn' : 'ok'}`}>{currentFrame.is_interpolated ? 'AI Reconstruction' : 'Observed Satellite'}</span></div>
          <div className="mo-row"><span className="mo-key">Resolution</span><span className="mo-val">{currentFrame.is_interpolated ? 'High (Temporal)' : 'Base'}</span></div>
        </div>
      )}

      {!bbox && (
        <div className="map-empty-state">
          <div className="map-empty-title">AetherGIS Core Ready</div>
          <div className="map-empty-subtitle">Awaiting AOI selection to initialize telemetry stream</div>
        </div>
      )}

      {/* BOTTOM: Status Bar */}
      {bbox && (
        <div className="map-status-strip">
          <span className="map-status-label">Sensor Viewport</span>
          <span style={{ color: 'rgba(255,255,255,0.4)', borderLeft: '1px solid #222', paddingLeft: 16 }}>LON: {bbox[0].toFixed(4)}° – {bbox[2].toFixed(4)}°E</span>
          <span style={{ color: 'rgba(255,255,255,0.4)', marginLeft: 20 }}>LAT: {bbox[1].toFixed(4)}° – {bbox[3].toFixed(4)}°N</span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 16, alignItems: 'center' }}>
            <span style={{ color: '#444', fontSize: 7, textTransform: 'uppercase' }}>
              {baseLayerType === 'satellite' ? '© NASA GIBS / Blue Marble' : '© OpenStreetMap'}
            </span>
            <span style={{ color: '#555' }}>PROJ: EPSG:3857</span>
            <span style={{ color: 'rgba(255,80,80,0.9)', cursor: 'pointer', fontFamily: 'var(--cond)', fontWeight: 700, letterSpacing: '0.04em' }} onClick={() => setConfirmDiscard(true)}>[ DISCARD AOI ]</span>
          </div>
        </div>
      )}

      {confirmDiscard && (
        <ConfirmDialog
          title="Discard AOI Geometry"
          message="Are you sure you want to clear the current Area of Interest? This will reset the telemetry stream view."
          onConfirm={handleDiscardAOI}
          onCancel={() => setConfirmDiscard(false)}
        />
      )}

      <PipelineProgress />
    </div>
  );
}
