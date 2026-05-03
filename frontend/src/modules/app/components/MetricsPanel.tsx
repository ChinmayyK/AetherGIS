/**
 * AetherGIS — System Metrics Panel (Modules 6, 14).
 * Shows PSNR trend, SSIM trend, confidence stability, and time-series analytics.
 * Appended to AnalysisPanel — does NOT replace existing content.
 */
import { useEffect } from 'react';
import { useStore } from '@app/store/useStore';
import { fetchMetricEvolution, fetchTimeSeries } from '@shared/api/client';

interface MiniSparkProps {
  data: { value: number }[];
  color?: string;
  width?: number;
  height?: number;
}

function MiniSpark({ data, color = 'var(--blue)', width = 120, height = 28 }: MiniSparkProps) {
  if (!data || data.length < 2) {
    return (
      <svg viewBox={`0 0 ${width} ${height}`} style={{ width, height }}>
        <text x="4" y={height / 2 + 4} fill="var(--t4)" fontSize="7" fontFamily="monospace">no data</text>
      </svg>
    );
  }
  const vals = data.map(d => d.value);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  const pad = 4;

  const pts = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * (width - pad * 2) + pad;
    const y = height - pad - ((v - min) / range) * (height - pad * 2);
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');

  return (
    <svg viewBox={`0 0 ${width} ${height}`} fill="none" style={{ width, height }}>
      <path d={pts + ` L${width - pad},${height} L${pad},${height}Z`} fill={color} fillOpacity={0.12} />
      <path d={pts} stroke={color} strokeWidth="1.2" strokeLinejoin="round" />
    </svg>
  );
}

export default function MetricsPanel() {
  const {
    jobId, pipelineResult,
    metricEvolution, setMetricEvolution,
    timeSeries, setTimeSeries,
  } = useStore();

  const jobCompleted = !!pipelineResult;

  useEffect(() => {
    if (!jobId || !jobCompleted) return;
    if (!metricEvolution) {
      fetchMetricEvolution(jobId)
        .then(setMetricEvolution)
        .catch(() => setMetricEvolution(null));
    }
    if (!timeSeries) {
      fetchTimeSeries(jobId)
        .then(setTimeSeries)
        .catch(() => setTimeSeries(null));
    }
  }, [jobId, jobCompleted, metricEvolution, setMetricEvolution, timeSeries, setTimeSeries]);

  if (!jobCompleted) return null;

  const evo = metricEvolution;
  const ts = timeSeries;

  return (
    <>
      <div className="section-hdr" style={{ marginTop: 0 }}>
        Metric Evolution
        <span style={{ marginLeft: 'auto', fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--blue)' }}>Module 14</span>
      </div>
      <div style={{ padding: '6px 8px', borderBottom: '1px solid var(--b2)' }}>
        {!evo ? (
          <div style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--t4)' }}>Loading…</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {evo.psnr_trend?.length > 0 && (
              <div>
                <div style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--t4)', marginBottom: 2 }}>
                  PSNR trend · avg: <span style={{ color: 'var(--blue)' }}>{evo.avg_psnr?.toFixed(1) ?? '—'} dB</span>
                </div>
                <MiniSpark data={evo.psnr_trend} color="var(--blue)" width={230} height={28} />
              </div>
            )}
            {evo.ssim_trend?.length > 0 && (
              <div>
                <div style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--t4)', marginBottom: 2 }}>
                  SSIM trend · avg: <span style={{ color: 'var(--green)' }}>{evo.avg_ssim?.toFixed(3) ?? '—'}</span>
                </div>
                <MiniSpark data={evo.ssim_trend} color="var(--green)" width={230} height={24} />
              </div>
            )}
            {evo.confidence_trend?.length > 0 && (
              <div>
                <div style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--t4)', marginBottom: 2 }}>
                  Confidence · stability σ: <span style={{ color: 'var(--teal)' }}>{evo.confidence_stability?.toFixed(4) ?? '—'}</span>
                </div>
                <MiniSpark data={evo.confidence_trend} color="var(--teal)" width={230} height={22} />
              </div>
            )}
            {!evo.psnr_trend?.length && !evo.ssim_trend?.length && (
              <div style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--t4)', fontStyle: 'italic' }}>
                No per-frame PSNR/SSIM available for this job.
              </div>
            )}
          </div>
        )}
      </div>

      <div className="section-hdr">
        Time-Series Analytics
        <span style={{ marginLeft: 'auto', fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--blue)' }}>Module 6</span>
      </div>
      <div style={{ padding: '6px 8px', borderBottom: '1px solid var(--b2)' }}>
        {!ts ? (
          <div style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--t4)' }}>Loading…</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {ts.brightness_trend?.length > 0 && (
              <div>
                <div style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--t4)', marginBottom: 2 }}>
                  Brightness · mean: <span style={{ color: 'var(--orange)' }}>{ts.mean_brightness?.toFixed(3) ?? '—'}</span>
                </div>
                <MiniSpark data={ts.brightness_trend} color="var(--orange)" width={230} height={22} />
              </div>
            )}
            {ts.motion_trend?.length > 0 && (
              <div>
                <div style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--t4)', marginBottom: 2 }}>
                  Motion intensity · change rate: <span style={{ color: '#a78bfa' }}>{ts.change_rate?.toFixed(5) ?? '—'}</span>
                </div>
                <MiniSpark data={ts.motion_trend} color="#a78bfa" width={230} height={22} />
              </div>
            )}
            {ts.coverage_trend?.length > 0 && (
              <div>
                <div style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--t4)', marginBottom: 2 }}>
                  Cloud coverage · mean: <span style={{ color: '#38bdf8' }}>{ts.mean_coverage !== null ? `${(ts.mean_coverage * 100).toFixed(1)}%` : '—'}</span>
                </div>
                <MiniSpark data={ts.coverage_trend} color="#38bdf8" width={230} height={22} />
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
