import { defineConfig } from '@playwright/test';
import { existsSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// 隔离测试文件到工程所在磁盘，避免系统临时盘空间影响浏览器及 SQLite。
const testTemporaryDirectory = fileURLToPath(new URL('./artifacts/tmp/', import.meta.url));
mkdirSync(testTemporaryDirectory, { recursive: true });
process.env.TEMP = testTemporaryDirectory;
process.env.TMP = testTemporaryDirectory;
process.env.TMPDIR = testTemporaryDirectory;

const installed = [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
].find(existsSync);

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 15_000 },
  reporter: 'list',
  outputDir: './artifacts/test-results',
  use: {
    baseURL: 'http://127.0.0.1:51012',
    viewport: { width: 1440, height: 1000 },
    reducedMotion: 'reduce',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    launchOptions: { executablePath: process.env.BROWSER_EXECUTABLE || installed },
  },
  webServer: [
    { command: `${process.env.PYTHON || 'python'} tests/live_backend.py`, url: 'http://127.0.0.1:51011/health', timeout: 30_000, reuseExistingServer: false },
    { command: 'node server.mjs', url: 'http://127.0.0.1:51012', env: { HOST: '127.0.0.1', PORT: '51012', BACKEND_URL: 'http://127.0.0.1:51011' }, timeout: 30_000, reuseExistingServer: false },
  ],
});
