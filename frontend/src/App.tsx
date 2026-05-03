/**
 * AetherGIS — Modular Monolith Entry Point
 * Routing handled via React Router to separate Brand and App modules.
 */
import { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Shared
import { useHealth, useJobStatus, useJobResults } from '@shared/api/client';

// Brand Module
import LandingPage from '@brand/LandingPage';
import PrivacyPolicy from '@brand/PrivacyPolicy';
import Documentation from '@brand/Documentation';
import ProductPage from '@brand/ProductPage';
import HowItWorksPage from '@brand/HowItWorksPage';
import DataSourcesPage from '@brand/DataSourcesPage';
import Terms from '@brand/Terms';
import Disclaimer from '@brand/Disclaimer';
import SecurityPage from '@brand/SecurityPage';
import AboutPage from '@brand/AboutPage';
import ContactPage from '@brand/ContactPage';
import AccessPage from '@brand/AccessPage';
import StatusPage from '@brand/StatusPage';
// Docs sub-pages are now handled within the single Documentation component via URL hash
import AuthGate from '@shared/components/AuthGate';

// App Module Components
import MapViewer from '@app/components/MapViewer';
import LayerControls from '@app/components/LayerControls';
import AnalysisPanel from '@app/components/AnalysisPanel';
import TimelineScrubber from '@app/components/TimelineScrubber';
import ServerStatus from '@app/components/ServerStatus';
import MenuBar from '@app/components/MenuBar';
import SessionManager from '@app/components/SessionManager';
import SessionGate from '@app/components/SessionGate';
import ProductNoticeBanner from '@app/components/ProductNoticeBanner';
import { DashboardThemeProvider } from '@app/theme/DashboardThemeProvider';
import { useStore } from '@app/store/useStore';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 2, staleTime: 10_000, retryDelay: (n) => Math.min(1000 * 2 ** n, 10_000) },
  },
});

// ─── App Engine Components ──────────────────────────────────────────────────

function JobPoller() {
  const { jobId, jobStatus, setJobStatus, setPipelineResult, setJobProgress, setJobMessage, setApiError } = useStore();
  const isActive = jobStatus === 'queued' || jobStatus === 'running';
  const { data: statusData } = useJobStatus(jobId, isActive);
  const { data: resultsData } = useJobResults(jobId, statusData?.status === 'COMPLETED');

  useEffect(() => {
    if (!statusData) return;
    if (statusData.status === 'COMPLETED') setJobStatus('completed');
    else if (statusData.status === 'FAILED') { setJobStatus('failed'); setApiError(statusData.error || 'Pipeline job failed.'); }
    else if (statusData.status === 'RUNNING') setJobStatus('running');
    if (statusData.progress !== undefined) setJobProgress(statusData.progress);
    if (statusData.message !== undefined) setJobMessage(statusData.message ?? null);
  }, [statusData, setJobStatus, setApiError, setJobProgress, setJobMessage]);

  useEffect(() => {
    if (resultsData) { setPipelineResult(resultsData); setJobMessage('Pipeline complete'); setApiError(null); }
  }, [resultsData, setPipelineResult, setJobMessage, setApiError]);

  return null;
}

function PlaybackEngine() {
  const isPlaying = useStore((s) => s.isPlaying);
  const playbackSpeed = useStore((s) => s.playbackSpeed);
  const hasFrames = useStore((s) => (s.pipelineResult?.frames.length ?? 0) > 0);

  useEffect(() => {
    if (!isPlaying || !hasFrames) return;
    const msPerFrame = 1000 / (10 * playbackSpeed);
    let rafId = 0;
    let lastTick = performance.now();

    const loop = () => {
      const now = performance.now();
      if (now - lastTick >= msPerFrame) {
        useStore.getState().playbackTick();
        lastTick = now;
      }
      rafId = window.requestAnimationFrame(loop);
    };

    rafId = window.requestAnimationFrame(loop);
    return () => window.cancelAnimationFrame(rafId);
  }, [isPlaying, playbackSpeed, hasFrames]);

  return null;
}

function KeyboardHandler() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      const s = useStore.getState();
      switch (e.key) {
        case ' ': e.preventDefault(); s.setIsPlaying(!s.isPlaying); break;
        case 'ArrowLeft': e.preventDefault(); s.setIsPlaying(false); { const p = s.getNextFrameIndex(s.currentFrameIndex, -1); if (p !== null) s.setCurrentFrameIndex(p); } break;
        case 'ArrowRight': e.preventDefault(); s.setIsPlaying(false); { const n = s.getNextFrameIndex(s.currentFrameIndex, 1); if (n !== null) s.setCurrentFrameIndex(n); } break;
        case 'Home': e.preventDefault(); s.setIsPlaying(false); s.seekToStart(); break;
        case 'End': e.preventDefault(); s.setIsPlaying(false); s.seekToEnd(); break;
        case '1': s.setPlaybackSpeed(0.5); break;
        case '2': s.setPlaybackSpeed(1); break;
        case '3': s.setPlaybackSpeed(2); break;
        case '4': s.setPlaybackSpeed(4); break;
        case 'm': case 'M': s.setShowMetadataOverlay(!s.showMetadataOverlay); break;
        case 'Escape': s.setIsPlaying(false); break;
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  return null;
}

function Toolbar() {
  const { bbox, setBbox, dataSource } = useStore();
  const [showSessions, setShowSessions] = useState(false);

  useEffect(() => {
    const clearAoi = () => setBbox(null);
    const openSM = () => setShowSessions(true);
    window.addEventListener('aethergis:clearAoi', clearAoi);
    window.addEventListener('aethergis:openSessionManager', openSM);
    return () => {
      window.removeEventListener('aethergis:clearAoi', clearAoi);
      window.removeEventListener('aethergis:openSessionManager', openSM);
    };
  }, [setBbox]);

  return (
    <>
      <div className="toolbar">
        <div className="tb-group">
          <button className="tb-btn tb-session-btn" onClick={() => setShowSessions(true)}>
            <span className="tb-btn-icon">⊞</span>
            <span className="tb-btn-text">Sessions</span>
          </button>
        </div>
        <div className="tb-sep" />
        <div className="tb-group">
          <button className="tb-btn active" title="Draw AOI">⬚</button>
          <button className="tb-btn" title="Pan">✥</button>
          <button className="tb-btn" title="Zoom to AOI" disabled={!bbox} onClick={() => window.dispatchEvent(new CustomEvent('aethergis:zoomToAoi'))}>⊙</button>
          <button className="tb-btn" title="Clear AOI" disabled={!bbox} onClick={() => setBbox(null)}>⊗</button>
        </div>
        <div className="tb-sep" />
        <div className="tb-group">
          <div className="tb-label">SOURCE:</div>
          <div className="tb-source-badge">
            <span className="tb-src-dot ok" />
            {dataSource === 'nasa_gibs' ? 'NASA GIBS' : dataSource === 'insat' ? 'MOSDAC' : 'BHUVAN'}
          </div>
        </div>
        <div className="tb-sep" />
        <div className="tb-info">CRS: <strong>EPSG:4326</strong></div>
      </div>
      {showSessions && <SessionManager onClose={() => setShowSessions(false)} />}
    </>
  );
}

function StatusBar() {
  const { pipelineResult, jobStatus, bbox, apiError } = useStore();
  const { data: health } = useHealth();
  const statusCls = jobStatus === 'completed' ? 'sb-ready' : jobStatus === 'failed' ? '' : jobStatus !== 'idle' ? 'sb-warn' : 'sb-ready';
  const statusLabel = jobStatus === 'completed' ? '● Pipeline complete' : jobStatus === 'failed' ? '✕ Pipeline failed' : jobStatus !== 'idle' ? '● Running…' : '● Ready';
  const gpuLabel = health?.gpu_device_name?.replace('NVIDIA GeForce ', '') || (health?.gpu_available ? 'Accelerated' : 'Disabled');

  return (
    <div className="statusbar">
      <div className="sb-seg">AetherGIS 2.0.0</div>
      {bbox && <div className="sb-seg">AOI Active</div>}
      {pipelineResult?.frames.length ? <div className="sb-seg">Frames: {pipelineResult.frames.length}</div> : null}
      {apiError && <div className="sb-seg text-red">⚠ Error</div>}
      <div className={`sb-seg ${statusCls}`}>{statusLabel}</div>
      <div className="sb-seg right">GPU: {gpuLabel}</div>
    </div>
  );
}

// ─── Modules ────────────────────────────────────────────────────────────────

/**
 * App Module - The GeoAI Engine Workspace
 */
function AppModule() {
  const [isDesktopViewport, setIsDesktopViewport] = useState(() => {
    if (typeof window === 'undefined') return true;
    const coarsePointer = window.matchMedia('(pointer: coarse)').matches;
    return window.innerWidth >= 1180 && !coarsePointer;
  });

  const refreshHistory = useStore((s) => s.refreshHistory);
  useEffect(() => {
    refreshHistory().catch((err) => console.error('History sync failed:', err));
  }, [refreshHistory]);

  useEffect(() => {
    const evaluateViewport = () => {
      const coarsePointer = window.matchMedia('(pointer: coarse)').matches;
      setIsDesktopViewport(window.innerWidth >= 1180 && !coarsePointer);
    };
    evaluateViewport();
    window.addEventListener('resize', evaluateViewport);
    return () => window.removeEventListener('resize', evaluateViewport);
  }, []);

  if (!isDesktopViewport) {
    return (
      <div className="app-device-gate" role="main" aria-label="Desktop required">
        <div className="app-device-gate-card">
          <div className="app-device-gate-badge">Desktop Workspace Required</div>
          <h1>AetherGIS App is currently desktop-optimized.</h1>
          <p>
            The operational dashboard requires a large viewport for map telemetry, timeline playback,
            and multi-panel analysis. Please open this page on a desktop or laptop display.
          </p>
          <div className="app-device-gate-meta">
            Minimum supported viewport: 1180px width · Fine-pointer device recommended
          </div>
        </div>
      </div>
    );
  }

  return (
    <SessionGate>
      <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--bg)', overflow: 'hidden' }}>
        <JobPoller />
        <PlaybackEngine />
        <KeyboardHandler />
        <MenuBar />
        <Toolbar />
        <ServerStatus />
        <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '262px minmax(0, 1fr) 288px', overflow: 'hidden' }}>
          <div className="dock dock-left"><LayerControls /></div>
          <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}><MapViewer /></div>
            <TimelineScrubber />
          </div>
          <div className="dock dock-right"><AnalysisPanel /></div>
        </div>
        <StatusBar />
        <ProductNoticeBanner />
      </div>
    </SessionGate>
  );
}

function DashboardRoute() {
  return (
    <DashboardThemeProvider>
      <AuthGate>
        <AppModule />
      </AuthGate>
    </DashboardThemeProvider>
  );
}

/**
 * Main Application Shell
 */
export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          {/* Brand Module Routes */}
          <Route path="/" element={<LandingPage />} />
          <Route path="/product" element={<ProductPage />} />
          <Route path="/features" element={<Navigate to="/product" replace />} />
          <Route path="/how-it-works" element={<HowItWorksPage />} />
          <Route path="/data-sources" element={<DataSourcesPage />} />
          <Route path="/privacy" element={<PrivacyPolicy />} />
          <Route path="/docs" element={<Documentation />} />
          <Route path="/docs/getting-started" element={<Navigate to="/docs#quickstart"    replace />} />
          <Route path="/docs/architecture"     element={<Navigate to="/docs#architecture"   replace />} />
          <Route path="/docs/api"              element={<Navigate to="/docs#api-run"        replace />} />
          <Route path="/docs/ai-system"        element={<Navigate to="/docs#ai-system"      replace />} />
          <Route path="/docs/user-guide"       element={<Navigate to="/docs#user-guide"     replace />} />
          <Route path="/terms" element={<Terms />} />
          <Route path="/disclaimer" element={<Disclaimer />} />
          <Route path="/security" element={<SecurityPage />} />
          <Route path="/about" element={<AboutPage />} />
          <Route path="/contact" element={<ContactPage />} />
          <Route path="/access" element={<AccessPage />} />
          <Route path="/status" element={<StatusPage />} />

          {/* App Module Routes */}
          <Route path="/dashboard" element={<DashboardRoute />} />

          {/* Fallback */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
