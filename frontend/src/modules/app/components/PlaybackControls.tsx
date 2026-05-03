/**
 * AetherGIS — PlaybackControls (map-view transport bar)
 * Uses the centralized PlaybackEngine + store actions. NO setInterval here.
 */
import { useStore, type PlaybackMode } from '@app/store/useStore';

export default function PlaybackControls() {
  const {
    pipelineResult,
    currentFrameIndex,
    setCurrentFrameIndex,
    isPlaying,
    setIsPlaying,
    playbackSpeed,
    setPlaybackSpeed,
    playbackMode,
    setPlaybackMode,
    getNextFrameIndex,
    seekToStart,
    seekToEnd,
  } = useStore();

  const frames = pipelineResult?.frames ?? [];
  const totalFrames = frames.length;
  const currentMeta = frames[currentFrameIndex];
  const hasResult = totalFrames > 0;

  // Delegate all navigation to store actions (stale-closure-safe)
  const handlePlayPause = () => {
    if (!isPlaying && getNextFrameIndex(currentFrameIndex, 1) === null) seekToStart();
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

  const canBack = hasResult && getNextFrameIndex(currentFrameIndex, -1) !== null;
  const canFwd  = hasResult && getNextFrameIndex(currentFrameIndex, 1) !== null;

  const iconBtn = (label: string, onClick: () => void, disabled = false, active = false) => (
    <button
      className={`btn btn-ghost btn-icon${active ? ' active' : ''}`}
      onClick={onClick}
      disabled={disabled}
      title={label}
      style={{ fontSize: 14 }}
    >
      {label}
    </button>
  );

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      width: '100%',
      padding: '0 4px',
    }}>
      {/* Frame counter */}
      <div style={{ fontSize: 11, fontFamily: "'JetBrains Mono', monospace", color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
        F{currentFrameIndex + 1}/{totalFrames || 0}
      </div>

      {/* Transport controls */}
      <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
        {iconBtn('⏮', () => { setIsPlaying(false); seekToStart(); }, !hasResult)}
        {iconBtn('⏪', handleStepBack, !canBack)}
        <button
          className="btn btn-primary btn-icon"
          onClick={handlePlayPause}
          disabled={!hasResult}
          style={{ fontSize: 14, width: 36, height: 36 }}
        >
          {isPlaying ? '⏸' : '▶'}
        </button>
        {iconBtn('⏩', handleStepForward, !canFwd)}
        {iconBtn('⏭', () => { setIsPlaying(false); seekToEnd(); }, !hasResult)}
      </div>

      {/* Speed selector */}
      <div style={{ display: 'flex', gap: 3 }}>
        {([0.5, 1, 2, 4] as const).map((s) => (
          <button
            key={s}
            className={`btn btn-ghost${playbackSpeed === s ? ' active' : ''}`}
            style={{ padding: '4px 8px', fontSize: 11, minWidth: 32 }}
            onClick={() => setPlaybackSpeed(s)}
          >
            {s}×
          </button>
        ))}
      </div>

      {/* Timestamp display */}
      {currentMeta && (
        <div style={{
          fontSize: 11,
          fontFamily: "'JetBrains Mono', monospace",
          color: currentMeta.is_interpolated ? 'var(--conf-medium)' : 'var(--conf-observed)',
          whiteSpace: 'nowrap',
          flex: 1,
          textAlign: 'center',
        }}>
          {new Date(currentMeta.timestamp).toISOString().slice(0, 16).replace('T', ' ')} UTC
          {currentMeta.is_interpolated ? ' · AI' : (currentMeta.provider_source ? ` · [${currentMeta.provider_source.toUpperCase()}]` : '')}
        </div>
      )}

      {/* Toggles */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <select
          value={playbackMode}
          onChange={(e) => { setIsPlaying(false); setPlaybackMode(e.target.value as PlaybackMode); }}
          style={{ background: 'var(--bg2)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 4, fontSize: 11, padding: '4px 8px' }}
        >
          <option value="all">All Frames</option>
          <option value="original">Original Only</option>
          <option value="interpolated">AI Generated</option>
        </select>

      </div>
    </div>
  );
}
