import { useStore } from '@app/store/useStore';
import { 
  getVideoUrl, 
  getMetadataUrl, 
  getReportUrl, 
  getZipUrl 
} from '@shared/api/client';

export default function ExportPanel() {
  const { pipelineResult, jobId } = useStore();
  const hasResult = pipelineResult?.status === 'COMPLETED' && !!jobId;

  const handleDownload = async (url: string, filename: string) => {
    try {
      // For some reports/zips, we might want to just open in new tab if they are large
      // but for production UX, we'll try to force download
      const res = await fetch(url);
      if (!res.ok) throw new Error('Download not ready');
      
      const blob = await res.blob();
      const blobUrl = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(blobUrl);
    } catch (e) {
      console.error('Download failed, opening in new tab', e);
      window.open(url, '_blank');
    }
  };

  interface ExportBtnProps {
    label: string;
    icon: string;
    url: string;
    filename: string;
    disabled?: boolean;
    secondary?: boolean;
  }

  const ExportBtn = ({ label, icon, url, filename, disabled, secondary }: ExportBtnProps) => (
    <button
      className={`btn ${secondary ? 'btn-ghost' : 'btn-ghost'}`}
      style={{ 
        flex: 1, 
        justifyContent: 'center', 
        fontSize: 11,
        border: '1px solid var(--border-subtle)',
        background: 'rgba(255,255,255,0.03)'
      }}
      onClick={() => handleDownload(url, filename)}
      disabled={disabled || !hasResult}
      title={disabled || !hasResult ? 'Run a pipeline first' : undefined}
    >
      <span style={{ marginRight: 6 }}>{icon}</span> {label}
    </button>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className="section-header">Export & Analytics</div>

      {!hasResult && (
        <div style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 8,
          padding: '24px 16px',
          textAlign: 'center',
          color: 'var(--text-muted)',
          fontSize: 12,
          lineHeight: 1.5
        }}>
          <div style={{ fontSize: 24, marginBottom: 8 }}>🔒</div>
          Run a pipeline to unlock production-grade exports and analytical reports.
        </div>
      )}

      {hasResult && (
        <>
          {/* Quick Metrics */}
          <div style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 8,
            padding: '12px',
            fontSize: 12,
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
              <span style={{ color: 'var(--text-secondary)' }}>Status:</span>
              <span style={{ color: 'var(--conf-high)', fontWeight: 600 }}>READY for Export</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, color: 'var(--text-muted)' }}>
              <div>Frames: {pipelineResult!.metrics?.total_frames}</div>
              <div>Gap: {pipelineResult!.frames?.[0]?.gap_category || 'N/A'}</div>
            </div>
          </div>

          {/* Video Downloads */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>🎬 Video Assets</div>
            <div style={{ display: 'flex', gap: 8 }}>
              <ExportBtn
                label="MP4 (Enhanced)"
                icon="🚀"
                url={getVideoUrl(jobId!, 'interpolated')}
                filename={`AetherGIS_Enhanced_${jobId!.slice(0, 8)}.mp4`}
              />
              <ExportBtn
                label="MP4 (Original)"
                icon="📹"
                url={getVideoUrl(jobId!, 'original')}
                filename={`AetherGIS_Original_${jobId!.slice(0, 8)}.mp4`}
                secondary
              />
            </div>
          </div>

          {/* Analytics & Data */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }}>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>📊 Data & Insights</div>
            <div style={{ display: 'flex', gap: 8 }}>
              <ExportBtn
                label="HTML Report"
                icon="📋"
                url={getReportUrl(jobId!)}
                filename={`AetherGIS_Report_${jobId!.slice(0, 8)}.html`}
              />
              <ExportBtn
                label="JSON Metadata"
                icon="📊"
                url={getMetadataUrl(jobId!)}
                filename={`AetherGIS_Metadata_${jobId!.slice(0, 8)}.json`}
              />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <ExportBtn
                label="ZIP Frames"
                icon="📦"
                url={getZipUrl(jobId!)}
                filename={`AetherGIS_Frames_${jobId!.slice(0, 8)}.zip`}
              />
            </div>
          </div>

          {/* Attribution & Notice */}
          <div style={{
            fontSize: 10,
            color: 'var(--text-muted)',
            lineHeight: 1.6,
            background: 'rgba(0,0,0,0.2)',
            borderRadius: 6,
            padding: '10px',
            border: '1px solid rgba(255,255,255,0.05)'
          }}>
            <div style={{ color: 'var(--text-secondary)', fontWeight: 600, marginBottom: 4 }}>ℹ Production Notice</div>
            <div>• Video generation is on-demand (FFmpeg/NVENC)</div>
            <div>• Metadata includes per-frame confidence, PSNR/SSIM</div>
            <div>• Analysis source: NASA GIBS / EOSDIS</div>
            <div style={{ marginTop: 8, color: '#f87171', fontSize: 9 }}>
              ⚠ Interpolated data is synthetic. Not for operational forecasting.
            </div>
          </div>
        </>
      )}
    </div>
  );
}
