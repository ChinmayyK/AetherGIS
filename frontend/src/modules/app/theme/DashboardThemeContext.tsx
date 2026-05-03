import { createContext } from 'react';

export type DashboardTheme = 'dark' | 'light';

export interface DashboardThemeContextValue {
  theme: DashboardTheme;
  isDark: boolean;
  setTheme: (theme: DashboardTheme) => void;
  toggleTheme: () => void;
}

export const STORAGE_KEY = 'aethergis-dashboard-theme';

export const DashboardThemeContext = createContext<DashboardThemeContextValue | null>(null);
