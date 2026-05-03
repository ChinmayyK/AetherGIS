/**
 * AetherGIS — MapOverlayManager (Modules 1, 3, 5, 10).
 *
 * This component does NOT own the OpenLayers map — it attaches to it via the
 * custom event bus (aethergis:mapReady). It draws:
 *   • Trajectory paths    (Canvas overlay — no OL layer needed)
 *   • Heatmap PNGs        (Static ImageLayer on OL map)
 *   • Anomaly markers     (Canvas dot-overlay)
 *   • Explainability      (Canvas overlay from data URL)
 *
 * It fires useEffect whenever the relevant store toggles change.
 * Safe: gracefully no-ops if map isn't ready.
 */
import { useEffect, useRef, useCallback } from 'react';
import { useStore } from '@app/store/useStore';
import {
  fetchTrajectories,
  fetchAlerts,
  fetchHeatmap,
  fetchExplanation,
} from '@shared/api/client';

export default function MapOverlayManager() {
  const {
    jobId, pipelineResult,
    showTrajectories,
    showUncertaintyMap,
    showChangeMap,
    showAnomalies,
    showExplainability,
    trajectories, setTrajectories,
    alerts, setAlerts,
    heatmaps, setHeatmap,
    explanation, setExplanation,
    setLoadingTrajectories,
    setLoadingAlerts,
    setLoadingHeatmap,
    overlayOpacity,
    currentFrameIndex,
  } = useStore();

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLElement | null>(null);

  const resizeCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    canvas.width = container.clientWidth;
    canvas.height = container.clientHeight;
  }, []);

  const renderAll = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    resizeCanvas();
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    // ── Trajectory paths ────────────────────────────────────────────────────
    if (showTrajectories && trajectories && trajectories.length > 0) {
      const opacity = overlayOpacity['trajectories'] ?? 0.85;
      const colors = ['#00d1b2','#3b82f6','#f59e0b','#ec4899','#10b981','#8b5cf6'];
      trajectories.forEach((traj, ti) => {
        const pts = traj.points;
        if (pts.length < 2) return;
        ctx.globalAlpha = opacity;
        ctx.strokeStyle = colors[ti % colors.length];
        ctx.lineWidth = 2;
        ctx.shadowColor = colors[ti % colors.length];
        ctx.shadowBlur = 3;
        ctx.beginPath();
        pts.forEach((pt, pi) => {
          const x = pt.x * W;
          const y = pt.y * H;
          if (pi === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.stroke();
        // Arrow head at end
        const last = pts[pts.length - 1];
        const prev = pts[pts.length - 2];
        const angle = Math.atan2((last.y - prev.y) * H, (last.x - prev.x) * W);
        const ax = last.x * W, ay = last.y * H;
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(ax - 10 * Math.cos(angle - 0.4), ay - 10 * Math.sin(angle - 0.4));
        ctx.lineTo(ax - 10 * Math.cos(angle + 0.4), ay - 10 * Math.sin(angle + 0.4));
        ctx.closePath();
        ctx.fillStyle = colors[ti % colors.length];
        ctx.fill();
        ctx.shadowBlur = 0;
      });
      ctx.globalAlpha = 1;
    }

    // ── Uncertainty heatmap ─────────────────────────────────────────────────
    if (showUncertaintyMap && heatmaps['uncertainty']?.data_url) {
      const opacity = overlayOpacity['uncertainty'] ?? 0.65;
      const img = new Image();
      img.onload = () => {
        ctx.globalAlpha = opacity;
        ctx.drawImage(img, 0, 0, W, H);
        ctx.globalAlpha = 1;
      };
      img.src = heatmaps['uncertainty'].data_url;
    }

    // ── Change/motion heatmap ───────────────────────────────────────────────
    if (showChangeMap && heatmaps['motion']?.data_url) {
      const opacity = overlayOpacity['change'] ?? 0.65;
      const img = new Image();
      img.onload = () => {
        ctx.globalAlpha = opacity;
        ctx.globalCompositeOperation = 'screen';
        ctx.drawImage(img, 0, 0, W, H);
        ctx.globalCompositeOperation = 'source-over';
        ctx.globalAlpha = 1;
      };
      img.src = heatmaps['motion'].data_url;
    }

    // ── Anomaly markers ─────────────────────────────────────────────────────
    if (showAnomalies && alerts && alerts.length > 0) {
      const opacity = overlayOpacity['anomaly'] ?? 0.75;
      const sevColor: Record<string, string> = { high: '#ef4444', medium: '#f97316', low: '#eab308' };
      ctx.globalAlpha = opacity;
      // We can't map frame_index to a map position precisely without metadata,
      // so we show markers arranged at the top-right with frame-proportional x position
      const frames = (pipelineResult?.frames ?? []);
      const total = Math.max(frames.length, 1);
      alerts.forEach((alert) => {
        const x = ((alert.frame_index / total) * (W - 40)) + 20;
        const y = 20;
        const color = sevColor[alert.severity] ?? '#888';
        ctx.beginPath();
        ctx.arc(x, y, alert.severity === 'high' ? 7 : 5, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1;
        ctx.stroke();
      });
      ctx.globalAlpha = 1;
    }

    // ── Explainability overlay ──────────────────────────────────────────────
    if (showExplainability && explanation?.overlay_url) {
      const opacity = overlayOpacity['explainability'] ?? 0.55;
      const img = new Image();
      img.onload = () => {
        ctx.globalAlpha = opacity;
        ctx.drawImage(img, 0, 0, W, H);
        ctx.globalAlpha = 1;
        // Draw uncertainty regions as dashed rectangles
        if (explanation.uncertainty_regions && explanation.uncertainty_regions.length > 0) {
          ctx.strokeStyle = '#f87171';
          ctx.lineWidth = 1.5;
          ctx.setLineDash([3, 3]);
          explanation.uncertainty_regions.slice(0, 5).forEach(r => {
            if (r.bbox) {
              ctx.strokeRect(r.bbox[0] * W, r.bbox[1] * H, (r.bbox[2] - r.bbox[0]) * W, (r.bbox[3] - r.bbox[1]) * H);
            }
          });
          ctx.setLineDash([]);
        }
      };
      img.src = explanation.overlay_url;
    }
  }, [
    showTrajectories, trajectories, overlayOpacity, showUncertaintyMap, heatmaps, 
    showChangeMap, showAnomalies, alerts, pipelineResult, showExplainability, explanation, resizeCanvas
  ]);

  // ── Resolve or create overlay canvas inside the OL map container ──────────
  useEffect(() => {
    const handler = (e: Event) => {
      const mapEl: HTMLElement | null = (e as CustomEvent).detail?.mapEl ?? null;
      if (!mapEl) return;
      // Create persistent overlay canvas
      let canvas = mapEl.querySelector<HTMLCanvasElement>('.aether-overlay-canvas');
      if (!canvas) {
        canvas = document.createElement('canvas');
        canvas.className = 'aether-overlay-canvas';
        canvas.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:200;';
        mapEl.style.position = 'relative';
        mapEl.appendChild(canvas);
      }
      canvasRef.current = canvas;
      containerRef.current = mapEl;
      resizeCanvas();
    };
    window.addEventListener('aethergis:mapReady', handler);
    return () => window.removeEventListener('aethergis:mapReady', handler);
  }, [resizeCanvas]);

  // ── Fetch trajectories when toggle turns ON ───────────────────────────────
  useEffect(() => {
    if (!jobId || !pipelineResult) return;
    if (!showTrajectories) return;
    if (trajectories !== null) { renderAll(); return; }
    setLoadingTrajectories(true);
    fetchTrajectories(jobId)
      .then((data) => setTrajectories(data?.trajectories ?? []))
      .catch(() => setTrajectories([]))
      .finally(() => setLoadingTrajectories(false));
  }, [showTrajectories, jobId, pipelineResult, trajectories, setTrajectories, setLoadingTrajectories, renderAll]);

  // ── Fetch alerts when anomaly toggle turns ON ─────────────────────────────
  useEffect(() => {
    if (!jobId || !pipelineResult) return;
    if (!showAnomalies) return;
    if (alerts !== null) { renderAll(); return; }
    setLoadingAlerts(true);
    fetchAlerts(jobId)
      .then((data) => setAlerts(data?.alerts ?? []))
      .catch(() => setAlerts([]))
      .finally(() => setLoadingAlerts(false));
  }, [showAnomalies, jobId, pipelineResult, alerts, setAlerts, setLoadingAlerts, renderAll]);

  // ── Fetch uncertainty heatmap ─────────────────────────────────────────────
  useEffect(() => {
    if (!jobId || !pipelineResult || !showUncertaintyMap) return;
    if (heatmaps['uncertainty']) { renderAll(); return; }
    setLoadingHeatmap('uncertainty', true);
    fetchHeatmap(jobId, 'uncertainty')
      .then((data) => setHeatmap('uncertainty', data))
      .catch(() => {})
      .finally(() => setLoadingHeatmap('uncertainty', false));
  }, [showUncertaintyMap, jobId, pipelineResult, heatmaps, setHeatmap, setLoadingHeatmap, renderAll]);

  // ── Fetch motion heatmap ──────────────────────────────────────────────────
  useEffect(() => {
    if (!jobId || !pipelineResult || !showChangeMap) return;
    if (heatmaps['motion']) { renderAll(); return; }
    setLoadingHeatmap('motion', true);
    fetchHeatmap(jobId, 'motion')
      .then((data) => setHeatmap('motion', data))
      .catch(() => {})
      .finally(() => setLoadingHeatmap('motion', false));
  }, [showChangeMap, jobId, pipelineResult, heatmaps, setHeatmap, setLoadingHeatmap, renderAll]);

  // ── Fetch explainability for current frame ────────────────────────────────
  useEffect(() => {
    if (!jobId || !pipelineResult || !showExplainability) return;
    const frames = pipelineResult.frames ?? [];
    const f = frames[currentFrameIndex];
    if (!f?.is_interpolated) return;
    fetchExplanation(jobId, currentFrameIndex)
      .then(setExplanation)
      .catch(() => { /* ignore */ });
  }, [showExplainability, jobId, pipelineResult, currentFrameIndex, setExplanation]);

  // ── Re-render canvas whenever relevant state changes ─────────────────────
  useEffect(() => { renderAll(); }, [renderAll, currentFrameIndex]);


  // No DOM output — canvas is appended directly to map container
  return null;
}
