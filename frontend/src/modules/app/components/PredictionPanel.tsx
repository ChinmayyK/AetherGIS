/**
 * AetherGIS — Prediction Panel (Module 2).
 * Shows AI-extrapolated future frames with clear LOW CONFIDENCE labelling.
 * Accessible when enablePrediction toggle is ON.
 */
import { useEffect } from 'react';
import { useStore } from '@app/store/useStore';
import { fetchPredictions } from '@shared/api/client';
import type { PredictionFrame } from '@app/store/useStore';

export default function PredictionPanel() {
  const {
    jobId, pipelineResult,
    enablePrediction,
    predictions, setPredictions,
    loadingPredictions, setLoadingPredictions,
    
  } = useStore();

  const jobCompleted = !!pipelineResult;

  useEffect(() => {
    if (!jobId || !jobCompleted || !enablePrediction) return;
    if (predictions !== null) return;
    setLoadingPredictions(true);
    fetchPredictions(jobId, 3, 10)
      .then((data) => setPredictions(data?.predictions ?? []))
      .catch(() => setPredictions([]))
      .finally(() => setLoadingPredictions(false));
  }, [jobId, jobCompleted, enablePrediction, predictions, setPredictions, setLoadingPredictions]);

  if (!enablePrediction) return null;

  return (
    <>
      <div className="section-hdr" style={{ marginTop: 0, borderTop: '1px solid var(--b3)' }}>
        Future Predictions
        <span style={{
          marginLeft: 4, fontFamily: 'var(--mono)', fontSize: 8, color: '#f472b6',
          background: 'rgba(244,114,182,0.1)', border: '1px solid rgba(244,114,182,0.3)',
          padding: '1px 5px', borderRadius: 3,
        }}>
          LOW CONFIDENCE
        </span>
        <span style={{ marginLeft: 'auto', fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--blue)' }}>Module 2</span>
      </div>

      <div style={{ padding: '4px 8px 6px', borderBottom: '1px solid var(--b2)' }}>
        {/* Disclaimer */}
        <div style={{
          background: 'rgba(244,114,182,0.07)', border: '1px solid rgba(244,114,182,0.25)',
          padding: '4px 7px', marginBottom: 6, borderRadius: 3,
        }}>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 8, color: '#f472b6', lineHeight: 1.5 }}>
            ⚠ EXPERIMENTAL — AI-extrapolated motion prediction. NOT for operational forecasting.
          </div>
        </div>

        {loadingPredictions && (
          <div style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--t4)', padding: '4px 0' }}>
            <span className="spinner" style={{ width: 8, height: 8, marginRight: 5 }} />
            Computing future trajectories…
          </div>
        )}

        {predictions && predictions.length === 0 && !loadingPredictions && (
          <div style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--t4)', fontStyle: 'italic' }}>
            Could not compute predictions — insufficient motion data.
          </div>
        )}

        {(predictions ?? []).map((p: PredictionFrame) => (
          <div
            key={p.step}
            style={{
              display: 'flex', gap: 8, padding: '5px 0',
              borderBottom: '1px solid var(--b2)',
              alignItems: 'flex-start',
            }}
          >
            {/* Thumbnail */}
            {p.data_url ? (
              <img
                src={p.data_url}
                alt={`Predicted +${p.minutes_ahead}m`}
                style={{
                  width: 52, height: 40, objectFit: 'cover',
                  border: '1px solid rgba(244,114,182,0.4)',
                  flexShrink: 0, borderRadius: 2,
                }}
              />
            ) : (
              <div style={{
                width: 52, height: 40, background: 'var(--b2)',
                border: '1px dashed var(--b3)', flexShrink: 0, borderRadius: 2,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontFamily: 'var(--mono)', fontSize: 7, color: 'var(--t4)',
              }}>
                N/A
              </div>
            )}

            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--cond)', fontSize: 9, fontWeight: 700, color: '#f472b6' }}>
                +{p.minutes_ahead} min
              </div>
              <div style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--t4)', lineHeight: 1.4 }}>
                {new Date(p.timestamp).toISOString().slice(11, 16)} UTC
              </div>
              <div style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--t3)', marginTop: 1 }}>
                Conf: <span style={{ color: p.confidence > 0.3 ? 'var(--orange)' : 'var(--red)' }}>
                  {(p.confidence * 100).toFixed(0)}%
                </span>
                {' · '}Δ ({p.motion_dx.toFixed(1)}, {p.motion_dy.toFixed(1)})px
              </div>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
