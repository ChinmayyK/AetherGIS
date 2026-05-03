import { useContext } from 'react';
import { DashboardThemeContext } from './DashboardThemeContext';

export function useDashboardTheme() {
  const context = useContext(DashboardThemeContext);
  if (!context) {
    throw new Error('useDashboardTheme must be used within DashboardThemeProvider');
  }
  return context;
}