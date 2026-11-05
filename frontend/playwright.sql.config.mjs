import { defineConfig } from '@playwright/test';
import base from './playwright.config.mjs';

export default defineConfig({
  ...base,
  testMatch: 'sql-evaluation.spec.mjs',
  timeout: 60_000,
  outputDir: './artifacts/sql-results',
  webServer: [
    { command: `${process.env.PYTHON || 'python'} tests/sql_backend.py`, url: 'http://127.0.0.1:51011/health', timeout: 120_000, reuseExistingServer: false },
    { ...base.webServer[1], timeout: 120_000 },
  ],
});
