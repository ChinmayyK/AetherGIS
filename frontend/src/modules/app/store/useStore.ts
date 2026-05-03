/**
 * AetherGIS - Zustand global state store.
 */
import { create } from 'zustand';
import { fetchSessions, fetchSessionRuns } from '@shared/api/client';

export type ConfidenceClass = 'HIGH' | 'MEDIUM' | 'LOW' | 'REJECTED';
export interface FrameMetadata {
  frame_index: number;
  timestamp: string;
  is_interpolated: boolean;
  confidence_score?: number;
  confidence_class?: ConfidenceClass;
  model_used?: string;
  flow_consistency?: number;
  mad_score?: number;
  gap_minutes?: number;
  gap_category?: string;
  psnr?: number;
  ssim?: number;
  provider_source?: string;
}
export interface QualityMetrics {
  tcs?: number;
  fsi?: number;
  avg_psnr?: number;
  avg_ssim?: number;
  high_confidence_count: number;
  medium_confidence_count: number;
  low_confidence_count: number;
  rejected_count: number;
  total_frames: number;
  interpolated_frames: number;
  observed_frames: number;
}
export interface PipelineResult {
  job_id: string;
  session_id?: string;
  status: 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED';
  layer_id: string;
  data_source?: string;
  bbox?: [number, number, number, number];
  time_start?: string;
  time_end?: string;
  original_video_url?: string;
  interpolated_video_url?: string;
  frames: FrameMetadata[];
  metrics?: QualityMetrics;
  error?: string;
  /** Human-readable session name, auto-generated or user-renamed. */
  session_name?: string;
}
export interface PresetRegion {
  label: string;
  bbox: [number, number, number, number];
  description: string;
  agency: string;
}
export interface LayerInfo {
  layer_id: string;
  name: string;
  temporal_resolution_minutes: number;
  use_case: string;
  description?: string;
  nadir_lon?: number | null;
  coverage_lon_min?: number;
  coverage_lon_max?: number;
  coverage_lat_min?: number;
  coverage_lat_max?: number;
  coverage_note?: string;
  preset_regions?: Record<string, PresetRegion>;
  default_preset?: string;
}
export type ComparisonMode = 'side-by-side' | 'overlay';
export type JobStatus = 'idle' | 'queued' | 'running' | 'completed' | 'failed';
export type ModelType = 'rife' | 'film' | 'lk_fallback';
import { persist } from 'zustand/middleware';

export type PlaybackMode = 'all' | 'original' | 'interpolated';

// ─── Advanced module types ─────────────────────────────────────────────────
export interface TrajectoryPoint { x: number; y: number; frame_index: number; }
export interface Trajectory {
  id: string; start_frame: number; end_frame: number;
  points: TrajectoryPoint[];
  motion_vector: { dx: number; dy: number };
  speed: number; intensity: number; direction_deg: number;
}
export interface AlertItem {
  id: string; frame_index: number; region: string;
  type: string; severity: 'high' | 'medium' | 'low';
  description: string; timestamp: string; value?: number;
}
export interface ConsistencyIssue {
  frame: number; issue: string; mad_score: number; z_score: number; severity: string;
}
export interface TrendPoint { t: string; value: number; is_interpolated?: boolean; class?: string; }
export interface PredictionFrame {
  step: number; minutes_ahead: number; timestamp: string;
  confidence: number; label: string; data_url: string | null;
  motion_dx: number; motion_dy: number;
}
export interface ExplanationResult {
  frame_index: number;
  motion_sources: { bbox: number[]; score: number }[];
  uncertainty_regions: { bbox: number[]; score: number }[];
  confidence_zones: { bbox: number[]; score: number }[];
  global_uncertainty: number; global_confidence: number;
  overlay_url: string | null;
}
export interface HeatmapData { type: string; data_url: string; total_frames: number; }
export interface MetricEvolution {
  psnr_trend: { value: number }[];
  avg_psnr: number;
  ssim_trend: { value: number }[];
  avg_ssim: number;
  confidence_trend: { value: number }[];
  confidence_stability: number;
}
export interface TimeSeriesData {
  brightness_trend: { value: number }[];
  mean_brightness: number;
  motion_trend: { value: number }[];
  change_rate: number;
  coverage_trend: { value: number }[];
  mean_coverage: number | null;
}

interface TGISState {
  sessionId: string;
  dataSource: 'nasa_gibs' | 'isro_bhuvan' | 'insat';
  selectedLayer: string | null;
  selectedPresetKey: string | null;
  timeStart: string;
  timeEnd: string;
  bbox: [number, number, number, number] | null;
  resolution: 512 | 1024 | 2048;
  nIntermediate: number;
  interpolationModel: ModelType;
  stepMinutes: number | null;
  smartSampling: boolean;
  includeLowConfidence: boolean;
  apiError: string | null;
  jobId: string | null;
  jobStatus: JobStatus;
  jobProgress: number;
  jobMessage: string | null;
  pipelineResult: PipelineResult | null;
  jobHistory: PipelineResult[];
  currentFrameIndex: number;
  isPlaying: boolean;
  playbackSpeed: 0.5 | 1 | 2 | 4;
  playbackMode: PlaybackMode;
  showMetadataOverlay: boolean;
  comparisonMode: ComparisonMode;
  showLowConfidence: boolean;
  activePanel: 'controls' | 'analysis' | 'export';
  layers: LayerInfo[];
  // ── Advanced overlay toggles (all default OFF) ───────────────────────────
  showTrajectories: boolean;
  showUncertaintyMap: boolean;
  showChangeMap: boolean;
  showAnomalies: boolean;
  showExplainability: boolean;
  enablePrediction: boolean;
  // ── Advanced data cache ───────────────────────────────────────────────────
  trajectories: Trajectory[] | null;
  alerts: AlertItem[] | null;
  predictions: PredictionFrame[] | null;
  explanation: ExplanationResult | null;
  heatmaps: Record<string, HeatmapData>;          // keyed by type
  consistencyIssues: ConsistencyIssue[] | null;
  metricEvolution: MetricEvolution | null;
  timeSeries: TimeSeriesData | null;
  // ── Loading states ────────────────────────────────────────────────────────
  loadingTrajectories: boolean;
  loadingAlerts: boolean;
  loadingPredictions: boolean;
  loadingHeatmap: Record<string, boolean>;
  // ── Overlay opacity ───────────────────────────────────────────────────────
  overlayOpacity: Record<string, number>;         // keyed by overlay type
  setDataSource: (src: 'nasa_gibs' | 'isro_bhuvan' | 'insat') => void;
  setSelectedLayer: (id: string | null) => void;
  setSelectedPresetKey: (key: string | null) => void;
  setTimeStart: (t: string) => void;
  setTimeEnd: (t: string) => void;
  setBbox: (bbox: [number, number, number, number] | null) => void;
  setResolution: (r: 512 | 1024 | 2048) => void;
  setNIntermediate: (n: number) => void;
  setInterpolationModel: (m: ModelType) => void;
  setStepMinutes: (m: number | null) => void;
  setSmartSampling: (v: boolean) => void;
  setIncludeLowConfidence: (v: boolean) => void;
  setApiError: (e: string | null) => void;
  setJobId: (id: string | null) => void;
  setJobStatus: (s: JobStatus) => void;
  setJobProgress: (p: number) => void;
  setJobMessage: (m: string | null) => void;
  setPipelineResult: (r: PipelineResult | null) => void;
  setCurrentFrameIndex: (i: number | ((prev: number) => number)) => void;
  setIsPlaying: (v: boolean) => void;
  setPlaybackSpeed: (s: 0.5 | 1 | 2 | 4) => void;
  setPlaybackMode: (m: PlaybackMode) => void;
  setShowMetadataOverlay: (v: boolean) => void;
  setComparisonMode: (m: ComparisonMode) => void;
  setShowLowConfidence: (v: boolean) => void;
  setActivePanel: (p: 'controls' | 'analysis' | 'export') => void;
  setLayers: (l: LayerInfo[]) => void;
  // ── Advanced toggle setters ───────────────────────────────────────────────
  setShowTrajectories: (v: boolean) => void;
  setShowUncertaintyMap: (v: boolean) => void;
  setShowChangeMap: (v: boolean) => void;
  setShowAnomalies: (v: boolean) => void;
  setShowExplainability: (v: boolean) => void;
  setEnablePrediction: (v: boolean) => void;
  // ── Advanced data setters ─────────────────────────────────────────────────
  setTrajectories: (v: Trajectory[] | null) => void;
  setAlerts: (v: AlertItem[] | null) => void;
  setPredictions: (v: PredictionFrame[] | null) => void;
  setExplanation: (v: ExplanationResult | null) => void;
  setHeatmap: (type: string, data: HeatmapData) => void;
  setConsistencyIssues: (v: ConsistencyIssue[] | null) => void;
  setMetricEvolution: (v: MetricEvolution | null) => void;
  setTimeSeries: (v: TimeSeriesData | null) => void;
  setLoadingTrajectories: (v: boolean) => void;
  setLoadingAlerts: (v: boolean) => void;
  setLoadingPredictions: (v: boolean) => void;
  setLoadingHeatmap: (type: string, v: boolean) => void;
  setOverlayOpacity: (type: string, v: number) => void;
  refreshHistory: () => Promise<void>;
  resetJob: () => void;
  loadSession: (result: PipelineResult) => void;
  clearSession: () => void;
  deleteRun: (jobId: string) => void;
  deleteSession: (sessionId: string) => void;
  renameSession: (sessionId: string, name: string) => void;
  /** Returns frames visible under current playbackMode + showLowConfidence filter. */
  getVisibleFrames: () => FrameMetadata[];
  /** Returns the next valid frame index from a given index, or null if at end. */
  getNextFrameIndex: (fromIndex: number, step: 1 | -1) => number | null;
  /** Advance playback by one tick. Stops at end. Call from a single interval. */
  playbackTick: () => void;
  /** Seek to first visible frame. */
  seekToStart: () => void;
  /** Seek to last visible frame. */
  seekToEnd: () => void;
}
const NOW = new Date();
const TWO_HOURS_AGO = new Date(NOW.getTime() - 2 * 60 * 60 * 1000);

export const useStore = create<TGISState>()(
  persist(
    (set) => ({
      sessionId: typeof window !== 'undefined' ? (localStorage.getItem('aethergis-session-id') || crypto.randomUUID()) : '',
      selectedLayer: null,
      selectedPresetKey: null,
      timeStart: TWO_HOURS_AGO.toISOString().slice(0, 16),
      timeEnd: NOW.toISOString().slice(0, 16),
      bbox: null,
      resolution: 1024,
      nIntermediate: 4,
      interpolationModel: 'film',
      stepMinutes: null,
      smartSampling: true,
      includeLowConfidence: false,
      jobId: null,
      jobStatus: 'idle',
      jobProgress: 0,
      jobMessage: null,
      pipelineResult: null,
      jobHistory: [],
      currentFrameIndex: 0,
      isPlaying: false,
      playbackSpeed: 1,
      playbackMode: 'all',
      showMetadataOverlay: true,
      comparisonMode: 'side-by-side',
      showLowConfidence: false,
      activePanel: 'controls',
      layers: [],
      apiError: null,
      dataSource: 'nasa_gibs',
      // ── Advanced overlay toggles ─────────────────────────────────────────
      showTrajectories: false,
      showUncertaintyMap: false,
      showChangeMap: false,
      showAnomalies: false,
      showExplainability: false,
      enablePrediction: false,
      // ── Advanced data cache ──────────────────────────────────────────────
      trajectories: null,
      alerts: null,
      predictions: null,
      explanation: null,
      heatmaps: {},
      consistencyIssues: null,
      metricEvolution: null,
      timeSeries: null,
      // ── Loading states ───────────────────────────────────────────────────
      loadingTrajectories: false,
      loadingAlerts: false,
      loadingPredictions: false,
      loadingHeatmap: {},
      // ── Overlay opacities (0–1) ──────────────────────────────────────────
      overlayOpacity: { trajectories: 0.85, uncertainty: 0.65, change: 0.65, anomaly: 0.75, explainability: 0.55 },
      setDataSource: (src) => set({ dataSource: src, selectedLayer: null, selectedPresetKey: null, bbox: null, jobStatus: 'idle', jobProgress: 0, jobId: null, pipelineResult: null }),
      setSelectedLayer: (id) => set({ selectedLayer: id }),
      setSelectedPresetKey: (key) => set({ selectedPresetKey: key }),
      setTimeStart: (t) => set({ timeStart: t }),
      setTimeEnd: (t) => set({ timeEnd: t }),
      setBbox: (bbox) => set({ bbox }),
      setResolution: (r) => set({ resolution: r }),
      setNIntermediate: (n) => set({ nIntermediate: n }),
      setInterpolationModel: (m) => set({ interpolationModel: m }),
      setStepMinutes: (m) => set({ stepMinutes: m }),
      setSmartSampling: (v) => set({ smartSampling: v }),
      setIncludeLowConfidence: (v) => set({ includeLowConfidence: v }),
      setJobId: (id) => set({ jobId: id }),
      setJobStatus: (s) => set({ jobStatus: s }),
      setJobProgress: (p) => set({ jobProgress: p }),
      setJobMessage: (m) => set({ jobMessage: m }),
      // ── Advanced toggle setters ──────────────────────────────────────────
      setShowTrajectories: (v) => set({ showTrajectories: v }),
      setShowUncertaintyMap: (v) => set({ showUncertaintyMap: v }),
      setShowChangeMap: (v) => set({ showChangeMap: v }),
      setShowAnomalies: (v) => set({ showAnomalies: v }),
      setShowExplainability: (v) => set({ showExplainability: v }),
      setEnablePrediction: (v) => set({ enablePrediction: v }),
      // ── Advanced data setters ────────────────────────────────────────────
      setTrajectories: (v) => set({ trajectories: v }),
      setAlerts: (v) => set({ alerts: v }),
      setPredictions: (v) => set({ predictions: v }),
      setExplanation: (v) => set({ explanation: v }),
      setHeatmap: (type, data) => set((state) => ({ heatmaps: { ...state.heatmaps, [type]: data } })),
      setConsistencyIssues: (v) => set({ consistencyIssues: v }),
      setMetricEvolution: (v) => set({ metricEvolution: v }),
      setTimeSeries: (v) => set({ timeSeries: v }),
      setLoadingTrajectories: (v) => set({ loadingTrajectories: v }),
      setLoadingAlerts: (v) => set({ loadingAlerts: v }),
      setLoadingPredictions: (v) => set({ loadingPredictions: v }),
      setLoadingHeatmap: (type, v) => set((state) => ({ loadingHeatmap: { ...state.loadingHeatmap, [type]: v } })),
      setOverlayOpacity: (type, v) => set((state) => ({ overlayOpacity: { ...state.overlayOpacity, [type]: v } })),
      setPipelineResult: (r) => set((state) => {
        if (!r) return { pipelineResult: null };
        // Auto-name: "LayerShort · Region · HH:MM–HH:MM"
        const layerShort = r.layer_id.split('_').slice(0, 2).join(' ');
        const region = state.selectedPresetKey ?? 'Custom AOI';
        const t0 = r.time_start ? new Date(r.time_start).toISOString().slice(11, 16) : '--:--';
        const t1 = r.time_end ? new Date(r.time_end).toISOString().slice(11, 16) : '--:--';
        const autoName = r.session_name ?? `${layerShort} · ${region} · ${t0}–${t1} UTC`;
        const withName: PipelineResult = { ...r, session_name: autoName };
        const isNew = !state.jobHistory.some(j => j.job_id === r.job_id);
        const newHistory = isNew ? [withName, ...state.jobHistory].slice(0, 20) : state.jobHistory;
        return { pipelineResult: withName, jobHistory: newHistory };
      }),
      setCurrentFrameIndex: (i) => set((state) => ({ currentFrameIndex: typeof i === 'function' ? i(state.currentFrameIndex) : i })),
      setIsPlaying: (v) => set({ isPlaying: v }),
      setPlaybackSpeed: (s) => set({ playbackSpeed: s }),
      setPlaybackMode: (m) => set({ playbackMode: m, currentFrameIndex: 0 }),
      setShowMetadataOverlay: (v) => set({ showMetadataOverlay: v }),
      setComparisonMode: (m) => set({ comparisonMode: m }),
      setShowLowConfidence: (v) => set({ showLowConfidence: v }),
      setActivePanel: (p) => set({ activePanel: p }),
      setLayers: (l) => set({ layers: l }),
      setApiError: (e) => set({ apiError: e }),
      refreshHistory: async () => {
        try {
          const sessions = await fetchSessions();
          let allHistory: PipelineResult[] = [];
          
          for (const s of sessions) {
            const runs = await fetchSessionRuns(s.session_id);
            const mappedRuns: PipelineResult[] = (runs as { 
              job_id: string; 
              status: string; 
              layer_id: string; 
              provider: string; 
              bbox: number[]; 
              time_start: string; 
              time_end: string;
              result?: { frames?: FrameMetadata[]; metrics?: QualityMetrics };
            }[]).map((r) => ({
              job_id: r.job_id,
              session_id: s.session_id,
              status: r.status.toUpperCase() as PipelineResult['status'],
              layer_id: r.layer_id,
              data_source: r.provider,
              bbox: r.bbox as [number, number, number, number],
              time_start: r.time_start,
              time_end: r.time_end,
              session_name: s.name,
              frames: r.result?.frames ?? [],
              metrics: r.result?.metrics ?? undefined,
            }));
            allHistory = [...allHistory, ...mappedRuns];
          }

          // Sort by time descending (newest first)
          allHistory.sort((a, b) => {
            const tA = a.time_start ? new Date(a.time_start).getTime() : 0;
            const tB = b.time_start ? new Date(b.time_start).getTime() : 0;
            return tB - tA;
          });

          set({ jobHistory: allHistory.slice(0, 100) });
        } catch (err) {
          console.warn('History sync failed', err);
        }
      },

      resetJob: () => set({ jobStatus: 'idle', jobProgress: 0, jobMessage: null, pipelineResult: null, currentFrameIndex: 0, isPlaying: false,
        trajectories: null, alerts: null, predictions: null, explanation: null, heatmaps: {}, consistencyIssues: null, metricEvolution: null, timeSeries: null,
      }),
      loadSession: (result) => set({
        pipelineResult: result,
        jobId: result.job_id,
        jobStatus: 'completed',
        selectedLayer: result.layer_id,
        bbox: result.bbox ?? null,
        timeStart: result.time_start ? result.time_start.slice(0, 16) : TWO_HOURS_AGO.toISOString().slice(0, 16),
        timeEnd: result.time_end ? result.time_end.slice(0, 16) : NOW.toISOString().slice(0, 16),
        currentFrameIndex: 0,
        isPlaying: false,
      }),
      clearSession: () => set({
        pipelineResult: null,
        jobStatus: 'idle',
        jobId: null,
        jobProgress: 0,
        currentFrameIndex: 0,
        isPlaying: false,
      }),

      deleteRun: (jobId) => {
        fetch(`/api/v1/pipeline/${jobId}`, { method: 'DELETE' }).catch(() => {});
        set((state) => ({
          jobHistory: state.jobHistory.filter(j => j.job_id !== jobId),
          ...(state.pipelineResult?.job_id === jobId
            ? { pipelineResult: null, jobId: null, jobStatus: 'idle' as const, currentFrameIndex: 0, isPlaying: false }
            : {}),
        }));
      },

      deleteSession: (sessionId) => {
        fetch(`/api/v1/sessions/${sessionId}`, { method: 'DELETE' }).catch(() => {});
        set((state) => ({
          jobHistory: state.jobHistory.filter(j => j.session_id !== sessionId),
          ...(state.pipelineResult?.session_id === sessionId
            ? { pipelineResult: null, jobId: null, jobStatus: 'idle' as const, currentFrameIndex: 0, isPlaying: false }
            : {}),
        }));
      },

      renameSession: (sessionId, name) => {
        fetch(`/api/v1/sessions/${sessionId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name }),
        }).catch(() => {});
        set((state) => ({
          jobHistory: state.jobHistory.map(j =>
            j.session_id === sessionId ? { ...j, session_name: name } : j
          ),
          pipelineResult: state.pipelineResult?.session_id === sessionId
            ? { ...state.pipelineResult, session_name: name }
            : state.pipelineResult,
        }));
      },

      // ─── Playback engine (single source of truth) ────────────────────────────
      getVisibleFrames: (): FrameMetadata[] => {
        const state = useStore.getState();
        const all: FrameMetadata[] = state.pipelineResult?.frames ?? [];
        const { playbackMode, showLowConfidence } = state;
        return all.filter((f: FrameMetadata): boolean => {
          if (f.confidence_class === 'REJECTED') return false;
          if (!showLowConfidence && f.confidence_class === 'LOW' && f.is_interpolated) return false;
          if (playbackMode === 'original') return !f.is_interpolated;
          if (playbackMode === 'interpolated') return f.is_interpolated;
          return true; // 'all'
        });
      },

      getNextFrameIndex: (fromIndex: number, step: 1 | -1): number | null => {
        const state = useStore.getState();
        const all: FrameMetadata[] = state.pipelineResult?.frames ?? [];
        const { playbackMode, showLowConfidence } = state;
        let curr = fromIndex + step;
        while (curr >= 0 && curr < all.length) {
          const f: FrameMetadata = all[curr];
          if (f.confidence_class === 'REJECTED') { curr += step; continue; }
          if (!showLowConfidence && f.confidence_class === 'LOW' && f.is_interpolated) { curr += step; continue; }
          if (playbackMode === 'original' && f.is_interpolated) { curr += step; continue; }
          if (playbackMode === 'interpolated' && !f.is_interpolated) { curr += step; continue; }
          return curr;
        }
        return null;
      },

      playbackTick: (): void => {
        const state = useStore.getState();
        if (!state.isPlaying) return;
        const next = state.getNextFrameIndex(state.currentFrameIndex, 1);
        if (next === null) {
          state.seekToStart();
        } else {
          useStore.setState({ currentFrameIndex: next });
        }
      },

      seekToStart: (): void => {
        const state = useStore.getState();
        const first = state.getNextFrameIndex(-1, 1);
        useStore.setState({ currentFrameIndex: first ?? 0 });
      },

      seekToEnd: (): void => {
        const state = useStore.getState();
        const len = state.pipelineResult?.frames.length ?? 0;
        const last = state.getNextFrameIndex(len, -1);
        if (last !== null) useStore.setState({ currentFrameIndex: last });
      },
    }),
    {
      name: 'aethergis-storage',
      partialize: (state) => ({
        sessionId: state.sessionId,
        dataSource: state.dataSource,
        selectedLayer: state.selectedLayer,
        selectedPresetKey: state.selectedPresetKey,
        timeStart: state.timeStart,
        timeEnd: state.timeEnd,
        bbox: state.bbox,
        resolution: state.resolution,
        nIntermediate: state.nIntermediate,
        interpolationModel: state.interpolationModel,
        stepMinutes: state.stepMinutes,
        smartSampling: state.smartSampling,
        pipelineResult: state.pipelineResult,
        jobHistory: state.jobHistory,
        jobId: state.jobId,
        playbackMode: state.playbackMode,
      }),
    }
  )
);


