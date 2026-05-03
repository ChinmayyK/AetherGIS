/**
 * ProductNoticeBanner — Cookie-notice style disclaimer banner
 * Shows on first dashboard load, dismissible and stored in localStorage
 * Theme-aware: Uses CSS variables for automatic light/dark adaptation
 */
import { useState } from 'react';

const STORAGE_KEY = 'aethergis:notice-dismissed';

export function ProductNoticeBanner() {
  const [isVisible, setIsVisible] = useState(() => {
    // Check if user has already dismissed the notice
    if (typeof window !== 'undefined') {
      return !localStorage.getItem(STORAGE_KEY);
    }
    return false;
  });

  const handleDismiss = () => {
    localStorage.setItem(STORAGE_KEY, 'true');
    setIsVisible(false);
  };

  if (!isVisible) return null;

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 0,
        left: 0,
        right: 0,
        background: 'var(--warn-strip-bg)',
        borderTop: '2px solid var(--warn-strip-border)',
        padding: '14px 20px',
        zIndex: 9999,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '20px',
        boxShadow: '0 -4px 20px var(--overlay-backdrop)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flex: 1 }}>
        <span style={{ fontSize: '18px', color: 'var(--warn-strip-title)' }}>⚠</span>
        <div style={{ color: 'var(--warn-strip-body)', fontSize: '12px', lineHeight: 1.5 }}>
          <strong style={{ color: 'var(--warn-strip-title)' }}>Product Notice:</strong>{' '}
          AI-interpolated frames are <strong>synthetically generated approximations</strong> intended for qualitative temporal analysis only. 
          They are <strong>not suitable</strong> for operational forecasting, storm advisory, or scientific measurement. 
          Always refer to original observed satellite data for authoritative analysis.
          <span style={{ color: 'var(--t4)', marginLeft: '8px' }}>
            Source: NASA GIBS · SSIM/TCS metrics measure AI self-consistency only.
          </span>
        </div>
      </div>
      
      <button
        onClick={handleDismiss}
        style={{
          background: 'var(--orng-bg)',
          border: '1px solid var(--warn-strip-border)',
          color: 'var(--orange)',
          padding: '8px 16px',
          fontSize: '11px',
          fontWeight: 600,
          textTransform: 'uppercase',
          letterSpacing: '0.04em',
          cursor: 'pointer',
          borderRadius: '2px',
          whiteSpace: 'nowrap',
          transition: 'all 0.15s ease',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = 'var(--warn-strip-border)';
          e.currentTarget.style.color = 'var(--bg)';
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = 'var(--orng-bg)';
          e.currentTarget.style.color = 'var(--orange)';
        }}
      >
        Acknowledge
      </button>
    </div>
  );
}

export default ProductNoticeBanner;
