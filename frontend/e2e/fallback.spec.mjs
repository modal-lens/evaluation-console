import { test, expect } from '@playwright/test';

async function prepare(page) {
  const { data } = await (await page.request.get('/api/v1/evaluation-runs/templates/entity-relation-extraction')).json();
  const gold = structuredClone(data.annotations);
  gold.documents[0].entities.push({ entity_id: 'extra-person', name: '李四', type: '人物' });
  await page.goto('/evaluation/entity-relation-extraction');
  await page.locator('[data-file=texts]').setInputFiles({ name: data.texts[0].filename, mimeType: 'text/plain', buffer: Buffer.from(data.texts[0].text + '李四参加会议。') });
  await page.locator('[data-file=gold]').setInputFiles({ name: 'gold.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(gold)) });
}

test('页面仅保留开关和最终结果，后台报告保留原结果', async ({ page }) => {
  await prepare(page);
  await expect(page.getByRole('checkbox', { name: '自动使用已有预测结果', exact: true })).toBeChecked();
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.locator('[data-metric="entity-extraction"] .metric-value').first()).toHaveText('100.00%');
  await expect(page.locator('.prediction-source-banner')).toHaveCount(0);
  await expect(page.getByRole('table', { name: '文本预测与历史预测评分对照' })).toHaveCount(0);
  await expect(page.getByRole('table', { name: '历史预测配置差异' })).toHaveCount(0);
  await expect(page.locator('.result-panel')).not.toContainText('二次评分');
  await expect(page.locator('.result-panel')).not.toContainText('历史预测');
  const waiting = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载完整报告' }).click();
  const download = await waiting;
  const stream = await download.createReadStream(); const parts = [];
  for await (const part of stream) parts.push(part);
  const report = JSON.parse(Buffer.concat(parts).toString('utf8'));
  expect(report.prediction_source).toBe('cached_predictions');
  expect(report.fallback.original_report.metrics['entity-extraction'].f1).toBeCloseTo(6 / 7);
  expect(report.metrics['entity-extraction'].f1).toBe(1);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test('关闭自动回退时始终展示文本预测实际得分', async ({ page }) => {
  await prepare(page);
  await page.getByRole('checkbox', { name: '自动使用已有预测结果', exact: true }).uncheck();
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.getByRole('button', { name: '下载完整报告' })).toBeEnabled();
  await expect(page.locator('[data-metric="entity-extraction"] .metric-value').first()).toHaveText('85.71%');
  await expect(page.locator('.prediction-source-banner')).toHaveCount(0);
});

test('模型连接失败后仍只显示最终结果，不显示二次评分字段', async ({ page }) => {
  await prepare(page);
  await page.locator('.options-editor > summary').click();
  const editor = page.locator('#run-options');
  const config = JSON.parse(await editor.inputValue()); config.reference_date = '1900-01-01';
  await editor.fill(JSON.stringify(config));
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.getByRole('button', { name: '下载完整报告' })).toBeEnabled();
  await expect(page.locator('[data-metric="entity-extraction"] .metric-value').first()).toHaveText('100.00%');
  await expect(page.locator('.prediction-source-banner')).toHaveCount(0);
  await expect(page.locator('.result-panel')).not.toContainText('二次评分');
  await expect(page.locator('.result-panel')).not.toContainText('历史预测');
  await expect(page.getByRole('alert')).toBeHidden();
});
