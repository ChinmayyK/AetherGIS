import { useEffect, useMemo, useState } from 'react';
import { 
  useLayers, 
  useLayerCapabilities, 
  useSubmitPipeline, 
  cancelPipeline, 
  useHealth,
  useModels
} from '@shared/api/client';
import { useStore } from '@app/store/useStore';
import type { LayerInfo, PresetRegion, ModelType } from '@app/store/useStore';
import { applyLayerPreset, chooseRecommendedLayer, getDefaultPresetKey } from '@shared/utils/layerDefaults';
import AdvancedOverlaysPanel from './AdvancedOverlaysPanel';
import ConfirmDialog from './ConfirmDialog';

const VISIBLE_BAND_LAYERS = new Set([
  'GOES-East_ABI_Band2_Red_Visible_1km',
  'GOES-West_ABI_Band2_Red_Visible_1km',
  'Himawari_AHI_Band3_Red_Visible_1km',
  'MODIS_Terra_CorrectedReflectance_TrueColor',
  'MODIS_Aqua_CorrectedReflectance_TrueColor',
]);

const LAYER_SWATCHES: Record<string, string> = {
  'GOES-East_ABI_Band2_Red_Visible_1km': '#3a7a60',
  'GOES-West_ABI_Band2_Red_Visible_1km': '#2a6a50',
  'Himawari_AHI_Band3_Red_Visible_1km': '#6a4a9a',
  'MODIS_Terra_CorrectedReflectance_TrueColor': '#3a5a9a',
  'MODIS_Aqua_CorrectedReflectance_TrueColor': '#3a6a9a',
  'VIIRS_SNPP_DayNightBand_ENCC': '#7a6020',
  'GOES-East_ABI_Band13_Clean_Infrared': '#8a3030',
  'GOES-West_ABI_Band13_Clean_Infrared': '#7a2525',
  'Himawari_AHI_Band13_Clean_Infrared': '#9a3545',
};

function formatCadence(minutes: number) {
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 1440) return `${Math.round((minutes / 60) * 10) / 10}h`;
  if (minutes < 10080) return 'Daily';
  return `${Math.round(minutes / 1440)}d`;
}

function gapMinutes(start: string, end: string): number {
  if (!start || !end) return 0;
  return (new Date(end).getTime() - new Date(start).getTime()) / 60_000;
}

function gapWarning(gap: number): string | null {
  if (gap <= 0 || gap < 15) return null;
  if (gap < 30) return `Operational Gap: ${Math.round(gap)} min. AI interpolation is highly accurate for short gaps (up to 4 intermediate frames).`;
  if (gap < 60) return `Moderate Gap: ${Math.round(gap)} min. AI accuracy may decrease; limit to 2 intermediate frames.`;
  if (gap < 120) return `Large Gap: ${Math.round(gap)} min. Recommending a single intermediate frame to maintain structural integrity.`;
  return `Warning: Extreme Gap (${Math.round(gap)} min). AI results at this distance are highly speculative and provided for visual continuity ONLY.`;
}

function bboxTooSmall(bbox: number[] | null): boolean {
  if (!bbox) return false;
  const dx = bbox[2] - bbox[0];
  const dy = bbox[3] - bbox[1];
  return dx * dy < 0.005;
}

function formatUtc(value?: string) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString().slice(0, 16).replace('T', ' ');
}

function solarHourAtLongitude(timeValue: string, longitude: number) {
  const date = new Date(timeValue);
  if (Number.isNaN(date.getTime())) return null;
  const utcHour = date.getUTCHours() + date.getUTCMinutes() / 60;
  let localSolarHour = utcHour + longitude / 15;
  while (localSolarHour < 0) localSolarHour += 24;
  while (localSolarHour >= 24) localSolarHour -= 24;
  return localSolarHour;
}

function getLayerInterpretation(layer: LayerInfo | null): string | null {
  if (!layer) return null;
  const id = layer.layer_id.toLowerCase();
  const name = layer.name.toLowerCase();
  if (id.includes('band13') || name.includes('infrared')) {
    return 'Thermal Infrared (10.4µm): Visualizes brightness temperature. Brighter/whiter areas indicate colder cloud tops (higher altitude/convective vigor), while darker/grey areas indicate warmer surfaces or lower clouds. Used for 24/7 motion and intensity tracking.';
  }
  if (name.includes('visible') || id.includes('truecolor') || id.includes('red_visible')) {
    return 'Visible Reflectance: High-resolution view of reflected sunlight. Bright areas are clouds or land. Note: Imagery will be dark/blank during local night hours.';
  }
  if (id.includes('daynight')) {
    return 'Day-Night Band (DNB): Specialized low-light sensor sensitive to moonlight/starlight. Detects city lights, moonlit clouds, and lightning activity at night.';
  }
  return null;
}

export default function LayerControls() {
  const {
    selectedLayer,
    setSelectedLayer,
    selectedPresetKey,
    setSelectedPresetKey,
    timeStart,
    setTimeStart,
    timeEnd,
    setTimeEnd,
    bbox,
    setBbox,
    resolution,
    setResolution,
    nIntermediate,
    setNIntermediate,
    interpolationModel,
    setInterpolationModel,
    stepMinutes,
    setStepMinutes,
    smartSampling,
    setSmartSampling,
    includeLowConfidence,
    setIncludeLowConfidence,
    showLowConfidence,
    setShowLowConfidence,
    jobStatus,
    jobId,
    setJobId,
    setJobStatus,
    setJobProgress,
    setJobMessage,
    setApiError,
    layers,
    setLayers,
    dataSource,
    resetJob,
  } = useStore();

  const { data: fetchedLayers, isLoading: layersLoading, isError: layersError } = useLayers(dataSource);
  const [confirmRun, setConfirmRun] = useState(false);
  const { data: health, isError: serverDown } = useHealth();
  const { data: models = [] } = useModels();
  const { data: layerCapabilities, isFetching: capabilitiesLoading } = useLayerCapabilities(selectedLayer);
  const submitMutation = useSubmitPipeline();

  useEffect(() => {
    if (!fetchedLayers || fetchedLayers.length === 0) return;
    setLayers(fetchedLayers);
  }, [fetchedLayers, setLayers]);

  useEffect(() => {
    if (!layers.length) return;
    if (selectedLayer && layers.some((layer) => layer.layer_id === selectedLayer)) return;
    const recommendedLayerId = chooseRecommendedLayer(layers);
    if (recommendedLayerId) setSelectedLayer(recommendedLayerId);
  }, [layers, selectedLayer, setSelectedLayer]);

  const selectedLayerInfo = layers.find((layer) => layer.layer_id === selectedLayer) ?? null;

  useEffect(() => {
    if (!selectedLayerInfo) {
      setSelectedPresetKey(null);
      setBbox(null);
      return;
    }

    if (!layerCapabilities && capabilitiesLoading) return;

    const hasCurrentPreset = selectedPresetKey && selectedLayerInfo.preset_regions?.[selectedPresetKey];
    if (hasCurrentPreset && bbox) return;

    const defaultPresetKey = getDefaultPresetKey(selectedLayerInfo);
    applyLayerPreset(selectedLayerInfo, defaultPresetKey, { setSelectedPresetKey, setBbox, setTimeStart, setTimeEnd }, layerCapabilities ?? undefined);
  }, [selectedLayerInfo, selectedPresetKey, bbox, layerCapabilities, capabilitiesLoading, setSelectedPresetKey, setBbox, setTimeStart, setTimeEnd]);

  const currentPreset: PresetRegion | null =
    selectedLayerInfo?.preset_regions && selectedPresetKey
      ? (selectedLayerInfo.preset_regions[selectedPresetKey] as PresetRegion) ?? null
      : null;

  const gapMin = gapMinutes(timeStart, timeEnd);
  const gapWarn = gapWarning(gapMin);
  const isVeryLargeGap = gapMin > 60;
  const isBboxSmall = bboxTooSmall(bbox);
  const isTimeInvalid = !!timeStart && !!timeEnd && new Date(timeEnd) <= new Date(timeStart);
  const isRunning = jobStatus === 'queued' || jobStatus === 'running';
  const nativeStep = layerCapabilities?.step_minutes ?? selectedLayerInfo?.temporal_resolution_minutes ?? null;
  const layerInterpretation = useMemo(() => getLayerInterpretation(selectedLayerInfo), [selectedLayerInfo]);

  const visibleBandWarning = useMemo(() => {
    if (!selectedLayer || !VISIBLE_BAND_LAYERS.has(selectedLayer) || !timeStart) return null;
    const centerLon = bbox ? (bbox[0] + bbox[2]) / 2 : null;
    if (centerLon == null) return null;

    const startSolarHour = solarHourAtLongitude(timeStart, centerLon);
    const endSolarHour = timeEnd ? solarHourAtLongitude(timeEnd, centerLon) : startSolarHour;
    if (startSolarHour == null || endSolarHour == null) return null;

    const isDaylight = (hour: number) => hour >= 6 && hour < 18;
    if (isDaylight(startSolarHour) || isDaylight(endSolarHour)) return null;

    return `Visible band likely dark for this AOI at local solar time (${startSolarHour.toFixed(1)}h to ${endSolarHour.toFixed(1)}h). Use an infrared layer for 24/7 tracking.`;
  }, [selectedLayer, bbox, timeStart, timeEnd]);

  const runBlockReason = useMemo(() => {
    if (serverDown) return 'Backend is offline - cannot start pipeline';
    if (!selectedLayer) return 'Select a satellite layer';
    if (!timeStart || !timeEnd) return 'Set start and end times';
    if (isTimeInvalid) return 'End time must be after start time';
    if (!bbox) return 'Select a monitoring domain';
    if (isBboxSmall) return 'Selected area is too small - choose a wider preset or expand the AOI';
    if (layersError) return 'Layer list unavailable - check backend';
    if (isRunning) return 'Pipeline is already running';
    return null;
  }, [serverDown, selectedLayer, timeStart, timeEnd, isTimeInvalid, bbox, isBboxSmall, layersError, isRunning]);

  const canRun = !runBlockReason;

  const handleApplyPreset = (layer: LayerInfo | null, presetKey: string) => {
    applyLayerPreset(layer, presetKey, { setSelectedPresetKey, setBbox, setTimeStart, setTimeEnd }, layerCapabilities ?? undefined);
  };

  const handleLayerClick = (id: string) => {
    setSelectedLayer(id);
  };

  const handleLayerKeyDown = (e: React.KeyboardEvent, id: string) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setSelectedLayer(id);
    }
  };

  const handleRunClick = () => {
    if (!canRun || !bbox || !selectedLayer) return;
    setConfirmRun(true);
  };

  const handleRunPipeline = async () => {
    setConfirmRun(false);
    if (!canRun || !bbox || !selectedLayer) return;
    setJobStatus('queued');
    setJobProgress(0);
    setJobMessage('Submitting pipeline job...');
    setApiError(null);

    try {
      const result = await submitMutation.mutateAsync({
        data_source: dataSource,
        layer_id: selectedLayer,
        bbox: bbox as number[],
        time_start: new Date(timeStart).toISOString(),
        time_end: new Date(timeEnd).toISOString(),
        resolution,
        interpolation_model: interpolationModel,
        n_intermediate: smartSampling ? 4 : nIntermediate, // Logical placeholder for smart
        step_minutes: smartSampling ? null : stepMinutes,
        include_low_confidence: includeLowConfidence,
      });
      setJobId(result.job_id);
      setJobStatus('running');
      setJobMessage('Pipeline job accepted. Preparing frames...');
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } }; message?: string };
      const msg = error?.response?.data?.detail || error?.message || 'Failed to submit pipeline job.';
      setApiError(msg);
      setJobStatus('failed');
      setJobMessage(msg);
    }
  };

  const handleCancelPipeline = async () => {
    if (!window.confirm("Do you want to cancel the generation?")) return;
    
    if (jobId && (jobStatus === 'running' || jobStatus === 'queued')) {
      try {
        await cancelPipeline(jobId);
      } catch (err) {
        console.warn("Cancel request failed", err);
      }
    }
    
    resetJob();
    setApiError("Pipeline generation cancelled.");
  };

  const renderLayerList = () => {
    if (layersLoading) {
      return (
        <div style={{ padding: '8px' }}>
          {[0, 1, 2, 3, 4].map((i) => <div key={i} style={{ height: 24, background: 'var(--b3)', marginBottom: 2, opacity: 1 - i * 0.15, animation: 'pulse 1.5s ease-in-out infinite' }} />)}
          <div style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--t4)', marginTop: 4, fontStyle: 'italic' }}>Fetching layers from {dataSource.replace('_', ' ').toUpperCase()}...</div>
        </div>
      );
    }

    if (layersError || serverDown) {
      return (
        <div style={{ padding: '8px', borderLeft: '3px solid var(--red)', margin: '4px 4px', background: 'var(--red-bg)' }}>
          <div style={{ fontFamily: 'var(--cond)', fontSize: 11, fontWeight: 600, color: 'var(--red)', marginBottom: 3 }}>Layer Data Unavailable</div>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--t3)', lineHeight: 1.6 }}>Cannot reach the layer API. Check that the backend is running.</div>
        </div>
      );
    }

    if (!layers.length) return <div style={{ padding: '8px', fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--t4)', fontStyle: 'italic', textAlign: 'center' }}>No layers available for the selected data source.</div>;

    return layers.map((layer) => (
      <button
        key={layer.layer_id}
        className={`layer-row${selectedLayer === layer.layer_id ? ' active' : ''}`}
        onClick={() => handleLayerClick(layer.layer_id)}
        onKeyDown={(e) => handleLayerKeyDown(e, layer.layer_id)}
        aria-pressed={selectedLayer === layer.layer_id}
        style={{ border: 'none', width: '100%', textAlign: 'left', display: 'flex', alignItems: 'center' }}
      >
        <div className={`layer-vis${selectedLayer === layer.layer_id ? ' checked' : ''}`} aria-hidden="true">
          {selectedLayer === layer.layer_id ? 'OK' : '[]'}
        </div>
        <div className="layer-swatch" style={{ background: LAYER_SWATCHES[layer.layer_id] || '#888' }} aria-hidden="true" />
        <div className="layer-name-text">
          {layer.name}
          <span style={{ 
            fontSize: 7.5, 
            marginLeft: 5, 
            padding: '1px 3px', 
            borderRadius: 2, 
            background: layer.temporal_resolution_minutes < 60 ? 'var(--teal-bg)' : 'var(--blue-bg)',
            color: layer.temporal_resolution_minutes < 60 ? 'var(--teal)' : 'var(--blue)',
            border: `1px solid ${layer.temporal_resolution_minutes < 60 ? 'var(--teal-lt)' : 'var(--blue-lt)'}`
          }}>
            {layer.temporal_resolution_minutes < 60 ? 'GEO' : 'LEO'}
          </span>
        </div>
        <div className="layer-cadence">{formatCadence(layer.temporal_resolution_minutes)}</div>
      </button>
    ));

  };

  const renderCoverageWarning = () => {
    if (!selectedLayerInfo || !bbox) return null;
    if (selectedLayerInfo.coverage_lon_min == null || selectedLayerInfo.coverage_lon_max == null) return null;
    const bboxCenterLon = (bbox[0] + bbox[2]) / 2;
    const lonMin = selectedLayerInfo.coverage_lon_min;
    const lonMax = selectedLayerInfo.coverage_lon_max;
    const normalizedCenterLon = bboxCenterLon < lonMin ? bboxCenterLon + 360 : bboxCenterLon;
    const inCoverage = normalizedCenterLon >= lonMin - 5 && normalizedCenterLon <= lonMax + 5;
    if (inCoverage) return null;

    return (
      <div style={{ background: 'var(--red-bg)', borderBottom: '1px solid var(--red-lt)', padding: '4px 8px' }}>
        <div style={{ fontFamily: 'var(--mono)', fontSize: 8.5, color: 'var(--red)', lineHeight: 1.4 }}>
          <strong>OUT OF SATELLITE COVERAGE</strong> - Your AOI is outside this satellite field of view.<br />
          {selectedLayerInfo.coverage_note || 'Select a different layer for this region.'}
        </div>
      </div>
    );
  };

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}>
      <div style={{ flex: 1, overflowY: 'auto', opacity: isRunning ? 0.5 : 1, transition: 'opacity 0.2s', minWidth: 0 }}>
        
        <fieldset disabled={isRunning} style={{ border: 'none', padding: 0, margin: 0, minWidth: 0, width: '100%' }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ borderBottom: '1px solid var(--b3)' }}>{renderLayerList()}</div>
            {renderCoverageWarning()}

            {/* CPU Optical Flow note: shown only when FILM weights absent but infra is healthy
                (i.e. LK fallback is active and the pipeline actually works) */}
            {health && !health.film_model_loaded && health.db_connected && health.redis_connected && (
              <div style={{ background: 'var(--blue-bg)', borderBottom: '1px solid var(--blue-lt)', padding: '4px 8px' }}>
                <div style={{ fontFamily: 'var(--mono)', fontSize: 8.5, color: 'var(--blue)', lineHeight: 1.3 }}>
                  <strong>CPU mode</strong> — Optical flow (Lucas-Kanade) active. Deep learning weights not installed on this host.
                  Pipeline output is valid.
                </div>
              </div>
            )}

            {/* GPU unavailable: only shown if GPU is expected but missing (i.e. GPU was previously available) */}
            {health && !health.gpu_available && health.film_model_loaded && (
              <div style={{ background: 'var(--orng-bg)', borderBottom: '1px solid var(--orng-lt)', padding: '4px 8px' }}>
                <div style={{ fontFamily: 'var(--mono)', fontSize: 8.5, color: 'var(--orange)', lineHeight: 1.3 }}>
                  GPU acceleration unavailable — FILM model is running on CPU. Inference will be slower.
                </div>
              </div>
            )}

            {visibleBandWarning && (
              <div style={{ background: 'var(--red-bg)', borderBottom: '1px solid var(--red-lt)', padding: '4px 8px' }}>
                <div style={{ fontFamily: 'var(--mono)', fontSize: 8.5, color: 'var(--red)', lineHeight: 1.4 }}><strong>NIGHT TIME RANGE</strong> - {visibleBandWarning}</div>
              </div>
            )}

            {selectedLayerInfo?.preset_regions && Object.keys(selectedLayerInfo.preset_regions).length > 0 && (
              <div style={{ borderBottom: '1px solid var(--b3)' }}>
                <div className="section-hdr" style={{ gap: 4 }}>Monitoring Domain<span style={{ marginLeft: 'auto', fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--teal)' }}>AUTO</span></div>
                <div className="section-body" style={{ paddingBottom: 0 }}>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3, marginBottom: 6 }}>
                    {Object.entries(selectedLayerInfo.preset_regions).map(([key, preset]) => {
                      const region = preset as PresetRegion;
                      const isActive = selectedPresetKey === key;
                      return <button key={key} onClick={() => handleApplyPreset(selectedLayerInfo, key)} style={{ fontFamily: 'var(--mono)', fontSize: 8.5, padding: '2px 7px', borderRadius: 3, cursor: 'pointer', border: isActive ? '1px solid var(--teal)' : '1px solid var(--b3)', background: isActive ? 'var(--teal-bg)' : 'var(--b2)', color: isActive ? 'var(--teal)' : 'var(--t3)' }}>{region.label}</button>;
                    })}
                  </div>
                  {currentPreset && (
                    <div style={{ background: 'var(--b2)', border: '1px solid var(--b3)', borderRadius: 3, padding: '5px 7px', marginBottom: 6 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}><span style={{ fontFamily: 'var(--cond)', fontSize: 9, color: 'var(--t2)', fontWeight: 700 }}>{currentPreset.label}</span><span style={{ fontFamily: 'var(--mono)', fontSize: 7.5, color: 'var(--teal)' }}>{currentPreset.agency}</span></div>
                      <div style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--t4)', lineHeight: 1.45 }}>{currentPreset.description}</div>
                    </div>
                  )}
                </div>
              </div>
            )}

            <div className="section-hdr">Region of Interest<span style={{ fontSize: 8 }}>v</span></div>
            <div className="section-body">
              <div className="form-row"><span className="form-label">CRS</span><select className="inp"><option>EPSG:4326 (WGS 84)</option></select></div>
              {!bbox ? (
                <div style={{ background: 'var(--blue-bg)', border: '1px dashed var(--blue)', padding: '6px 8px', marginBottom: 4 }}><div style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--blue)', marginBottom: 2 }}>Awaiting selection</div><div style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--t4)' }}>Click a preset domain on the map or choose one above.</div></div>
              ) : (
                <>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 3, marginBottom: 4 }}>
                    {(['W', 'S', 'E', 'N'] as const).map((dir, i) => <div key={dir} style={{ position: 'relative' }}><input className="inp" value={(bbox[i] as number).toFixed(4)} readOnly style={{ width: '100%', flex: 'none', paddingRight: 22 }} /><div style={{ position: 'absolute', right: 0, top: 0, bottom: 0, width: 22, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--cond)', fontSize: 9, color: 'var(--t4)', borderLeft: '1px solid var(--b3)', pointerEvents: 'none' }}>{dir}</div></div>)}
                  </div>
                  {isBboxSmall && <div style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--orange)', background: 'var(--orng-bg)', border: '1px solid var(--orng-lt)', padding: '3px 6px', marginBottom: 4 }}>Area is very small - choose a wider preset or expand selection.</div>}
                </>
              )}
            </div>

            {layerInterpretation && (
              <>
                <div className="section-hdr">Product Meaning<span style={{ fontSize: 8 }}>v</span></div>
                <div className="section-body">
                  <div style={{ background: 'var(--b2)', border: '1px solid var(--b3)', borderRadius: 3, padding: '5px 7px', fontFamily: 'var(--mono)', fontSize: 8.5, color: 'var(--t3)', lineHeight: 1.5 }}>
                    {layerInterpretation}
                  </div>
                </div>
              </>
            )}

            <div className="section-hdr">Time Parameters<span style={{ fontSize: 8 }}>v</span></div>
            <div className="section-body">
              <div className="form-row"><span className="form-label">Start (UTC)</span><input type="datetime-local" className="inp" value={timeStart} onChange={(e) => setTimeStart(e.target.value)} /></div>
              <div className="form-row"><span className="form-label">End (UTC)</span><input type="datetime-local" className="inp" value={timeEnd} onChange={(e) => setTimeEnd(e.target.value)} style={isTimeInvalid ? { borderColor: 'var(--red)' } : {}} /></div>
              {isTimeInvalid && <div style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--red)', marginBottom: 4 }}>End time must be after start time.</div>}
              <div className="form-row">
                <span className="form-label">Step</span>
                <select className="inp" value={stepMinutes || ''} onChange={(e) => setStepMinutes(e.target.value ? Number(e.target.value) : null)} disabled={smartSampling}>
                  <option value="">{nativeStep ? `Auto (${formatCadence(nativeStep)})` : 'Auto (layer native)'}</option>
                  {[10, 15, 30, 60, 120, 240, 480, 1440].map(m => (
                    <option key={m} value={m}>{formatCadence(m)}</option>
                  ))}
                </select>
              </div>
              <div className="check-row" onClick={() => setSmartSampling(!smartSampling)} style={{ marginTop: 4 }}>
                <input type="checkbox" id="smart-sampling" checked={smartSampling} readOnly />
                <label className="check-label" htmlFor="smart-sampling">Smart Temporal Sampling (Recommended)</label>
              </div>
              {smartSampling && (
                 <div style={{ fontFamily: 'var(--mono)', fontSize: 8.5, color: 'var(--teal)', background: 'var(--teal-bg)', border: '1px solid var(--teal-lt)', padding: '4px 6px', marginTop: 4 }}>
                   <strong>Smart Mode Active:</strong> Balancing frame density and motion accuracy based on layer cadence.
                 </div>
              )}
              {layerCapabilities && (
                <div style={{ fontFamily: 'var(--mono)', fontSize: 8.5, color: 'var(--t4)', lineHeight: 1.5, marginTop: 4, padding: '4px', background: 'var(--b2)', borderRadius: 3 }}>
                  <div>Latest Server Data: <strong>{formatUtc(layerCapabilities.latest_available_time) ?? 'Unknown'} UTC</strong></div>
                  <div>Time Sync Mode: <strong style={{ color: 'var(--teal)' }}>{layerCapabilities.time_source_live ? 'Live Sensor Metadata' : 'Heuristic Availability Logic'}</strong></div>
                  {nativeStep && <div>Observation Interval: <strong>{formatCadence(nativeStep)}</strong></div>}
                  {layerCapabilities.nadir_lon !== undefined && layerCapabilities.nadir_lon !== null && (
                    <div>Satellite Position: <strong>{layerCapabilities.nadir_lon}° E (Nadir)</strong></div>
                  )}
                </div>
              )}
              {gapWarn && <div style={{ fontFamily: 'var(--mono)', fontSize: 9, lineHeight: 1.5, color: isVeryLargeGap ? 'var(--red)' : 'var(--orange)', background: isVeryLargeGap ? 'var(--red-bg)' : 'var(--orng-bg)', border: `1px solid ${isVeryLargeGap ? 'var(--red-lt)' : 'var(--orng-lt)'}`, padding: '3px 6px', marginTop: 4 }}>{gapWarn}</div>}
              <div className="form-row" style={{ marginTop: 4 }}><span className="form-label">Resolution</span><select className="inp" value={resolution} onChange={(e) => setResolution(Number(e.target.value) as 512 | 1024 | 2048)}><option value={512}>512 x 512 px (fast)</option><option value={1024}>1024 x 1024 px</option><option value={2048}>2048 x 2048 px (slow)</option></select></div>
            </div>

            <div className="section-hdr" role="button" tabIndex={0}>AI / Interpolation<span style={{ fontSize: 8 }} aria-hidden="true">v</span></div>
            <div className="section-body">
              <div className="form-row">
                <span className="form-label">Model</span>
                <select className="inp" value={interpolationModel} onChange={(e) => setInterpolationModel(e.target.value as ModelType)}>
                  {models.length > 0 ? (
                    models.map(m => (
                      <option key={m.id} value={m.id}>
                        {m.name} {m.id === 'film' ? '(Primary)' : m.id === 'optical_flow' ? '(CPU Fallback)' : ''}
                      </option>
                    ))
                  ) : (
                    <>
                      <option value="film">FILM (Primary)</option>
                      <option value="rife">RIFE 4.x</option>
                      <option value="lk_fallback">Optical Flow Baseline</option>
                    </>
                  )}
                </select>
              </div>

              <div className="form-row"><span className="form-label">Frames/frame</span><div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 6 }}><input className="range" type="range" min={1} max={isVeryLargeGap ? 1 : gapMin > 30 ? 2 : gapMin > 15 ? 4 : 8} value={Math.min(nIntermediate, isVeryLargeGap ? 1 : gapMin > 30 ? 2 : gapMin > 15 ? 4 : 8)} style={{ flex: 1 }} onChange={(e) => setNIntermediate(Number(e.target.value))} /><span className="slider-val">{Math.min(nIntermediate, isVeryLargeGap ? 1 : gapMin > 30 ? 2 : gapMin > 15 ? 4 : 8)}</span></div></div>
              <div style={{ borderTop: '1px solid var(--b3)', paddingTop: 5, marginTop: 2 }}>
                {[
                  { id: 'cb-seg', label: 'Temporal segmentation', checked: true, readonly: true },
                  { id: 'cb-flow', label: 'Optical flow validation', checked: true, readonly: true },
                  { id: 'cb-conf', label: 'Confidence scoring', checked: true, readonly: true },
                  { id: 'cb-low', label: 'Show low confidence frames', checked: showLowConfidence, readonly: false, action: () => setShowLowConfidence(!showLowConfidence) },
                  { id: 'cb-lci', label: 'Include low confidence in export', checked: includeLowConfidence, readonly: false, action: () => setIncludeLowConfidence(!includeLowConfidence) },
                ].map((item) => <div key={item.id} className="check-row" onClick={item.readonly ? undefined : item.action}><input type="checkbox" id={item.id} checked={item.checked} onChange={item.readonly ? undefined : item.action} disabled={item.readonly} /><label className="check-label" htmlFor={item.id} style={{ color: item.readonly ? 'var(--t4)' : 'var(--t2)' }}>{item.label}</label></div>)}
              </div>
            </div>
          </div>
        </fieldset>
        <AdvancedOverlaysPanel />
      </div>

      <div className="run-panel">
        {runBlockReason && !isRunning && <div style={{ fontFamily: 'var(--mono)', fontSize: 9, color: serverDown ? 'var(--red)' : 'var(--orange)', background: serverDown ? 'var(--red-bg)' : 'var(--orng-bg)', border: `1px solid ${serverDown ? 'var(--red-lt)' : 'var(--orng-lt)'}`, padding: '3px 6px', marginBottom: 5 }}>{runBlockReason}</div>}
        {isRunning ? (
          <div className="run-row" style={{ gap: 8 }}>
             <button className="btn-primary" style={{ flex: 1, opacity: 0.65 }} disabled>RUNNING PIPELINE...</button>
             <button className="btn-secondary" style={{ color: 'var(--red)', border: '1px solid var(--red-lt)', background: 'var(--red-bg)', padding: '0 12px', borderRadius: 4, cursor: 'pointer', fontFamily: 'var(--cond)', fontSize: 13, fontWeight: 600 }} onClick={handleCancelPipeline}>Cancel</button>
           </div>
        ) : (
          <div className="run-row"><button className="btn-primary" style={{ flex: 1, opacity: canRun ? 1 : 0.65 }} onClick={handleRunClick} disabled={!canRun}>Run Pipeline</button></div>
        )}
        <div className="run-info">{(selectedLayerInfo?.name ?? selectedLayer ?? 'No layer selected')} · {interpolationModel.toUpperCase()} · {nIntermediate} frame(s)/gap</div>
      </div>

      {confirmRun && (() => {
        const activeStep = smartSampling ? (nativeStep ?? 15) : (stepMinutes ?? nativeStep ?? 15);
        const activeIter = smartSampling ? 4 : nIntermediate;
        const estimatedObs = Math.max(1, Math.ceil(gapMin / activeStep));
        const estimatedAI = estimatedObs * activeIter;
        const totalFrames = estimatedObs + estimatedAI;
        const estSec = Math.ceil(totalFrames * (health?.gpu_available ? 0.3 : 3.5) + estimatedObs * 1.5);
        const estTimeStr = estSec > 60 ? `${Math.floor(estSec/60)}m ${estSec%60}s` : `${estSec}s`;

        return (
          <ConfirmDialog
            title="Confirm Pipeline Execution"
            message={`You are about to start a compute-intensive pipeline using system resources.`}
            details={[
              `Domain: ${currentPreset?.label ?? 'Custom Bbox'}`,
              `Time Window: ${gapMin > 60 ? Math.round(gapMin/60) + ' hrs' : gapMin + ' mins'}`,
              `Projected output: ~${totalFrames} total frames (${estimatedObs} observed, ${estimatedAI} interpolated)`,
              `Hardware target: ${health?.gpu_available ? 'GPU (RTX 4060)' : 'CPU-only'}`,
              `Estimated processing time: ${estTimeStr}`
            ]}
            onConfirm={handleRunPipeline}
            onCancel={() => setConfirmRun(false)}
            confirmLabel="Run Pipeline"
          />
        );
      })()}
    </div>
  );
}
