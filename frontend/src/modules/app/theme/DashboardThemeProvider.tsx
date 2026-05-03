import {
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import {
  DashboardThemeContext,
  STORAGE_KEY,
  type DashboardTheme,
  type DashboardThemeContextValue,
} from './DashboardThemeContext';

function getInitialTheme(): DashboardTheme {
  if (typeof window === 'undefined') return 'dark';
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored === 'light' || stored === 'dark' ? stored : 'dark';
}

export function DashboardThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<DashboardTheme>(getInitialTheme);

  useEffect(() => {
    const root = document.documentElement;
    const body = document.body;

    root.classList.add('dashboard-mode');
    body.classList.add('dashboard-mode');

    return () => {
      root.classList.remove('dashboard-mode');
      body.classList.remove('dashboard-mode');
      root.removeAttribute('data-dashboard-theme');
      body.removeAttribute('data-dashboard-theme');
      root.style.removeProperty('color-scheme');
      body.style.removeProperty('color-scheme');
    };
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    const body = document.body;

    root.dataset.dashboardTheme = theme;
    body.dataset.dashboardTheme = theme;
    root.style.setProperty('color-scheme', theme);
    body.style.setProperty('color-scheme', theme);
    window.localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  const value = useMemo<DashboardThemeContextValue>(
    () => ({
      theme,
      isDark: theme === 'dark',
      setTheme,
      toggleTheme: () => setTheme((current) => (current === 'dark' ? 'light' : 'dark')),
    }),
    [theme],
  );

  return (
    <DashboardThemeContext.Provider value={value}>
      {children}
    </DashboardThemeContext.Provider>
  );
}
