/**
 * AetherGIS — Temporal Consistency Checker Panel (Module 11).
 * Appended inside AnalysisPanel's metrics tab.
 */
import { useEffect } from 'react';
import { useStore } from '@app/store/useStore';
import { fetchTemporalConsistency } from '@shared/api/client';

export default function TemporalConsistencyPanel() {
  const {
    jobId, pipelineResult,
    consistencyIssues, setConsistencyIssues,
  } = useStore();

  const jobCompleted = !!pipelineResult;

  useEffect(() => {
    if (!jobId || !jobCompleted) return;
    if (consistencyIssues !== null) return;
    fetchTemporalConsistency(jobId)
      .then((data) => setConsistencyIssues(data?.issues ?? []))
      .catch(() => setConsistencyIssues([]));
  }, [jobId, jobCompleted, consistencyIssues, setConsistencyIssues]);

  if (!jobCompleted) return null;

  const issues = consistencyIssues ?? [];
  const high = issues.filter(i => i.severity === 'high').length;

  const SEV: Record<string, string> = { high: 'var(--red)', medium: 'var(--orange)', low: 'var(--t3)' };

  return (
    <>
      <div className="section-hdr">
        Temporal Consistency
        {consistencyIssues === null && (
          <span style={{ marginLeft: 4, fontSize: 8, color: 'var(--t4)' }}>⧗</span>
        )}
        {issues.length > 0 && (
          <span style={{
            marginLeft: 'auto', fontFamily: 'var(--mono)', fontSize: 8,
            color: high > 0 ? 'var(--red)' : 'var(--orange)',
            background: high > 0 ? 'var(--red-bg)' : 'var(--orng-bg)',
            border: `1px solid ${high > 0 ? 'var(--red-lt)' : 'var(--orng-lt)'}`,
            padding: '1px 5px', borderRadius: 3,
          }}>
            {issues.length} issues
          </span>
        )}
        {issues.length === 0 && consistencyIssues !== null && (
          <span style={{ marginLeft: 'auto', fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--green)' }}>✓ OK</span>
        )}
        <span style={{ marginLeft: 4, fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--blue)' }}>M11</span>
      </div>
      <div style={{ padding: '4px 0', borderBottom: '1px solid var(--b2)', maxHeight: 130, overflowY: 'auto' }}>
        {issues.length === 0 && consistencyIssues !== null && (
          <div style={{ padding: '4px 8px', fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--green)' }}>
            No unrealistic transitions detected.
          </div>
        )}
        {issues.map((iss, idx) => (
          <div
            key={idx}
            onClick={() => window.dispatchEvent(new CustomEvent('aethergis:seekToFrame', { detail: { frameIndex: iss.frame } }))}
            style={{
              display: 'flex', alignItems: 'center', gap: 6, padding: '3px 8px',
              borderBottom: '1px solid var(--b2)', cursor: 'pointer',
              borderLeft: `2px solid ${SEV[iss.severity] ?? 'var(--t4)'}`,
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--b2)')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
          >
            <span style={{ fontFamily: 'var(--mono)', fontSize: 8, color: SEV[iss.severity], width: 30 }}>
              #{iss.frame}
            </span>
            <span style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--t3)', flex: 1 }}>
              {iss.issue.replace(/_/g, ' ')}
            </span>
            <span style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--t4)' }}>
              MAD {iss.mad_score?.toFixed(3) ?? '—'}
            </span>
          </div>
        ))}
      </div>
    </>
  );
}
