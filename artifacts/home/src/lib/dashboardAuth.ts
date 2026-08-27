/**
 * Same-tab auth bridge for app-global protected UI.
 *
 * Main Brain and Cockpit use separate storage keys, so the global alert dock
 * cannot infer the active page's authenticated state from storage alone.
 */
export const DASHBOARD_AUTH_EVENT = 'dashboard:auth';

export interface DashboardAuthDetail {
  authenticated: boolean;
  authorization: string;
}

export function announceDashboardAuth(authenticated: boolean, authorization = ''): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<DashboardAuthDetail>(DASHBOARD_AUTH_EVENT, {
    detail: {
      authenticated: authenticated && Boolean(authorization),
      authorization: authenticated ? authorization : '',
    },
  }));
}