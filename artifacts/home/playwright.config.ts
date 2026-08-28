import { defineConfig } from '@playwright/test';

const baseURL = process.env.OPERATOR_CONSOLE_BASE_URL
  ?? (process.env.REPLIT_DEV_DOMAIN ? `https://${process.env.REPLIT_DEV_DOMAIN}` : 'http://127.0.0.1:5173');

export default defineConfig({
  testDir: './e2e',
  testMatch: 'operator-decision-contract.spec.ts',
  timeout: 45_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['line']],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});