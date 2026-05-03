/**
 * AetherGIS — TimelineScrubber (QGIS-style dock)
 * PRD-aligned: uses centralized store playback engine, correct frame-index
 * mapping, gap indicators, confidence colouring, and stats with PRD KPIs.
 *
 * NO setInterval here — PlaybackEngine (App.tsx) is the single driver.
 */
import type { FrameMetadata, PlaybackMode } from '@app/store/useStore';
import { useStore } from '@app/store/useStore';

/** PRD §11.4 — frame tick type based on confidence class and rejection status */
function getTickClass(frame: FrameMetadata): 'real' | 'ai-h' | 'ai-m' | 'ai-l' | 'rejected' {
  if (!frame.is_interpolated) return 'real';
  const cls = frame.confidence_class;
  if (!cls) return 'ai-h';
  if (cls === 'REJECTED') return 'rejected';
  if (cls === 'HIGH') return 'ai-h';
  if (cls === 'MEDIUM') return 'ai-m';
  if (cls === 'LOW') return 'ai-l';
  return 'ai-h';
}

const TICK_TITLE: Record<string, string> = {
  real: 'Observed (real satellite data)',
  'ai-h': 'AI-Generated · High Confidence',
  'ai-m': 'AI-Generated · Medium Confidence',
  'ai-l': 'AI-Generated · Low Confidence',
  rejected: 'Rejected — Flow inconsistency too high (no frame generated)',
};

function fmt(ts: string) {
  return new Date(ts).toISOString().slice(11, 16);
}

// ─── Empty state ──────────────────────────────────────────────────────────────
function EmptyTrack() {
  return (
    <div style={{
      flex: 1,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      fontFamily: 'var(--mono)',
      fontSize: 9,
      color: 'var(--t4)',
      fontStyle: 'italic',
      letterSpacing: '0.03em',
      gap: 8,
    }}>
      <span>No frames loaded</span>
      <span style={{ color: 'var(--b1)' }}>|</span>
      <span>Select AOI → Set time range → Run Pipeline</span>
    </div>
  );
}

export default function TimelineScrubber() {
  const {
    pipelineResult,
    currentFrameIndex,
    setCurrentFrameIndex,
    isPlaying,
    setIsPlaying,
    playbackSpeed,
    setPlaybackSpeed,
    showLowConfidence,
    setShowLowConfidence,
    playbackMode,
    setPlaybackMode,
    getNextFrameIndex,
    seekToStart,
    seekToEnd,
    enablePrediction,
    predictions,
  } = useStore();

  // All raw frames from the result
  const allFrames = pipelineResult?.frames ?? [];
  const metrics = pipelineResult?.metrics;

  // The "visible" frame list respects playbackMode + showLowConfidence.
  // We still RENDER all ticks (dimmed when excluded from playback).
  // The currentFrameIndex is always an index into allFrames (not filtered list).
  const visibleCount = allFrames.filter((f): boolean => {
    if (f.confidence_class === 'REJECTED') return false;
    if (!showLowConfidence && f.confidence_class === 'LOW' && f.is_interpolated) return false;
    if (playbackMode === 'original') return !f.is_interpolated;
    if (playbackMode === 'interpolated') return f.is_interpolated;
    return true;
  }).length;

  const currentMeta = allFrames[currentFrameIndex];
  const rejectedCount = allFrames.filter(f => f.confidence_class === 'REJECTED').length;

  // Transport handlers — all index math goes through store actions
  const handlePlayPause = () => {
    if (!isPlaying) {
      // If at the end, restart from the beginning
      if (getNextFrameIndex(currentFrameIndex, 1) === null) seekToStart();
    }
    setIsPlaying(!isPlaying);
  };

  const handleStepBack = () => {
    setIsPlaying(false);
    const prev = getNextFrameIndex(currentFrameIndex, -1);
    if (prev !== null) setCurrentFrameIndex(prev);
  };

  const handleStepForward = () => {
    setIsPlaying(false);
    const next = getNextFrameIndex(currentFrameIndex, 1);
    if (next !== null) setCurrentFrameIndex(next);
  };

  const handleFirst = () => { setIsPlaying(false); seekToStart(); };
  const handleLast  = () => { setIsPlaying(false); seekToEnd(); };

  // Time ruler (5 evenly-spaced labels)
  let rulerLabels: string[] = [];
  if (allFrames.length >= 2) {
    const t0 = new Date(allFrames[0].timestamp).getTime();
    const t1 = new Date(allFrames[allFrames.length - 1].timestamp).getTime();
    rulerLabels = [0, 0.25, 0.5, 0.75, 1].map(p =>
      new Date(t0 + p * (t1 - t0)).toISOString().slice(11, 16)
    );
  }

  const hasFrames = allFrames.length > 0;
  const canBack = hasFrames && getNextFrameIndex(currentFrameIndex, -1) !== null;
  const canFwd  = hasFrames && getNextFrameIndex(currentFrameIndex, 1) !== null;

  // Visible frame count for display (mode-filtered)
  const modeLabel = playbackMode === 'original' ? 'Observed' : playbackMode === 'interpolated' ? 'AI' : 'All';

  return (
    <div className="timeline-dock">
      {/* Transport header */}
      <div className="tl-header">
        <button className="pb-btn" title="First frame" disabled={!hasFrames} onClick={handleFirst}>⏮</button>
        <button className="pb-btn" title="Step back" disabled={!canBack} onClick={handleStepBack}>◀</button>
        <button
          className="pb-btn pb-play"
          disabled={!hasFrames}
          onClick={handlePlayPause}
          title={isPlaying ? 'Pause' : 'Play'}
        >
          {isPlaying ? '⏸' : '▶'}
        </button>
        <button className="pb-btn" title="Step forward" disabled={!canFwd} onClick={handleStepForward}>▶</button>
        <button className="pb-btn" title="Last frame" disabled={!hasFrames} onClick={handleLast}>⏭</button>

        <select
          className="speed-select"
          value={playbackSpeed}
          onChange={e => setPlaybackSpeed(Number(e.target.value) as 0.5 | 1 | 2 | 4)}
        >
          <option value={0.5}>0.5×</option>
          <option value={1}>1×</option>
          <option value={2}>2×</option>
          <option value={4}>4×</option>
        </select>

         <select
           className="speed-select"
           value={playbackMode}
           onChange={e => { setIsPlaying(false); setPlaybackMode(e.target.value as PlaybackMode); }}
           style={{ width: 110, marginLeft: 4 }}
         >
          <option value="all">All Frames</option>
          <option value="original">Original Only</option>
          <option value="interpolated">AI Generated</option>
        </select>

        {/* Current frame timestamp */}
        <div className="tl-time-display">
          {currentMeta ? (
            <>
              <span style={{ color: 'var(--t3)', fontSize: 9 }}>
                {new Date(currentMeta.timestamp).toISOString().slice(0, 10)}
              </span>
              {' '}
              <strong>{fmt(currentMeta.timestamp)}</strong>
              <span style={{ color: 'var(--t3)', fontSize: 9 }}> UTC</span>
              {'  ·  '}
              <span style={{ color: 'var(--t3)', fontSize: 9 }}>
                {currentFrameIndex + 1}/{allFrames.length}
                {' '}
                <span style={{ color: 'var(--blue)', fontSize: 8 }}>({modeLabel}: {visibleCount})</span>
              </span>
            </>
          ) : (
            <span style={{ color: 'var(--t4)', fontSize: 10 }}>No frames loaded</span>
          )}
        </div>

        {/* Low-Confidence toggle (PRD §6.6.3) */}
        <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--t3)', cursor: 'pointer', marginLeft: 4 }}>
          <input
            type="checkbox"
            checked={showLowConfidence}
            onChange={() => setShowLowConfidence(!showLowConfidence)}
            style={{ width: 11, height: 11, accentColor: 'var(--red)' }}
          />
          Low Conf
        </label>

        {/* Stats bar */}
        {metrics && (
          <div className="tl-stats-bar">
            <div className="tl-stat">
              <div className="tl-stat-v blue">
                {metrics.interpolated_frames > 0 && metrics.observed_frames > 0
                  ? `${Math.round(metrics.total_frames / metrics.observed_frames)}×`
                  : '—'}
              </div>
              <div className="tl-stat-k">Temp Res</div>
            </div>
            <div className="tl-stat">
              <div className={`tl-stat-v ${(metrics.avg_psnr ?? 0) >= 28 ? 'green' : 'orange'}`}>
                {metrics.avg_psnr?.toFixed(1) ?? '—'}
              </div>
              <div className="tl-stat-k">PSNR dB</div>
            </div>
            <div className="tl-stat">
              <div className={`tl-stat-v ${(metrics.avg_ssim ?? 0) >= 0.85 ? 'green' : 'orange'}`}>
                {metrics.avg_ssim?.toFixed(2) ?? '—'}
              </div>
              <div className="tl-stat-k">SSIM</div>
            </div>
            <div className="tl-stat">
              <div className={`tl-stat-v ${(metrics.tcs ?? 0) >= 0.75 ? 'green' : 'orange'}`}>
                {metrics.tcs?.toFixed(2) ?? '—'}
              </div>
              <div className="tl-stat-k">TCS</div>
            </div>
            {rejectedCount > 0 && (
              <div className="tl-stat" title="Frames rejected by optical flow validation (PRD §11.3)">
                <div className="tl-stat-v red">{rejectedCount}</div>
                <div className="tl-stat-k">Rejected</div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Track body */}
      <div className="tl-body">
        {/* Time ruler */}
        {rulerLabels.length > 0 && (
          <div className="tl-ruler">
            {rulerLabels.map((l, i) => <span key={i}>{l}</span>)}
          </div>
        )}

        {/* Frame track — ALL frames rendered; playback-excluded ones dimmed */}
        <div className="tl-track">
          {allFrames.length === 0 ? (
            <EmptyTrack />
          ) : allFrames.map((frame) => {
            const type = getTickClass(frame);
            const idx = frame.frame_index;
            const isSelected = idx === currentFrameIndex;

            let dimOpacity = 1;
            if (playbackMode === 'original' && frame.is_interpolated) dimOpacity = 0.18;
            if (playbackMode === 'interpolated' && !frame.is_interpolated) dimOpacity = 0.18;
            if (!showLowConfidence && frame.confidence_class === 'LOW' && frame.is_interpolated) dimOpacity = 0.12;

            return (
              <div
                key={idx}
                className={`f-tick ${type}${isSelected ? ' sel' : ''}${type === 'rejected' ? ' f-tick-rejected' : ''}`}
                style={{ opacity: dimOpacity }}
                title={`${TICK_TITLE[type]} · ${fmt(frame.timestamp)} UTC · Frame ${idx + 1}`}
                onClick={() => {
                  setIsPlaying(false);
                  setCurrentFrameIndex(idx);
                }}
              >
                {type === 'real'   && <div className="f-tick-lbl">R</div>}
                {type === 'ai-m'  && <div className="f-tick-lbl">~</div>}
                {type === 'ai-l'  && <div className="f-tick-lbl">!</div>}
                {type === 'rejected' && <div className="f-tick-lbl" style={{ color: 'var(--t2)' }}>✕</div>}
              </div>
            );
          })}
          {/* Predicted frame ticks (Module 2) — append after observed frames */}
          {enablePrediction && predictions && predictions.length > 0 && (
            <>
              <div style={{
                width: 1, height: '100%', background: '#f472b680', alignSelf: 'stretch',
                flexShrink: 0, margin: '0 2px',
              }} title="Prediction boundary" />
              {predictions.map((p) => (
                <div
                  key={`pred-${p.step}`}
                  className="f-tick"
                  style={{
                    background: 'rgba(244,114,182,0.15)',
                    borderColor: 'rgba(244,114,182,0.5)',
                    opacity: p.confidence,
                    cursor: 'default',
                    position: 'relative',
                  }}
                  title={`PREDICTED +${p.minutes_ahead}min · ${new Date(p.timestamp).toISOString().slice(11,16)} UTC · Conf ${(p.confidence*100).toFixed(0)}%`}
                >
                  <div className="f-tick-lbl" style={{ color: '#f472b6', fontSize: 6 }}>P</div>
                </div>
              ))}
            </>
          )}
        </div>

        {/* Footer: legend + KPI summary */}
        <div className="tl-footer">
          <div className="tl-legend">
            <div className="leg">
              <div className="leg-sw" style={{ background: 'rgba(32,122,48,0.5)', borderColor: 'rgba(32,122,48,0.8)' }} />
              Observed
            </div>
            <div className="leg">
              <div className="leg-sw" style={{ background: 'rgba(26,95,168,0.3)', borderColor: 'rgba(26,95,168,0.5)' }} />
              AI · High
            </div>
            <div className="leg">
              <div className="leg-sw" style={{ background: 'rgba(192,96,16,0.3)', borderColor: 'rgba(192,96,16,0.5)' }} />
              AI · Med
            </div>
            <div className="leg">
              <div className="leg-sw" style={{ background: 'rgba(184,32,32,0.15)', borderColor: 'rgba(184,32,32,0.35)' }} />
              AI · Low
            </div>
            <div className="leg">
              <div className="leg-sw" style={{ background: 'repeating-linear-gradient(45deg,var(--b3),var(--b3) 2px,var(--b2) 2px,var(--b2) 4px)', borderColor: 'var(--b1)' }} />
              Rejected
            </div>
            {enablePrediction && predictions && predictions.length > 0 && (
              <div className="leg">
                <div className="leg-sw" style={{ background: 'rgba(244,114,182,0.2)', borderColor: 'rgba(244,114,182,0.5)' }} />
                <span style={{ color: '#f472b6' }}>Predicted</span>
              </div>
            )}
          </div>
          {metrics && (
            <div className="tl-scores">
              TCS <strong>{metrics.tcs?.toFixed(2) ?? '—'}</strong>
              {' '}·{' '}
              FSI <strong>{metrics.fsi?.toFixed(2) ?? '—'}</strong>
              {' '}·{' '}
              {metrics.observed_frames} obs / {metrics.interpolated_frames} AI
              {rejectedCount > 0 && <span style={{ color: 'var(--red)' }}> / {rejectedCount} rejected</span>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
