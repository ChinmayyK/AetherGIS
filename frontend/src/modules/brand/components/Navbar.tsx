import React, { useEffect, useId, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useSystemConfig } from '@shared/api/client';

interface NavItem {
  label: string;
  to: string;
}

const PRIMARY_NAV_ITEMS: NavItem[] = [
  { label: 'Product', to: '/' },
  { label: 'How It Works', to: '/how-it-works' },
  { label: 'Features', to: '/product' },
  { label: 'Data Sources', to: '/data-sources' },
  { label: 'Documentation', to: '/docs' },
  { label: 'About', to: '/about' },
];

const MOBILE_EXTRA_ITEMS: NavItem[] = [
  { label: 'Contact', to: '/contact' },
  { label: 'Security', to: '/security' },
  { label: 'Status', to: '/status' },
  { label: 'Privacy', to: '/privacy' },
  { label: 'Terms', to: '/terms' },
];

const Navbar: React.FC = () => {
  const location = useLocation();
  const { data: config } = useSystemConfig();

  const [isScrolled, setIsScrolled] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const mobileMenuId = useId();

  useEffect(() => {
    const handleScroll = () => setIsScrolled(window.scrollY > 16);
    handleScroll();
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  useEffect(() => {
    if (!isMobileMenuOpen) {
      return undefined;
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsMobileMenuOpen(false);
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isMobileMenuOpen]);

  const handleDashboardRedirect = () => {
    if (config?.mode === 'production' || config?.is_dev_preview) {
      window.location.href = '/api/v1/auth/login';
    } else {
      window.location.href = '/dashboard';
    }
  };

  const isActive = (path: string) =>
    path === '/docs'
      ? location.pathname.startsWith('/docs')
      : path === '/product'
        ? location.pathname === '/product' || location.pathname === '/features'
        : location.pathname === path;

  return (
    <header className={`brand-nav ${isScrolled ? 'is-scrolled' : ''}`}>
      <div className="brand-nav-inner">
        <Link to="/" className="brand-logo" aria-label="AetherGIS home">
          <img
            src="/icon.png"
            alt=""
            className="brand-logo-icon"
            aria-hidden="true"
          />
          <div className="brand-logo-text">
            <span className="brand-logo-title">AetherGIS</span>
            <span className="brand-logo-subtitle">AI-Based Temporal Enhancement</span>
          </div>
        </Link>

        <nav className="brand-nav-links" aria-label="Primary">
          {PRIMARY_NAV_ITEMS.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className={`brand-nav-link ${isActive(item.to) ? 'is-active' : ''}`}
              aria-current={isActive(item.to) ? 'page' : undefined}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="brand-nav-actions">
          <button type="button" className="brand-btn brand-btn-ghost brand-btn-sm" onClick={handleDashboardRedirect}>
            Log In
          </button>
          <button type="button" className="brand-btn brand-btn-primary brand-btn-sm" onClick={handleDashboardRedirect}>
            Request Access
          </button>
        </div>

        <button
          type="button"
          className="brand-nav-toggle"
          onClick={() => setIsMobileMenuOpen((open) => !open)}
          aria-label={isMobileMenuOpen ? 'Close navigation menu' : 'Open navigation menu'}
          aria-expanded={isMobileMenuOpen}
          aria-controls={mobileMenuId}
        >
          <span />
          <span />
          <span />
        </button>
      </div>

      {isMobileMenuOpen ? (
        <div id={mobileMenuId} className="brand-mobile-menu is-open">
          <nav className="brand-mobile-links" aria-label="Mobile">
            {[...PRIMARY_NAV_ITEMS, ...MOBILE_EXTRA_ITEMS].map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className={isActive(item.to) ? 'is-active' : ''}
                aria-current={isActive(item.to) ? 'page' : undefined}
              >
                {item.label}
              </Link>
            ))}
            <button
              type="button"
              className="brand-btn brand-btn-primary"
              onClick={handleDashboardRedirect}
            >
              Request Access
            </button>
          </nav>
        </div>
      ) : null}
    </header>
  );
};

export default Navbar;

