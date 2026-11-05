// Uses the backend's configured credential; never records requests or traces.
import { chromium } from '@playwright/test';
import { existsSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const base = process.env.SQL_TEST_URL || 'http://127.0.0.1:51022';
const dataset = fileURLToPath(new URL('../../data/sql_evaluation/test_cases_100.json', import.meta.url));
const output = fileURLToPath(new URL('../../verification/sql-live/', import.meta.url));
const cases = JSON.parse(await readFile(dataset, 'utf8'));
const executablePath = process.env.BROWSER_EXECUTABLE || [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
].find(existsSync);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ executablePath, headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const pageErrors = [];
page.on('pageerror', error => pageErrors.push(error.message));
const api = async path => {
  const response = await page.request.get(base + '/api/v1/sql-evaluation' + path);
  assert.equal(response.status(), 200);
  return (await response.json()).data;
};
try {
  await page.goto(base + '/evaluation/natural-language-sql');
  assert.equal(await page.locator('.dataset-rows > details').count(), 0);
  assert.equal(await page.getByRole('button', { name: '开始本地脚本评测' }).isDisabled(), true);
  await page.getByLabel('上传测试数据 JSON', { exact: true }).setInputFiles(dataset);
  await page.waitForFunction(() => document.querySelectorAll('.dataset-rows > details').length === 100);
  assert.equal(await page.locator('.dataset-rows > details').count(), cases.length);
  await page.locator('.dataset-rows > details').first().evaluate(node => { node.open = true; });
  assert.ok((await page.locator('.dataset-rows').innerText()).includes(cases[0].standard_sql));
  await page.screenshot({ path: output + 'uploaded.png', fullPage: true });
  assert.equal(await page.getByLabel('工作流 API Key', { exact: true }).inputValue(), '');
  const created = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/sql-evaluation/jobs'));
  await page.getByRole('button', { name: '开始本地脚本评测' }).click();
  const response = await created;
  assert.equal(response.status(), 202);
  const submitted = response.request().postDataJSON();
  assert.equal(submitted.api_key, '');
  assert.deepEqual(submitted.cases, cases);
  let job = (await response.json()).data;
  console.log(JSON.stringify({ job_id: job.job_id, total: job.total, credential: 'backend default' }));
  const deadline = Date.now() + 40 * 60_000;
  let previous = '';
  while (Date.now() < deadline) {
    job = await api('/jobs/' + job.job_id);
    const progress = `${job.status} ${job.completed}/${job.total}`;
    if (progress !== previous) { console.log(progress); previous = progress; }
    if (!['queued', 'running'].includes(job.status)) break;
    await page.waitForTimeout(3000);
  }
  await writeFile(output + 'job.json', JSON.stringify(job, null, 2));
  assert.equal(job.status, 'succeeded', job.error);
  assert.equal(job.total, cases.length);
  assert.equal(job.completed, cases.length);
  const saved = await page.request.get(base + `/api/v1/sql-evaluation/jobs/${job.job_id}/files/test_cases.json`);
  assert.deepEqual(await saved.json(), cases);
  await page.getByRole('button', { name: '刷新进度', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.result-status')?.textContent.includes('评测完成'));
  const [download] = await Promise.all([
    page.waitForEvent('download'), page.getByRole('link', { name: '完整 JSON', exact: true }).click(),
  ]);
  await download.saveAs(output + 'results.json');
  const report = JSON.parse(await readFile(output + 'results.json', 'utf8'));
  assert.deepEqual(report.metrics, job.result.metrics);
  assert.deepEqual(pageErrors, []);
  await page.screenshot({ path: output + 'completed.png', fullPage: true });
  console.log(JSON.stringify({ page: 'passed', api: 'passed', uploaded: cases.length, metrics: report.metrics, artifacts: output }, null, 2));
} finally {
  await browser.close();
}
