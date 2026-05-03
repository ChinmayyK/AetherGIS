/**
 * AetherGIS — MenuBar with functional dropdown menus.
 * CSS-only hover dropdowns (no extra dependencies).
 * Each menu item maps to a real store action or browser action.
 */
import { useRef, useState } from 'react';
import { useStore } from '@app/store/useStore';
import {
  getLogoutUrl,
  releaseSessionLock,
  triggerVideoExport,
  useAuth,
  useHealth,
  useSystemConfig,
} from '@shared/api/client';
import ConfirmDialog from './ConfirmDialog';
import { useDashboardTheme } from '@app/theme/useDashboardTheme';

/* ─── Types ──────────────────────────────────────────────────────────────── */
interface MenuItem {
  label: string;
  shortcut?: string;
  divider?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  href?: string;
}

/* ─── Single dropdown menu ───────────────────────────────────────────────── */
function Menu({ label, items }: { label: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const close = () => setOpen(false);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setOpen(!open);
    } else if (e.key === 'Escape') {
      close();
    }
  };

  return (
    <div
      ref={ref}
      className="menu-root"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        className={`menu-item${open ? ' active' : ''}`}
        onClick={() => setOpen(!open)}
        onKeyDown={handleKeyDown}
        aria-haspopup="true"
        aria-expanded={open}
        style={{ background: 'transparent', border: 'none', color: 'inherit' }}
      >
        {label}
      </button>
      {open && (
        <div className="dropdown" role="menu">
          {items.map((item, i) =>
            item.divider ? (
              <div key={i} className="dd-divider" role="separator" />
            ) : (
              <button
                key={i}
                className={`dd-item${item.disabled ? ' disabled' : ''}`}
                role="menuitem"
                disabled={item.disabled}
                onClick={() => {
                  if (item.disabled) return;
                  close();
                  if (item.href) window.open(item.href, '_blank');
                  else item.onClick?.();
                }}
                style={{ width: '100%', textAlign: 'left', background: 'transparent', border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
              >
                <span className="dd-label">{item.label}</span>
                {item.shortcut && <span className="dd-shortcut">{item.shortcut}</span>}
              </button>
            )
          )}
        </div>
      )}
    </div>
  );
}


/* ─── Keyboard shortcuts modal ───────────────────────────────────────────── */
function ShortcutsModal({ onClose }: { onClose: () => void }) {
  const SHORTCUTS = [
    { key: 'Space', action: 'Play / Pause' },
    { key: '← →', action: 'Step frame backward / forward' },
    { key: 'Home / End', action: 'Jump to first / last frame' },
    { key: '1 2 3 4', action: 'Set playback speed 0.5× 1× 2× 4×' },
    { key: 'A', action: 'All frames mode' },
    { key: 'O', action: 'Original only mode' },
    { key: 'I', action: 'AI generated only mode' },
    { key: 'M', action: 'Toggle metadata overlay' },
    { key: 'Esc', action: 'Close modal / Stop playback' },
  ];
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <div className="modal-title">Keyboard Shortcuts</div>
        <table className="shortcuts-table">
          <tbody>
            {SHORTCUTS.map(s => (
              <tr key={s.key}>
                <td><kbd className="kbd">{s.key}</kbd></td>
                <td className="kbd-action">{s.action}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <button className="modal-close" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}

/* ─── About modal ────────────────────────────────────────────────────────── */
function AboutModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <div className="modal-title">About AetherGIS</div>
        <div className="modal-body">
          <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--blue)', fontFamily: 'var(--cond)', letterSpacing: '0.04em', marginBottom: 8 }}>
            Aether<span style={{ color: 'var(--t1)' }}>GIS</span>
            <span style={{ fontSize: 12, fontWeight: 400, color: 'var(--t3)', marginLeft: 8 }}>v1.0.0</span>
          </div>
          <p>An AI-powered temporal super-resolution platform for satellite meteorological imagery. Bridges observational gaps in GEO/LEO satellite data using deep learning frame interpolation.</p>
          <p><strong>Interpolation Engine:</strong> Google FILM (Frame Interpolation for Large Motion)</p>
          <p><strong>Data Sources:</strong> NASA GIBS · ISRO MOSDAC (Phase 2) · EUMETSAT (Phase 3)</p>
          <p><strong>Supported Satellites:</strong> GOES-East/West, Himawari-9, MODIS Terra/Aqua, VIIRS S-NPP</p>
          <div className="modal-notice">
            AI-interpolated frames are synthetic approximations for qualitative temporal analysis only.
            Not for operational forecasting or scientific measurement.
          </div>
        </div>
        <button className="modal-close" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}

/* ─── Main MenuBar ───────────────────────────────────────────────────────── */
export default function MenuBar() {
  const {
    jobStatus, jobProgress, jobId, pipelineResult,
    setShowMetadataOverlay, showMetadataOverlay,
    setShowLowConfidence, showLowConfidence, setIsPlaying, bbox,
    setActivePanel, dataSource, setDataSource, sessionId,
  } = useStore();
  const { data: health, isError: healthError } = useHealth();
  const { data: auth } = useAuth();
  const { data: config } = useSystemConfig();
  const { theme, isDark, toggleTheme } = useDashboardTheme();
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [showAbout, setShowAbout] = useState(false);
  const [pendingDataSource, setPendingDataSource] = useState<'nasa_gibs' | 'isro_bhuvan' | 'insat' | null>(null);
  const [exportStatus, setExportStatus] = useState<Record<string, 'idle' | 'loading' | 'done'>>({});

  // filmLoaded: true if FILM or RIFE DL weights are actually loaded
  const filmLoaded = health?.film_model_loaded ?? health?.rife_model_loaded ?? false;
  // cpuFallback: backend explicitly signals LK optical flow mode, infra healthy
  const cpuFallback = health?.cpu_fallback_mode ?? false;
  const gpuOk = health?.gpu_available ?? false;
  const apiOnline = !healthError && !!health;
  const gpuLabel = health?.gpu_device_name?.replace('NVIDIA GeForce ', '') || (gpuOk ? 'Accelerated' : 'CPU-only');
  const userLabel = auth?.user || (config?.features.auth ? 'Secure user' : 'Local dev');
  const logoutLabel = config?.features.auth || config?.is_dev_preview ? 'Logout' : 'Leave Dashboard';

  const handleExport = async (type: 'original' | 'interpolated') => {
    if (!jobId) return;
    setExportStatus(s => ({ ...s, [type]: 'loading' }));
    try {
      const res = await triggerVideoExport(jobId, type);
      if (res.status === 'ready' && res.url) {
        const a = document.createElement('a');
        a.href = res.url;
        a.download = `aethergis_${type}_${jobId.slice(0, 8)}.mp4`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      }
      setExportStatus(s => ({ ...s, [type]: 'done' }));
    } catch {
      setExportStatus(s => ({ ...s, [type]: 'idle' }));
    }
  };

  const statusLabel: Record<string, string> = {
    idle: '● Pipeline ready',
    queued: '● Queued…',
    running: `◷ Processing ${jobProgress > 0 ? Math.round(jobProgress * 100) + '%' : '…'}`,
    completed: '● Pipeline complete',
    failed: '✕ Pipeline failed',
  };
  const statusCls: Record<string, string> = {
    idle: 'sb-ready', queued: 'sb-warn', running: 'sb-warn', completed: 'sb-ready', failed: '',
  };

  const handleDataSourceSelect = (src: 'nasa_gibs' | 'isro_bhuvan' | 'insat') => {
    if (jobStatus === 'running' || jobStatus === 'queued') {
      setPendingDataSource(src);
    } else {
      setDataSource(src);
    }
  };

  const handleLogout = async () => {
    try {
      await releaseSessionLock(sessionId);
    } catch (error) {
      console.warn('Session release failed during logout', error);
    } finally {
      window.location.assign(getLogoutUrl('/'));
    }
  };

  const fileItems: MenuItem[] = [
    {
      label: 'New Session',
      shortcut: 'Ctrl+N',
      onClick: () => {
        // Open Session Manager with the new-session intent
        window.dispatchEvent(new CustomEvent('aethergis:openSessionManager', { detail: { action: 'new' } }));
      },
    },
    { label: 'divider', divider: true },
    {
      label: exportStatus.interpolated === 'loading' ? 'Generating…' : 'Export MP4 (Enhanced)',
      shortcut: 'Ctrl+E',
      disabled: !jobId,
      onClick: () => handleExport('interpolated'),
    },
    {
      label: exportStatus.original === 'loading' ? 'Generating…' : 'Export MP4 (Original)',
      disabled: !jobId,
      onClick: () => handleExport('original'),
    },
    {
      label: 'Export JSON Metadata',
      disabled: !jobId,
      onClick: () => {
        if (jobId) window.open(`/api/v1/pipeline/${jobId}/metadata`, '_blank');
      },
    },
    { label: 'divider', divider: true },
    {
      label: 'Close App',
      onClick: () => window.close(),
    },
    { label: 'divider', divider: true },
    {
      label: logoutLabel,
      onClick: handleLogout,
    },
  ];

  const viewItems: MenuItem[] = [
    {
      label: `${showMetadataOverlay ? '✓' : '  '} Metadata Overlay`,
      shortcut: 'M',
      onClick: () => setShowMetadataOverlay(!showMetadataOverlay),
    },
    {
      label: `${showLowConfidence ? '✓' : '  '} Show Low-Confidence Frames`,
      onClick: () => setShowLowConfidence(!showLowConfidence),
    },
    {
      label: `${theme === 'dark' ? '✓' : '  '} Dark Mode`,
      onClick: () => {
        if (!isDark) toggleTheme();
      },
    },
    {
      label: `${theme === 'light' ? '✓' : '  '} Light Mode`,
      onClick: () => {
        if (isDark) toggleTheme();
      },
    },
    { label: 'divider', divider: true },
    {
      label: 'Zoom to AOI',
      disabled: !bbox,
      onClick: () => {
        // Dispatch custom event that MapViewer listens for
        window.dispatchEvent(new CustomEvent('aethergis:zoomToAoi'));
      },
    },
  ];

  const pipelineItems: MenuItem[] = [
    {
      label: 'Run Pipeline',
      shortcut: 'Ctrl+R',
      disabled: !bbox || jobStatus === 'running' || jobStatus === 'queued',
      onClick: () => {
        window.dispatchEvent(new CustomEvent('aethergis:runPipeline'));
      },
    },
    {
      label: 'Cancel Pipeline',
      disabled: jobStatus !== 'running' && jobStatus !== 'queued',
      onClick: () => {
        window.dispatchEvent(new CustomEvent('aethergis:cancelPipeline'));
      },
    },
    { label: 'divider', divider: true },
    {
      label: 'Pipeline Settings',
      onClick: () => setActivePanel('controls'),
    },
  ];

  const analysisItems: MenuItem[] = [
    {
      label: 'View Metrics',
      disabled: !pipelineResult,
      onClick: () => setActivePanel('analysis'),
    },
    {
      label: 'Export‥',
      disabled: !pipelineResult,
      onClick: () => setActivePanel('export'),
    },
    { label: 'divider', divider: true },
    {
      label: 'Pause Playback',
      disabled: !pipelineResult,
      onClick: () => setIsPlaying(false),
    },
  ];

  const toolsItems: MenuItem[] = [
    {
      label: 'Clear AOI',
      disabled: !bbox,
      onClick: () => {
        window.dispatchEvent(new CustomEvent('aethergis:clearAoi'));
      },
    },
    { label: 'divider', divider: true },
    {
      label: 'Keyboard Shortcuts',
      shortcut: '?',
      onClick: () => setShowShortcuts(true),
    },
  ];

  const helpItems: MenuItem[] = [
    {
      label: 'About AetherGIS',
      onClick: () => setShowAbout(true),
    },
    {
      label: 'Documentation',
      href: 'https://github.com/tejasbhor/AetherGIS#readme',
    },
    {
      label: 'Report Issue',
      href: 'https://github.com/tejasbhor/AetherGIS/issues',
    },
    { label: 'divider', divider: true },
    {
      label: 'Keyboard Shortcuts',
      onClick: () => setShowShortcuts(true),
    },
  ];

  return (
    <>
      <div className="menubar">
        <div className="app-name">
          Aether<span className="blue">GIS</span>{' '}
          <span style={{ fontWeight: 500, color: 'var(--blue)', fontSize: 11 }}>2.0.0</span>
        </div>

        <Menu label="File" items={fileItems} />
        <Menu label="View" items={viewItems} />
        <Menu label="Data Source" items={[
          { label: `${dataSource === 'nasa_gibs' ? '✓ ' : '  '}NASA GIBS`, onClick: () => handleDataSourceSelect('nasa_gibs') },
          { label: `${dataSource === 'insat' ? '✓ ' : '  '}ISRO MOSDAC`, onClick: () => handleDataSourceSelect('insat') },
          { label: `${dataSource === 'isro_bhuvan' ? '✓ ' : '  '}ISRO Bhuvan`, onClick: () => handleDataSourceSelect('isro_bhuvan') },
          { label: 'divider', divider: true },
          { label: 'Layer Browser', onClick: () => setActivePanel('controls') },
        ]} />
        <Menu label="Pipeline" items={pipelineItems} />
        <Menu label="Analysis" items={analysisItems} />
        <Menu label="Tools" items={toolsItems} />
        <Menu label="Help" items={helpItems} />

        <div className="menubar-right">
          <div className="user-pill" title={userLabel}>
            <span className="user-pill-dot" aria-hidden="true" />
            <span className="truncate">{userLabel}</span>
          </div>
          <div className="status-pill">
            <div className={`s-dot ${apiOnline ? (health?.redis_connected ? 'ok' : 'warn') : 'err'}`} />
            {dataSource === 'nasa_gibs' ? 'NASA GIBS' : dataSource === 'insat' ? 'MOSDAC' : 'BHUVAN'} {!apiOnline && <span style={{ color: 'var(--red)', fontWeight: 600 }}>(offline)</span>}
          </div>
          <div className="status-pill">
            <div className={`s-dot ${apiOnline && (filmLoaded || cpuFallback) ? (filmLoaded ? 'ok' : 'idle') : apiOnline ? 'warn' : 'err'}`} />
            FILM Engine{' '}
            {apiOnline
              ? filmLoaded
                ? null
                : cpuFallback
                  ? <span style={{ color: 'var(--t4)', fontWeight: 400 }}>(CPU fallback)</span>
                  : <span style={{ color: 'var(--orange)' }}>(unavailable)</span>
              : null
            }
          </div>
          <div className="status-pill">
            <div className={`s-dot ${gpuOk ? 'ok' : 'idle'}`} />
            GPU · {gpuLabel}
          </div>
          {jobStatus === 'running' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <div style={{ width: 120, height: 6, background: 'var(--b3)', borderRadius: 3, overflow: 'hidden' }}>
                <div style={{ height: '100%', background: 'var(--blue)', width: `${jobProgress * 100}%`, transition: 'width 0.4s ease-out' }} />
              </div>
            </div>
          )}
          <div className={`status-pill ${statusCls[jobStatus] || ''}`} style={{ 
            background: jobStatus === 'running' ? 'var(--blue-light-bg)' : undefined, 
            color: jobStatus === 'running' ? 'var(--blue)' : undefined,
            border: jobStatus === 'running' ? '1px solid var(--blue-shadow)' : undefined
          }}>
            {statusLabel[jobStatus]}
          </div>
          <button className="chrome-btn" onClick={toggleTheme} aria-label={`Switch to ${isDark ? 'light' : 'dark'} mode`}>
            {isDark ? 'Light' : 'Dark'}
          </button>
          <button className="chrome-btn chrome-btn-danger" onClick={handleLogout}>
            {logoutLabel}
          </button>
        </div>
      </div>

      {pendingDataSource && (
        <ConfirmDialog
          title="Stop Processing Data?"
          danger={true}
          message="Are you sure you want to change the data source? This will abandon your current processing job and wipe out the pipeline state immediately."
          warning="Active background nodes may be orphaned until garbage collection occurs."
          confirmLabel="Stop Processing"
          onConfirm={() => {
            setDataSource(pendingDataSource);
            setPendingDataSource(null);
          }}
          onCancel={() => setPendingDataSource(null)}
        />
      )}

      {showShortcuts && <ShortcutsModal onClose={() => setShowShortcuts(false)} />}
      {showAbout && <AboutModal onClose={() => setShowAbout(false)} />}
    </>
  );
}
