/**
 * AetherGIS — Alerts Panel (Module 5).
 * Appended to the right dock (AnalysisPanel) — does NOT replace anything.
 * Each alert is clickable → dispatches aethergis:seekToFrame event.
 */
import { useEffect } from 'react';
import { useStore } from '@app/store/useStore';
import { fetchAlerts } from '@shared/api/client';

const SEV_COLOR: Record<string, string> = {
  high: 'var(--red)',
  medium: 'var(--orange)',
  low: 'var(--t3)',
};

const TYPE_ICON: Record<string, string> = {
  rapid_change: '⚡',
  strong_motion: '→',
  high_uncertainty: '⚠',
  temporal_anomaly: '⊘',
};

export default function AlertsPanel() {
  const {
    jobId, pipelineResult,
    alerts, setAlerts,
    loadingAlerts, setLoadingAlerts,
  } = useStore();

  const jobCompleted = !!pipelineResult;

  // Auto-fetch when job completes
  useEffect(() => {
    if (!jobId || !jobCompleted) return;
    if (alerts !== null) return; // already fetched
    setLoadingAlerts(true);
    fetchAlerts(jobId)
      .then((data) => setAlerts(data?.alerts ?? []))
      .catch(() => setAlerts([]))
      .finally(() => setLoadingAlerts(false));
  }, [jobId, jobCompleted, alerts, setAlerts, setLoadingAlerts]);

  const handleAlertClick = (frameIndex: number) => {
    window.dispatchEvent(new CustomEvent('aethergis:seekToFrame', { detail: { frameIndex } }));
  };

  return (
    <>
      <div className="section-hdr" style={{ marginTop: 0 }}>
        Alerts
        {loadingAlerts && (
          <span style={{ marginLeft: 4, fontSize: 8, color: 'var(--t4)' }}>⧗ loading…</span>
        )}
        {alerts && alerts.length > 0 && (
          <span style={{
            marginLeft: 'auto', fontFamily: 'var(--mono)', fontSize: 8,
            color: alerts.some(a => a.severity === 'high') ? 'var(--red)' : 'var(--orange)',
            background: alerts.some(a => a.severity === 'high') ? 'var(--red-bg)' : 'var(--orng-bg)',
            border: `1px solid ${alerts.some(a => a.severity === 'high') ? 'var(--red-lt)' : 'var(--orng-lt)'}`,
            padding: '1px 5px', borderRadius: 3,
          }}>
            {alerts.filter(a => a.severity === 'high').length > 0
              ? `${alerts.filter(a => a.severity === 'high').length} HIGH`
              : `${alerts.length} alerts`}
          </span>
        )}
        {alerts && alerts.length === 0 && !loadingAlerts && (
          <span style={{ marginLeft: 'auto', fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--green)' }}>✓ NONE</span>
        )}
      </div>
      <div style={{ padding: '0 0 4px 0', maxHeight: 200, overflowY: 'auto' }}>
        {!jobCompleted && (
          <div style={{ padding: '6px 8px', fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--t4)', fontStyle: 'italic' }}>
            Run pipeline to detect alerts.
          </div>
        )}
        {jobCompleted && loadingAlerts && (
          <div style={{ padding: '6px 8px', fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--t4)' }}>
            Scanning for alerts…
          </div>
        )}
        {alerts && alerts.length === 0 && !loadingAlerts && (
          <div style={{ padding: '6px 8px', fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--green)' }}>
            No significant alerts detected in this run.
          </div>
        )}
        {(alerts ?? []).map((alert) => (
          <div
            key={alert.id}
            onClick={() => handleAlertClick(alert.frame_index)}
            style={{
              display: 'flex', gap: 6, padding: '4px 8px', cursor: 'pointer',
              borderBottom: '1px solid var(--b2)',
              borderLeft: `3px solid ${SEV_COLOR[alert.severity] ?? 'var(--t4)'}`,
              background: 'var(--b1)',
              transition: 'background 0.1s',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--b2)')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--b1)')}
          >
            <div style={{ flexShrink: 0, fontFamily: 'var(--mono)', fontSize: 11, color: SEV_COLOR[alert.severity] ?? 'var(--t4)', lineHeight: 1.3 }}>
              {TYPE_ICON[alert.type] ?? '●'}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--cond)', fontSize: 9, fontWeight: 600, color: SEV_COLOR[alert.severity] ?? 'var(--t2)', textTransform: 'uppercase' }}>
                {alert.type.replace(/_/g, ' ')}
                <span style={{ color: 'var(--t4)', fontWeight: 400, marginLeft: 4 }}>·</span>
                <span style={{ color: 'var(--t4)', fontWeight: 400, marginLeft: 4 }}>frame {alert.frame_index}</span>
              </div>
              <div style={{ fontFamily: 'var(--mono)', fontSize: 8.5, color: 'var(--t3)', lineHeight: 1.4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {alert.description}
              </div>
            </div>
            <div style={{ flexShrink: 0, fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--t4)', alignSelf: 'center' }}>
              ↗
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
