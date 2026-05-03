"""AetherGIS — Auto-Report Generator (MODULE 15).

This module generates comprehensive, NASA-level technical reports for completed
interpolation pipeline jobs. Reports include full traceability of inputs,
execution parameters, quality metrics, and output artifacts.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


def _metric_badge(value: Optional[float], good: float, ok: float, unit: str = "") -> str:
    """Generate a styled badge for metric values with color coding."""
    if value is None:
        return "<span class='badge badge-na'>N/A</span>"
    if value >= good:
        color = "#207a30"
        bg = "#e8f6ec"
    elif value >= ok:
        color = "#c06010"
        bg = "#fdf0e0"
    else:
        color = "#b82020"
        bg = "#fde8e8"
    return f"<span class='badge' style='color: {color}; background: {bg}; border: 1px solid {color}33;'>{value:.3f}{unit}</span>"


def _sev_badge(severity: str) -> str:
    """Generate severity badge (HIGH/MEDIUM/LOW)."""
    s = str(severity).lower()
    if s in ["high", "critical"]:
        return "<span class='badge' style='color: #b82020; background: #fde8e8; border: 1px solid rgba(184,32,32,0.2);'>HIGH</span>"
    elif s == "medium":
        return "<span class='badge' style='color: #c06010; background: #fdf0e0; border: 1px solid rgba(192,96,16,0.2);'>MEDIUM</span>"
    return "<span class='badge' style='color: #207a30; background: #e8f6ec; border: 1px solid rgba(32,122,48,0.2);'>LOW</span>"


def _conf_badge(confidence: str) -> str:
    """Generate confidence class badge."""
    c = str(confidence).lower()
    if c == "high":
        return "<span class='badge badge-conf-high'>HIGH</span>"
    elif c == "medium":
        return "<span class='badge badge-conf-med'>MEDIUM</span>"
    elif c == "low":
        return "<span class='badge badge-conf-low'>LOW</span>"
    return "<span class='badge badge-na'>UNCLASSIFIED</span>"


def _format_datetime(dt: Optional[str]) -> str:
    """Format datetime string for display."""
    if not dt:
        return "—"
    try:
        if isinstance(dt, str):
            # Try to parse and reformat
            parsed = datetime.fromisoformat(dt.replace('Z', '+00:00'))
            return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")
        elif isinstance(dt, datetime):
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        pass
    return str(dt)[:19]


def _safe_get(obj: dict, *keys, default="—") -> Any:
    """Safely get nested dictionary value."""
    current = obj
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current if current is not None else default


def _calculate_duration(created_at: Optional[Any], completed_at: Optional[Any]) -> str:
    """Calculate execution duration between timestamps."""
    if not created_at or not completed_at:
        return "—"
    try:
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        if isinstance(completed_at, str):
            completed_at = datetime.fromisoformat(completed_at.replace('Z', '+00:00'))
        duration = completed_at - created_at
        total_seconds = int(duration.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    except Exception:
        return "—"


def _generate_frame_breakdown(frames: list) -> dict:
    """Analyze frame composition and return statistics."""
    if not frames:
        return {
            "observed": 0, "interpolated": 0, "total": 0,
            "by_model": {}, "by_confidence": {}, "by_gap": {}
        }
    
    observed = sum(1 for f in frames if f.get("is_original", False))
    interpolated = sum(1 for f in frames if not f.get("is_original", False))
    
    # Model distribution
    by_model = {}
    for f in frames:
        model = f.get("model_used") or "Unknown"
        by_model[model] = by_model.get(model, 0) + 1
    
    # Confidence distribution
    by_confidence = {"high": 0, "medium": 0, "low": 0, "rejected": 0}
    for f in frames:
        conf = (f.get("confidence_class") or "unknown").lower()
        if conf in by_confidence:
            by_confidence[conf] += 1
    
    # Gap category distribution
    by_gap = {"none": 0, "small": 0, "medium": 0, "large": 0, "critical": 0}
    for f in frames:
        gap = (f.get("gap_category") or "none").lower()
        if gap in by_gap:
            by_gap[gap] += 1
    
    return {
        "observed": observed,
        "interpolated": interpolated,
        "total": len(frames),
        "by_model": by_model,
        "by_confidence": by_confidence,
        "by_gap": by_gap
    }


def generate_html_report(
    job_id: str,
    pipeline_result: dict,
    trajectories: Optional[list] = None,
    alerts: Optional[list] = None,
    time_series: Optional[dict] = None,
    consistency_issues: Optional[list] = None,
) -> str:
    """
    Generate a comprehensive NASA-level technical report for a completed pipeline job.
    
    This report provides full traceability including:
    - Executive summary with key metrics
    - Run overview and execution timeline
    - Input parameters and spatial/temporal bounds
    - Interpolation configuration details
    - Model/pipeline methodology
    - Quality metrics with statistical breakdown
    - Frame-by-frame composition analysis
    - Generated artifacts manifest
    - Anomalies, warnings, and diagnostics
    - Limitations and validation notes
    """
    
    # Extract core data
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    metrics = pipeline_result.get("metrics") or {}
    frames = pipeline_result.get("frames") or []
    
    # Job metadata
    layer_id = pipeline_result.get("layer_id", "Unknown")
    data_source = pipeline_result.get("data_source", "Unknown")
    status = pipeline_result.get("status", "Unknown")
    bbox = pipeline_result.get("bbox", [])
    time_start = pipeline_result.get("time_start")
    time_end = pipeline_result.get("time_end")
    created_at = pipeline_result.get("created_at")
    completed_at = pipeline_result.get("completed_at")
    error_msg = pipeline_result.get("error")
    
    # Calculate metrics
    n_total = metrics.get("total_frames", len(frames))
    n_obs = metrics.get("observed_frames", 0)
    n_interp = metrics.get("interpolated_frames", 0)
    avg_psnr = metrics.get("avg_psnr")
    avg_ssim = metrics.get("avg_ssim")
    tcs = metrics.get("tcs")
    fsi = metrics.get("fsi")
    
    # Confidence counts
    high_conf = metrics.get("high_confidence_count", 0)
    med_conf = metrics.get("medium_confidence_count", 0)
    low_conf = metrics.get("low_confidence_count", 0)
    rejected = metrics.get("rejected_count", 0)
    
    # Frame breakdown
    frame_stats = _generate_frame_breakdown(frames)
    
    # Percentages
    obs_pct = (n_obs / max(n_total, 1)) * 100
    int_pct = (n_interp / max(n_total, 1)) * 100
    
    # Counts
    alert_count = len(alerts or [])
    high_alerts = sum(1 for a in (alerts or []) if a.get("severity", "").lower() == "high")
    traj_count = len(trajectories or [])
    issue_count = len(consistency_issues or [])
    
    # Duration
    duration = _calculate_duration(created_at, completed_at)
    
    # Generate tables
    alert_rows = ""
    for a in (alerts or [])[:25]:
        alert_rows += f"""
        <tr>
          <td class="font-mono">{a.get('frame_index', '—')}</td>
          <td>{str(a.get('type', '—')).replace('_', ' ').capitalize()}</td>
          <td>{_sev_badge(a.get('severity', 'low'))}</td>
          <td style="color: #444444;">{a.get('description', '—')[:140]}</td>
        </tr>"""
    
    traj_rows = ""
    for t in (trajectories or [])[:15]:
        traj_rows += f"""
        <tr>
          <td class="font-mono">{t.get('id', '—')}</td>
          <td class="font-mono">{t.get('speed', 0):.5f}</td>
          <td class="font-mono">{t.get('direction_deg', 0):.1f}&deg;</td>
          <td class="font-mono">{t.get('intensity', 0):.4f}</td>
        </tr>"""
    
    issue_rows = ""
    for iss in (consistency_issues or [])[:20]:
        issue_rows += f"""
        <tr>
          <td class="font-mono">{iss.get('frame', '—')}</td>
          <td>{iss.get('issue', '—')}</td>
          <td>{_sev_badge(iss.get('severity', 'low'))}</td>
          <td class="font-mono">{iss.get('mad_score', '—')}</td>
        </tr>"""
    
    # Model distribution rows
    model_rows = ""
    for model, count in sorted(frame_stats["by_model"].items(), key=lambda x: x[1], reverse=True):
        pct = (count / max(n_total, 1)) * 100
        model_rows += f"<tr><td class='font-mono'>{model}</td><td class='font-mono'>{count}</td><td class='font-mono'>{pct:.1f}%</td></tr>"
    
    # Confidence distribution
    frame_stats["by_confidence"]
    
    # Build the comprehensive HTML report
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>AetherGIS Technical Report — {job_id[:12]}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600&family=Barlow+Condensed:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #f5f5f5; --panel: #ffffff; --panel-hdr: #e8e8e8;
      --b1: #c0c0c0; --b2: #d8d8d8; --b3: #e8e8e8;
      --input-bg: #fafafa;
      --t1: #1a1a1a; --t2: #444444; --t3: #666666; --t4: #888888;
      --blue: #144e8c; --blue-bg: #e8f2fc; --blue-dark: #0d3a6b;
      --green: #207a30; --orange: #c06010; --red: #b82020;
      --accent: #144e8c;
    }}
    
    * {{ box-sizing: border-box; }}
    
    body {{
      font-family: 'Barlow', -apple-system, BlinkMacSystemFont, sans-serif;
      font-size: 13px; line-height: 1.5;
      background-color: var(--bg); color: var(--t1);
      margin: 0; padding: 0;
    }}
    
    .font-mono {{ font-family: 'JetBrains Mono', 'SF Mono', monospace; font-size: 11px; }}
    .font-cond {{ font-family: 'Barlow Condensed', sans-serif; }}
    
    /* Container & Layout */
    .container {{
      max-width: 1100px; margin: 0 auto;
      background: var(--panel);
      border: 1px solid var(--b1);
      box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    }}
    
    /* Header */
    .report-header {{
      background: linear-gradient(135deg, var(--blue-dark) 0%, var(--blue) 100%);
      color: white;
      padding: 28px 32px;
      border-bottom: 3px solid var(--blue-dark);
    }}
    
    .report-title {{
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 26px;
      font-weight: 700;
      letter-spacing: 0.03em;
      margin: 0 0 8px 0;
      text-transform: uppercase;
    }}
    
    .report-subtitle {{
      font-size: 13px;
      font-weight: 500;
      letter-spacing: 0.02em;
      opacity: 0.9;
      margin: 0 0 16px 0;
    }}
    
    .report-meta {{
      display: flex;
      gap: 32px;
      flex-wrap: wrap;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      opacity: 0.85;
    }}
    
    .report-meta span {{
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    
    .meta-label {{
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    
    /* Status Badge */
    .status-badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-weight: 600;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      padding: 4px 10px;
      border-radius: 2px;
    }}
    
    .status-success {{
      background: rgba(32, 122, 48, 0.15);
      color: #207a30;
      border: 1px solid rgba(32, 122, 48, 0.3);
    }}
    
    .status-error {{
      background: rgba(184, 32, 32, 0.15);
      color: #b82020;
      border: 1px solid rgba(184, 32, 32, 0.3);
    }}
    
    .status-warning {{
      background: rgba(192, 96, 16, 0.15);
      color: #c06010;
      border: 1px solid rgba(192, 96, 16, 0.3);
    }}
    
    .status-dot {{
      width: 7px; height: 7px;
      border-radius: 50%;
      background: currentColor;
    }}
    
    /* Content Area */
    .content {{
      padding: 24px 32px;
    }}
    
    /* Section Styling */
    .section {{
      margin-bottom: 32px;
    }}
    
    .section-title {{
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 14px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--blue);
      border-bottom: 2px solid var(--b2);
      padding-bottom: 8px;
      margin: 0 0 16px 0;
    }}
    
    .section-subtitle {{
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--t3);
      margin: 20px 0 10px 0;
    }}
    
    /* Executive Summary Box */
    .exec-summary {{
      background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
      border: 1px solid var(--b2);
      border-left: 4px solid var(--blue);
      padding: 20px 24px;
      margin-bottom: 24px;
    }}
    
    .exec-summary h3 {{
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 13px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--blue-dark);
      margin: 0 0 12px 0;
    }}
    
    .exec-summary p {{
      font-size: 12px;
      line-height: 1.6;
      color: var(--t2);
      margin: 0;
    }}
    
    /* Key Metrics Grid */
    .metrics-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 1px;
      background: var(--b2);
      border: 1px solid var(--b2);
      margin-bottom: 20px;
    }}
    
    .metric-card {{
      background: var(--panel);
      padding: 16px;
      display: flex;
      flex-direction: column;
    }}
    
    .metric-label {{
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--t4);
      margin-bottom: 6px;
    }}
    
    .metric-value {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 22px;
      font-weight: 600;
      color: var(--t1);
    }}
    
    .metric-value.small {{
      font-size: 16px;
    }}
    
    /* Progress Bar */
    .progress-container {{
      grid-column: span 4;
      background: var(--panel);
      padding: 16px 20px;
    }}
    
    .progress-bar {{
      display: flex;
      height: 8px;
      border-radius: 0;
      overflow: hidden;
      background: var(--b3);
      margin: 8px 0;
    }}
    
    .progress-segment-obs {{
      background: var(--blue);
      width: {obs_pct:.1f}%;
    }}
    
    .progress-segment-int {{
      background: var(--orange);
      width: {int_pct:.1f}%;
    }}
    
    .progress-legend {{
      display: flex;
      justify-content: space-between;
      font-size: 11px;
      color: var(--t3);
    }}
    
    /* Info Grid (Key-Value) */
    .info-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1px;
      background: var(--b2);
      border: 1px solid var(--b2);
    }}
    
    .info-row {{
      display: flex;
      background: var(--panel);
    }}
    
    .info-label {{
      width: 140px;
      padding: 10px 12px;
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--t4);
      background: var(--input-bg);
      border-right: 1px solid var(--b3);
    }}
    
    .info-value {{
      flex: 1;
      padding: 10px 12px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      color: var(--t2);
    }}
    
    /* Tables */
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 20px;
      font-size: 12px;
      border: 1px solid var(--b2);
    }}
    
    th {{
      background: var(--input-bg);
      color: var(--t2);
      font-family: 'Barlow Condensed', sans-serif;
      font-weight: 600;
      letter-spacing: 0.03em;
      padding: 8px 12px;
      text-align: left;
      border-bottom: 2px solid var(--b2);
      text-transform: uppercase;
      font-size: 10px;
    }}
    
    td {{
      background: var(--panel);
      padding: 8px 12px;
      border-bottom: 1px solid var(--b3);
      vertical-align: middle;
      color: var(--t2);
    }}
    
    tr:last-child td {{
      border-bottom: none;
    }}
    
    /* Badges */
    .badge {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 9px;
      padding: 2px 6px;
      border-radius: 2px;
      text-transform: uppercase;
      font-weight: 500;
      display: inline-block;
    }}
    
    .badge-na {{
      color: #787878;
      font-style: italic;
      background: transparent;
    }}
    
    .badge-conf-high {{
      color: var(--green);
      background: #e8f6ec;
      border: 1px solid rgba(32, 122, 48, 0.3);
    }}
    
    .badge-conf-med {{
      color: var(--orange);
      background: #fdf0e0;
      border: 1px solid rgba(192, 96, 16, 0.3);
    }}
    
    .badge-conf-low {{
      color: var(--red);
      background: #fde8e8;
      border: 1px solid rgba(184, 32, 32, 0.3);
    }}
    
    /* Note Boxes */
    .note-box {{
      background: var(--input-bg);
      border: 1px solid var(--b2);
      border-left: 3px solid var(--orange);
      padding: 12px 16px;
      margin: 12px 0;
      font-size: 11px;
      color: var(--t3);
    }}
    
    .note-box.warning {{
      border-left-color: var(--red);
      background: #fff8f7;
    }}
    
    .note-box.info {{
      border-left-color: var(--blue);
      background: #f0f5fa;
    }}
    
    .note-box strong {{
      color: var(--t2);
      font-weight: 600;
    }}
    
    /* Artifacts List */
    .artifact-list {{
      list-style: none;
      padding: 0;
      margin: 0;
    }}
    
    .artifact-list li {{
      display: flex;
      justify-content: space-between;
      padding: 8px 12px;
      border-bottom: 1px solid var(--b3);
      font-size: 11px;
    }}
    
    .artifact-list li:last-child {{
      border-bottom: none;
    }}
    
    .artifact-path {{
      font-family: 'JetBrains Mono', monospace;
      color: var(--blue);
    }}
    
    .artifact-desc {{
      color: var(--t3);
      font-style: italic;
    }}
    
    /* Footer */
    .report-footer {{
      background: var(--input-bg);
      border-top: 1px solid var(--b2);
      padding: 20px 32px;
      margin-top: 20px;
    }}
    
    .footer-section {{
      margin-bottom: 16px;
    }}
    
    .footer-section:last-child {{
      margin-bottom: 0;
    }}
    
    .footer-title {{
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--t3);
      margin-bottom: 8px;
    }}
    
    .footer-text {{
      font-size: 11px;
      color: var(--t3);
      line-height: 1.5;
    }}
    
    .footer-meta {{
      display: flex;
      justify-content: space-between;
      border-top: 1px solid var(--b2);
      padding-top: 12px;
      margin-top: 12px;
      font-size: 10px;
      color: var(--t4);
    }}
    
    /* Print Styles */
    @media print {{
      body {{ background: white; }}
      .container {{ border: none; box-shadow: none; }}
      .report-header {{ -webkit-print-color-adjust: exact; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <!-- Header -->
    <div class="report-header">
      <h1 class="report-title">AetherGIS — Temporal Interpolation Analysis Report</h1>
      <p class="report-subtitle">Satellite Imagery Frame Interpolation Pipeline — Technical Documentation</p>
      
      <div class="report-meta">
        <span><span class="meta-label">Job ID:</span> {job_id}</span>
        <span><span class="meta-label">Layer:</span> {layer_id}</span>
        <span><span class="meta-label">Status:</span> 
          <span class="status-badge {'status-success' if status == 'completed' else 'status-error' if error_msg else 'status-warning'}">
            <span class="status-dot"></span>{status.upper()}
          </span>
        </span>
        <span><span class="meta-label">Generated:</span> {now}</span>
      </div>
    </div>
    
    <!-- Content -->
    <div class="content">
      
      <!-- Executive Summary -->
      <div class="section">
        <h2 class="section-title">1. Executive Summary</h2>
        
        <div class="exec-summary">
          <h3>Analysis Overview</h3>
          <p>
            This report documents the execution of the AetherGIS temporal interpolation pipeline 
            for satellite imagery sequence generation. The pipeline processed <strong>{n_obs} observed frames</strong> 
            from {data_source} source data, generating <strong>{n_interp} AI-interpolated intermediate frames</strong> 
            for temporal gap filling. Total output sequence comprises <strong>{n_total} frames</strong>.
            {" Execution completed with errors." if error_msg else f" Execution completed successfully in {duration}."}
          </p>
        </div>
        
        <!-- Key Metrics -->
        <div class="metrics-grid">
          <div class="metric-card">
            <div class="metric-label">Total Frames</div>
            <div class="metric-value">{n_total}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">Observed (Source)</div>
            <div class="metric-value" style="color: var(--blue);">{n_obs}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">Interpolated (AI)</div>
            <div class="metric-value" style="color: var(--orange);">{n_interp}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">Execution Time</div>
            <div class="metric-value small">{duration}</div>
          </div>
          
          <div class="progress-container">
            <div class="metric-label">Frame Composition Ratio</div>
            <div class="progress-bar">
              <div class="progress-segment-obs"></div>
              <div class="progress-segment-int"></div>
            </div>
            <div class="progress-legend">
              <span><strong style="color: var(--blue);">{n_obs}</strong> Observed Primary ({obs_pct:.1f}%)</span>
              <span><strong style="color: var(--orange);">{n_interp}</strong> AI-Interpolated ({int_pct:.1f}%)</span>
            </div>
          </div>
        </div>
      </div>
      
      <!-- Run Overview -->
      <div class="section">
        <h2 class="section-title">2. Run Overview & Execution Timeline</h2>
        
        <div class="info-grid">
          <div class="info-row">
            <div class="info-label">Job ID</div>
            <div class="info-value">{job_id}</div>
          </div>
          <div class="info-row">
            <div class="info-label">Data Source</div>
            <div class="info-value">{data_source}</div>
          </div>
          <div class="info-row">
            <div class="info-label">Layer ID</div>
            <div class="info-value">{layer_id}</div>
          </div>
          <div class="info-row">
            <div class="info-label">Status</div>
            <div class="info-value">{status.upper()}</div>
          </div>
          <div class="info-row">
            <div class="info-label">Created At</div>
            <div class="info-value">{_format_datetime(created_at)}</div>
          </div>
          <div class="info-row">
            <div class="info-label">Completed At</div>
            <div class="info-value">{_format_datetime(completed_at)}</div>
          </div>
          <div class="info-row">
            <div class="info-label">Duration</div>
            <div class="info-value">{duration}</div>
          </div>
          <div class="info-row">
            <div class="info-label">Report Generated</div>
            <div class="info-value">{now}</div>
          </div>
        </div>
        
        {f'''<div class="note-box warning">
          <strong>Execution Error:</strong> This pipeline run encountered an error during execution: {error_msg}
        </div>''' if error_msg else ''}
      </div>
      
      <!-- Input Parameters -->
      <div class="section">
        <h2 class="section-title">3. Input Summary & Spatial Bounds</h2>
        
        <h3 class="section-subtitle">Temporal Coverage</h3>
        <div class="info-grid">
          <div class="info-row">
            <div class="info-label">Start Time</div>
            <div class="info-value">{_format_datetime(time_start)}</div>
          </div>
          <div class="info-row">
            <div class="info-label">End Time</div>
            <div class="info-value">{_format_datetime(time_end)}</div>
          </div>
        </div>
        
        <h3 class="section-subtitle">Spatial Bounding Box (WGS84)</h3>
        <div class="info-grid">
          <div class="info-row">
            <div class="info-label">West (Min Lon)</div>
            <div class="info-value">{bbox[0] if len(bbox) > 0 else '—'}°</div>
          </div>
          <div class="info-row">
            <div class="info-label">East (Max Lon)</div>
            <div class="info-value">{bbox[2] if len(bbox) > 2 else '—'}°</div>
          </div>
          <div class="info-row">
            <div class="info-label">South (Min Lat)</div>
            <div class="info-value">{bbox[1] if len(bbox) > 1 else '—'}°</div>
          </div>
          <div class="info-row">
            <div class="info-label">North (Max Lat)</div>
            <div class="info-value">{bbox[3] if len(bbox) > 3 else '—'}°</div>
          </div>
        </div>
        
        <div class="note-box info">
          <strong>Coordinate Reference System:</strong> EPSG:4326 (WGS84). 
          All spatial bounds are specified in decimal degrees.
        </div>
      </div>
      
      <!-- Interpolation Configuration -->
      <div class="section">
        <h2 class="section-title">4. Interpolation Configuration & Model Details</h2>
        
        <h3 class="section-subtitle">Frame Generation Model Distribution</h3>
        <table>
          <thead>
            <tr>
              <th>Model / Method</th>
              <th>Frame Count</th>
              <th>Percentage</th>
            </tr>
          </thead>
          <tbody>
            {model_rows if model_rows else '<tr><td colspan="3" style="text-align: center; color: var(--t4);">No model data available</td></tr>'}
          </tbody>
        </table>
        
        <h3 class="section-subtitle">Confidence Classification Distribution</h3>
        <table>
          <thead>
            <tr>
              <th>Confidence Class</th>
              <th>Frame Count</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>High Confidence</td>
              <td class="font-mono">{high_conf}</td>
              <td>{_conf_badge('high')}</td>
            </tr>
            <tr>
              <td>Medium Confidence</td>
              <td class="font-mono">{med_conf}</td>
              <td>{_conf_badge('medium')}</td>
            </tr>
            <tr>
              <td>Low Confidence</td>
              <td class="font-mono">{low_conf}</td>
              <td>{_conf_badge('low')}</td>
            </tr>
            <tr>
              <td>Rejected / Flagged</td>
              <td class="font-mono">{rejected}</td>
              <td>{_sev_badge('high' if rejected > 0 else 'low')}</td>
            </tr>
          </tbody>
        </table>
        
        <div class="note-box">
          <strong>Confidence Classification Methodology:</strong> Frames are classified based on 
          PSNR thresholding (&gt;28 dB = High, 22-28 dB = Medium, &lt;22 dB = Low), SSIM stability, 
          and temporal consistency scoring (TCS). Rejected frames failed quality gates.
        </div>
      </div>
      
      <!-- Quality Metrics -->
      <div class="section">
        <h2 class="section-title">5. Quality Metrics & Validation</h2>
        
        <div class="metrics-grid">
          <div class="metric-card">
            <div class="metric-label">Average PSNR</div>
            <div class="metric-value">{_metric_badge(avg_psnr, 28, 22, ' dB')}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">Average SSIM</div>
            <div class="metric-value">{_metric_badge(avg_ssim, 0.85, 0.70)}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">TCS Score</div>
            <div class="metric-value">{_metric_badge(tcs, 0.80, 0.65)}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">FSI Index</div>
            <div class="metric-value">{_metric_badge(fsi, 0.75, 0.60)}</div>
          </div>
        </div>
        
        <div class="note-box info">
          <strong>Metric Definitions:</strong><br>
          <strong>PSNR (Peak Signal-to-Noise Ratio):</strong> Measures reconstruction quality in dB. 
          Higher values indicate better fidelity to observed reference frames.<br>
          <strong>SSIM (Structural Similarity Index):</strong> Range [0,1]. Measures perceptual similarity 
          accounting for luminance, contrast, and structure.<br>
          <strong>TCS (Temporal Consistency Score):</strong> Frame-to-frame motion coherence metric. 
          High TCS indicates smooth temporal transitions.<br>
          <strong>FSI (Frame Stability Index):</strong> Composite metric assessing overall interpolation stability.
        </div>
        
        <div class="note-box">
          <strong>Important Limitation:</strong> PSNR and SSIM metrics measure AI self-consistency 
          relative to bracketing historical observed frames — they are <strong>NOT</strong> independent 
          ground-truth validations. These metrics assume the observed frames are accurate reference points.
        </div>
      </div>
      
      <!-- Output Artifacts -->
      <div class="section">
        <h2 class="section-title">6. Generated Output Artifacts</h2>
        
        <h3 class="section-subtitle">Video Sequences</h3>
        <ul class="artifact-list">
          <li>
            <span class="artifact-path">/exports/{job_id}/original.mp4</span>
            <span class="artifact-desc">Original observed frame sequence (no interpolation)</span>
          </li>
          <li>
            <span class="artifact-path">/exports/{job_id}/interpolated.mp4</span>
            <span class="artifact-desc">Full interpolated sequence (observed + AI frames)</span>
          </li>
        </ul>
        
        <h3 class="section-subtitle">Frame Archive</h3>
        <ul class="artifact-list">
          <li>
            <span class="artifact-path">/exports/{job_id}/frames/frame_*.png</span>
            <span class="artifact-desc">Individual frame images ({n_total} frames, PNG format)</span>
          </li>
        </ul>
        
        <h3 class="section-subtitle">Metadata & Documentation</h3>
        <ul class="artifact-list">
          <li>
            <span class="artifact-path">/exports/{job_id}/metadata.json</span>
            <span class="artifact-desc">Complete frame metadata with per-frame metrics</span>
          </li>
          <li>
            <span class="artifact-path">/exports/{job_id}/report.html</span>
            <span class="artifact-desc">This technical analysis report</span>
          </li>
        </ul>
      </div>
      
      <!-- Detected Anomalies -->
      {f'''
      <div class="section">
        <h2 class="section-title">7. Detected Anomalies & Alerts</h2>
        <p style="font-size: 12px; color: var(--t3); margin-bottom: 12px;">
          Total alerts: {alert_count} | High severity: {high_alerts} | Showing up to 25 most recent
        </p>
        <table>
          <thead>
            <tr>
              <th>Frame</th>
              <th>Classification</th>
              <th>Severity</th>
              <th>Diagnostic</th>
            </tr>
          </thead>
          <tbody>
            {alert_rows}
          </tbody>
        </table>
      </div>
      ''' if alert_count else ''}
      
      <!-- Consistency Diagnostics -->
      {f'''
      <div class="section">
        <h2 class="section-title">8. Temporal Consistency Diagnostics</h2>
        <p style="font-size: 12px; color: var(--t3); margin-bottom: 12px;">
          Total issues: {issue_count} | Showing up to 20 most significant
        </p>
        <table>
          <thead>
            <tr>
              <th>Frame</th>
              <th>Issue Type</th>
              <th>Severity</th>
              <th>MAD Variance</th>
            </tr>
          </thead>
          <tbody>
            {issue_rows}
          </tbody>
        </table>
      </div>
      ''' if issue_count else ''}
      
      <!-- Trajectory Analysis -->
      {f'''
      <div class="section">
        <h2 class="section-title">9. Vector Trajectory Analysis</h2>
        <p style="font-size: 12px; color: var(--t3); margin-bottom: 12px;">
          Detected trajectories: {traj_count} | Showing up to 15 most significant
        </p>
        <table>
          <thead>
            <tr>
              <th>Trajectory ID</th>
              <th>Velocity</th>
              <th>Heading</th>
              <th>Intensity</th>
            </tr>
          </thead>
          <tbody>
            {traj_rows}
          </tbody>
        </table>
      </div>
      ''' if traj_count else ''}
      
      <!-- Limitations & Validation -->
      <div class="section">
        <h2 class="section-title">10. Limitations, Caveats & Validation Notes</h2>
        
        <div class="note-box warning">
          <strong>Operational Restrictions:</strong> AI-interpolated frames are synthetically 
          generated and intended for <strong>qualitative temporal analysis only</strong>. Do not use 
          these outputs for operational forecasting, active storm advisory, or precise scientific 
          measurement without independent validation.
        </div>
        
        <div class="note-box">
          <strong>Temporal Gap Considerations:</strong> Interpolation accuracy degrades with 
          larger temporal gaps. Gap categories: None (0-30 min), Small (30-60 min), 
          Medium (1-4 hours), Large (4-12 hours), Critical (&gt;12 hours). Large gaps may 
          produce artifacts.
        </div>
        
        <div class="note-box info">
          <strong>Cross-Reference Requirements:</strong> Always validate interpolated sequences 
          against primary orbital assets (MODIS, VIIRS, GOES, Sentinel) before drawing 
          operational conclusions. This analysis is a data augmentation tool, not a 
          replacement for direct observation.
        </div>
        
        <h3 class="section-subtitle">Traceability Statement</h3>
        <p style="font-size: 11px; color: var(--t3); line-height: 1.6;">
          This report was auto-generated by AetherGIS v2.0 pipeline system. All metrics 
          are computed from the actual execution artifacts stored at <code>/exports/{job_id}/</code>. 
          Frame-level metadata includes: source timestamp, interpolation model used, 
          PSNR/SSIM scores (for interpolated frames), confidence classification, and gap 
          category. In case of database record loss, results can be fully reconstructed 
          from the metadata.json sidecar file.
        </p>
      </div>
      
    </div>
    
    <!-- Footer -->
    <div class="report-footer">
      <div class="footer-section">
        <div class="footer-title">Data Provenance & Attribution</div>
        <p class="footer-text">
          <strong>Primary Source:</strong> NASA GIBS Earthdata API (Global Imagery Browse Services)<br>
          <strong>Interpolation Engine:</strong> AetherGIS v2.0 with RIFE/FILM optical flow models<br>
          <strong>Processing Location:</strong> AetherGIS Analysis Pipeline (Module 15)<br>
          <strong>Report ID:</strong> RPT-{job_id[:12]}-{datetime.utcnow().strftime('%Y%m%d')}
        </p>
      </div>
      
      <div class="footer-meta">
        <span>AetherGIS Technical Report</span>
        <span>Job: {job_id[:16]}</span>
        <span>Generated: {now}</span>
      </div>
    </div>
    
  </div>
</body>
</html>"""
    
    return html
