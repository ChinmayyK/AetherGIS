/**
 * AetherGIS - API client with Axios + React Query hooks.
 */
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

export const apiClient = axios.create({
  baseURL: API_BASE,
  timeout: 30_000,
  withCredentials: true,
  headers: { 
    'Content-Type': 'application/json',
    'X-Aether-CSRF': 'true' // Mandatory for non-GET requests in hardened mode
  },
});

export interface LayerCapabilities {
  layer_id: string;
  time_start?: string;
  time_end?: string;
  latest_available_time?: string;
  suggested_time_start?: string;
  suggested_time_end?: string;
  step_minutes?: number;
  time_source_live: boolean;
  temporal_resolution_minutes: number;
  min_resolution: number;
  max_resolution: number;
  bbox: number[];
  nadir_lon?: number | null;
  coverage_lon_min?: number;
  coverage_lon_max?: number;
  coverage_lat_min?: number;
  coverage_lat_max?: number;
  coverage_note?: string;
  default_preset?: string | null;
}

export interface PipelineRunPayload {
  data_source: 'nasa_gibs' | 'isro_bhuvan' | 'insat';
  session_id?: string | null;
  session_name?: string | null;
  layer_id: string;
  bbox: number[];
  time_start: string;
  time_end: string;
  resolution: number;
  interpolation_model: string;
  n_intermediate: number;
  step_minutes?: number | null;
  include_low_confidence: boolean;
}

export interface HealthResponse {
  status: string;
  redis_connected: boolean;
  db_connected: boolean;
  gpu_available: boolean;
  gpu_device_name?: string | null;
  rife_model_loaded: boolean;
  film_model_loaded: boolean;
  /** True when DL weights are absent but LK optical flow fallback is active. Infra is healthy. */
  cpu_fallback_mode: boolean;
  version: string;
}

export interface SessionSummary {
  session_id: string;
  name: string;
  provider_default: string;
  user_id?: string | null;
  run_count: number;
  created_at: string;
  updated_at: string;
  last_run_at?: string | null;
  archived_at?: string | null;
}

export interface RunSummary {
  run_id: string;
  job_id: string;
  session_id: string;
  provider: string;
  status: string;
  priority: string;
  layer_id: string;
  bbox: number[];
  time_start: string;
  time_end: string;
  resolution: number;
  interpolation_model: string;
  n_intermediate: number;
  step_minutes?: number | null;
  include_low_confidence: boolean;
  params: Record<string, unknown>;
  metrics: Record<string, unknown>;
  manifest: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  error_message?: string | null;
  current_stage: string;
  progress: number;
  message?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  expires_at?: string | null;
}

export const fetchLayers = async (dataSource = 'nasa_gibs') => {
  const { data } = await apiClient.get('/layers', { params: { data_source: dataSource } });
  return data;
};

export const fetchLayerCapabilities = async (layerId: string): Promise<LayerCapabilities> => {
  const { data } = await apiClient.get(`/layers/${layerId}/capabilities`);
  return data;
};

export const submitPipeline = async (payload: PipelineRunPayload): Promise<{ job_id: string }> => {
  const { data } = await apiClient.post('/pipeline/run', payload);
  return data;
};

export const cancelPipeline = async (jobId: string): Promise<{ status: string }> => {
  const { data } = await apiClient.post(`/pipeline/${jobId}/cancel`);
  return data;
};

export const triggerVideoExport = async (
  jobId: string,
  videoType: 'original' | 'interpolated' | 'all',
  tagged: boolean = true,
): Promise<{ status: 'ready' | 'generating'; url?: string }> => {
  const { data } = await apiClient.post(`/pipeline/${jobId}/export/${videoType}?tagged=${tagged}`);
  return data;
};

export const checkVideoReady = async (
  jobId: string,
  videoType: 'original' | 'interpolated' | 'all',
  tagged: boolean = true,
): Promise<{ status: 'ready' | 'not_generated' | 'generating'; url?: string }> => {
  const { data } = await apiClient.get(`/pipeline/${jobId}/export/${videoType}/status?tagged=${tagged}`);
  return data;
};

export const triggerZipExport = async (jobId: string, tagged: boolean = false, originalOnly: boolean = false): Promise<{ status: 'ready' | 'generating'; url?: string }> => {
  const { data } = await apiClient.post(`/pipeline/${jobId}/zip?tagged=${tagged}&original_only=${originalOnly}`);
  return data;
};

export const checkZipReady = async (jobId: string, tagged: boolean = false, originalOnly: boolean = false): Promise<{ status: 'ready' | 'generating'; url?: string; size_bytes?: number }> => {
  const { data } = await apiClient.get(`/pipeline/${jobId}/zip/status?tagged=${tagged}&original_only=${originalOnly}`);
  return data;
};

export const fetchJobStatus = async (jobId: string) => {
  const { data } = await apiClient.get(`/pipeline/${jobId}/status`);
  return data;
};

export const fetchJobResults = async (jobId: string) => {
  const { data } = await apiClient.get(`/pipeline/${jobId}/results`);
  return data;
};

export const fetchHealth = async (): Promise<HealthResponse> => {
  const { data } = await apiClient.get('/health');
  return data;
};

export const fetchSessions = async (): Promise<SessionSummary[]> => {
  const { data } = await apiClient.get('/sessions');
  return data;
};

export const createSession = async (payload: { name: string; provider_default?: string }): Promise<SessionSummary> => {
  const { data } = await apiClient.post('/sessions', payload);
  return data;
};

export const renameSession = async (sessionId: string, name: string): Promise<SessionSummary> => {
  const { data } = await apiClient.patch(`/sessions/${sessionId}`, { name });
  return data;
};

export const archiveSession = async (sessionId: string): Promise<{ session_id: string; status: string }> => {
  const { data } = await apiClient.delete(`/sessions/${sessionId}`);
  return data;
};

export const fetchSessionRuns = async (sessionId: string): Promise<RunSummary[]> => {
  const { data } = await apiClient.get(`/sessions/${sessionId}/runs`);
  return data;
};

export interface ModelInfo {
  id: string;
  name: string;
  description?: string;
}

export const fetchModels = async (): Promise<ModelInfo[]> => {
  const { data } = await apiClient.get('/models');
  return data;
};

export interface SystemConfig {
  mode: 'development' | 'production';
  version: string;
  gpu_support: boolean;
  is_dev_preview: boolean;
  features: {
    auth: boolean;
    queuing: boolean;
    mosdac_offline: boolean;
  };
}

export interface AuthStatus {
  authenticated: boolean;
  user?: string;
  mode: 'development' | 'production';
}

export const fetchAuthMe = async (): Promise<AuthStatus> => {
  const { data } = await apiClient.get('/auth/me');
  return data;
};

export const fetchSystemConfig = async (): Promise<SystemConfig> => {
  const { data } = await apiClient.get('/system/config');
  return data;
};

export interface SatelliteProvider {
  id: string;
  name: string;
  description?: string;
}

export const fetchSatelliteProviders = async (): Promise<SatelliteProvider[]> => {
  const { data } = await apiClient.get('/system/providers');
  return data;
};

export const releaseSessionLock = async (sessionId: string): Promise<{ status: string }> => {
  const { data } = await apiClient.post('/system/session/release', null, {
    params: { session_id: sessionId },
  });
  return data;
};

/** Switch the session to post-pipeline grace mode (5-min export window). */
export const startGraceSession = async (sessionId: string): Promise<{ status: string; ttl_seconds?: number }> => {
  const { data } = await apiClient.post('/system/session/start_grace', null, {
    params: { session_id: sessionId },
  });
  return data;
};

/** Send a heartbeat, optionally switching phase. Returns remaining TTL. */
export const sendSessionHeartbeat = async (
  sessionId: string,
  phase?: 'active' | 'grace',
): Promise<{ status: string; ttl_seconds?: number; phase?: string }> => {
  const { data } = await apiClient.post('/system/session/heartbeat', null, {
    params: { session_id: sessionId, ...(phase ? { phase } : {}) },
  });
  return data;
};

export const getLogoutUrl = (returnTo: string = '/') => {
  const isRelative = API_BASE.startsWith('/');
  const baseUrl = isRelative ? `${window.location.origin}${API_BASE}` : API_BASE;
  const base = new URL(`${baseUrl.replace(/\/+$/, '')}/`);
  base.pathname = `${base.pathname.replace(/\/$/, '')}/auth/logout`;
  base.searchParams.set('return_to', returnTo);
  return base.toString();
};

export const getLoginUrl = () => {
  const isRelative = API_BASE.startsWith('/');
  const baseUrl = isRelative ? `${window.location.origin}${API_BASE}` : API_BASE;
  const base = new URL(`${baseUrl.replace(/\/+$/, '')}/`);
  base.pathname = `${base.pathname.replace(/\/$/, '')}/auth/login`;
  return base.toString();
};

export const useLayers = (dataSource = 'nasa_gibs') =>

  useQuery({
    queryKey: ['layers', dataSource],
    queryFn: () => fetchLayers(dataSource),
    staleTime: 5 * 60 * 1000,
  });

export const useLayerCapabilities = (layerId: string | null) =>
  useQuery({
    queryKey: ['layer-capabilities', layerId],
    queryFn: () => fetchLayerCapabilities(layerId!),
    enabled: !!layerId,
    staleTime: 5 * 60 * 1000,
  });

export const useHealth = () =>
  useQuery({
    queryKey: ['health'],
    queryFn: fetchHealth,
    refetchInterval: 30_000,
    retry: false,
  });

export const useJobStatus = (jobId: string | null, enabled: boolean) =>
  useQuery({
    queryKey: ['job-status', jobId],
    queryFn: () => fetchJobStatus(jobId!),
    enabled: !!jobId && enabled,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === 'COMPLETED' || status === 'FAILED') return false;
      return 2000;
    },
  });

export const useJobResults = (jobId: string | null, enabled: boolean) =>
  useQuery({
    queryKey: ['job-results', jobId],
    queryFn: () => fetchJobResults(jobId!),
    enabled: !!jobId && enabled,
    retry: false,
  });

export const useSubmitPipeline = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: submitPipeline,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['job-status'] });
    },
  });
};

export const getVideoUrl = (jobId: string, type: 'original' | 'interpolated') =>
  `${API_BASE}/pipeline/${jobId}/video/${type}`;

export const getFrameUrl = (jobId: string, idx: number) =>
  `${API_BASE}/pipeline/${jobId}/frames/${idx}`;

export const getMetadataUrl = (jobId: string) =>
  `${API_BASE}/pipeline/${jobId}/metadata`;

export const getReportUrl = (jobId: string) =>
  `${API_BASE}/pipeline/${jobId}/report`;

export const getZipUrl = (jobId: string) =>
  `${API_BASE}/pipeline/${jobId}/zip`;

// ─── Advanced Analytics API (Modules 1–15) ────────────────────────────────────

export const fetchTrajectories = async (jobId: string) => {
  const { data } = await apiClient.get(`/jobs/${jobId}/trajectories`);
  return data;
};

export const fetchPredictions = async (
  jobId: string,
  nAhead: number = 3,
  stepMinutes: number = 10,
) => {
  const { data } = await apiClient.post(`/jobs/${jobId}/predict`, {
    n_ahead: nAhead,
    step_minutes: stepMinutes,
  });
  return data;
};

export const fetchExplanation = async (jobId: string, frameIdx: number) => {
  const { data } = await apiClient.get(`/jobs/${jobId}/explain/${frameIdx}`);
  return data;
};

export const fetchAlerts = async (jobId: string) => {
  const { data } = await apiClient.get(`/jobs/${jobId}/alerts`);
  return data;
};

export const fetchTimeSeries = async (jobId: string) => {
  const { data } = await apiClient.get(`/jobs/${jobId}/time_series`);
  return data;
};

export const submitReplay = async (
  jobId: string,
  interpolationModel: string,
  nIntermediate: number,
  regionBbox?: number[],
) => {
  const { data } = await apiClient.post(`/jobs/${jobId}/replay`, {
    interpolation_model: interpolationModel,
    n_intermediate: nIntermediate,
    region_bbox: regionBbox ?? null,
  });
  return data;
};

export const fetchHeatmap = async (jobId: string, type: 'motion' | 'uncertainty' | 'anomaly') => {
  const { data } = await apiClient.get(`/jobs/${jobId}/heatmap/${type}`);
  return data;
};

export const fetchTemporalConsistency = async (jobId: string) => {
  const { data } = await apiClient.get(`/jobs/${jobId}/temporal_consistency`);
  return data;
};

export const fetchMetricEvolution = async (jobId: string) => {
  const { data } = await apiClient.get(`/jobs/${jobId}/metric_evolution`);
  return data;
};

export const fetchConfidenceMap = async (jobId: string, frameIdx: number) => {
  const { data } = await apiClient.get(`/jobs/${jobId}/confidence_map/${frameIdx}`);
  return data;
};

export const fetchChangeMap = async (jobId: string, frameIdx: number) => {
  const { data } = await apiClient.get(`/jobs/${jobId}/change_map/${frameIdx}`);
  return data;
};

export const submitRegionQuery = async (payload: {
  job_id: string;
  bbox: number[];
  time_start: string;
  time_end: string;
}) => {
  const { data } = await apiClient.post('/region/query', payload);
  return data;
};

export const fetchMetricsSummary = async () => {
  const { data } = await apiClient.get('/metrics/summary');
  return data;
};

export const fetchSystemPerformance = async () => {
  const { data } = await apiClient.get('/system/performance');
  return data;
};

// ─── React Query hooks for new endpoints ──────────────────────────────────────

export const useTrajectories = (jobId: string | null, enabled: boolean) =>
  useQuery({
    queryKey: ['trajectories', jobId],
    queryFn: () => fetchTrajectories(jobId!),
    enabled: !!jobId && enabled,
    staleTime: 60_000,
    retry: 1,
  });

export const useAlerts = (jobId: string | null, enabled: boolean) =>
  useQuery({
    queryKey: ['alerts', jobId],
    queryFn: () => fetchAlerts(jobId!),
    enabled: !!jobId && enabled,
    staleTime: 60_000,
    retry: 1,
  });

export const useTimeSeries = (jobId: string | null, enabled: boolean) =>
  useQuery({
    queryKey: ['time-series', jobId],
    queryFn: () => fetchTimeSeries(jobId!),
    enabled: !!jobId && enabled,
    staleTime: 60_000,
    retry: 1,
  });

export const useMetricEvolution = (jobId: string | null, enabled: boolean) =>
  useQuery({
    queryKey: ['metric-evolution', jobId],
    queryFn: () => fetchMetricEvolution(jobId!),
    enabled: !!jobId && enabled,
    staleTime: 60_000,
    retry: 1,
  });

export const useTemporalConsistency = (jobId: string | null, enabled: boolean) =>
  useQuery({
    queryKey: ['temporal-consistency', jobId],
    queryFn: () => fetchTemporalConsistency(jobId!),
    enabled: !!jobId && enabled,
    staleTime: 60_000,
    retry: 1,
  });

export const useMetricsSummary = () =>
  useQuery({
    queryKey: ['metrics-summary'],
    queryFn: fetchMetricsSummary,
    staleTime: 30_000,
    retry: 1,
  });

export const useExplanation = (jobId: string | null, frameIdx: number, enabled: boolean) =>
  useQuery({
    queryKey: ['explanation', jobId, frameIdx],
    queryFn: () => fetchExplanation(jobId!, frameIdx),
    enabled: !!jobId && enabled && frameIdx >= 0,
    staleTime: 120_000,
    retry: 1,
  });

export const useHeatmap = (jobId: string | null, type: 'motion' | 'uncertainty' | 'anomaly', enabled: boolean) =>
  useQuery({
    queryKey: ['heatmap', jobId, type],
    queryFn: () => fetchHeatmap(jobId!, type),
    enabled: !!jobId && enabled,
    staleTime: 120_000,
    retry: 1,
  });

export const useModels = () =>
  useQuery({
    queryKey: ['models'],
    queryFn: fetchModels,
    staleTime: 10 * 60 * 1000,
    retry: 1,
  });

export const useSatelliteProviders = () =>
  useQuery({
    queryKey: ['satellite-providers'],
    queryFn: fetchSatelliteProviders,
    staleTime: 10 * 60 * 1000,
    retry: 1,
  });

export const useSystemConfig = () =>
  useQuery({
    queryKey: ['system-config'],
    queryFn: fetchSystemConfig,
    staleTime: Infinity, // System mode doesn't change at runtime
    retry: typeof window !== 'undefined' && ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname) ? false : 3,
  });

export const useAuth = () =>
  useQuery({
    queryKey: ['auth-me'],
    queryFn: fetchAuthMe,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
