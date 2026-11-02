import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const directory = process.env.DOMAIN_COMPARISON_DATA;

test('领域双流程：显示前后实体关系、差值、详细过程和严格验收', async ({ page }) => {
  let gold, predictions, options, expected;
  if (directory) {
    const load = name => JSON.parse(readFileSync(resolve(directory, name), 'utf8'));
    gold = load('gold.json'); predictions = load('predictions.json'); options = load('options.json');
    expected = load('verification.json').metrics;
  } else {
    const { data } = await (await page.request.get('/api/v1/evaluation-runs/templates/domain-entity-extraction')).json();
    gold = data.annotations; options = data.options; predictions = structuredClone(data.prediction_example);
    predictions.baseline_documents = structuredClone(predictions.documents);
    predictions.baseline_documents[0].entities.pop();
    predictions.baseline_documents[0].relations = [];
  }
  await page.goto('/evaluation/domain-entity-extraction');
  await page.locator('[data-mode=precomputed]').click();
  for (const [field, data] of [['gold', gold], ['predictions', predictions], ['options', options]]) {
    await page.locator(`[data-file=${field}]`).setInputFiles({ name: `${field}.json`, mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(data)) });
  }
  await page.getByRole('button', { name: '使用已有结果计算指标' }).click();
  await expect(page.locator('[data-action=download]')).toBeEnabled({ timeout: 30_000 });
  await expect(page.getByRole('table', { name: '领域与基线比较', exact: true })).toContainText('引入前关系');
  await expect(page.getByRole('table', { name: '领域抽取指标验收', exact: true })).toContainText('95.00% < F1 < 100.00%');
  const details = page.locator('button[aria-controls=module-calculation-content]');
  await expect(details).toHaveAttribute('aria-expanded', 'false');
  await details.click();
  await expect(page.getByRole('table', { name: '引入前关系 F1计算公式', exact: true })).toBeVisible();
  await expect(page.getByText(/关系 F1 变化 =/)).toBeVisible();
  const downloadEvent = page.waitForEvent('download');
  await page.locator('[data-action=download]').click();
  const download = await downloadEvent;
  const chunks = []; for await (const chunk of await download.createReadStream()) chunks.push(chunk);
  const report = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  expect(report.input_mode).toBe('precomputed');
  if (expected) {
    expect(report.metrics).toEqual(expected);
    expect(report.metrics.acceptance.passed).toBe(true);
  } else {
    // The perfect configured sample must fail the strict upper bound.
    expect(report.metrics.acceptance.checks.configured_entity.status).toBe('failed');
  }
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await expect(page.getByRole('alert')).toBeHidden();
});
