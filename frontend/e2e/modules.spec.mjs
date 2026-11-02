import { test, expect } from '@playwright/test';

const combined = [
  ['entity-relation-extraction', ['entity-extraction', 'relation-extraction']],
  ['cross-document/fusion', ['entity', 'relation', 'attribute'].map(kind => `cross-document/${kind}-fusion`)],
  ['cross-language/fusion', ['entity', 'relation', 'attribute'].map(kind => `cross-language/${kind}-fusion`)],
];

async function expectFusionAverageMatchesDetails(page, metrics) {
  const values = await Promise.all(metrics.map(async metric => {
    const text = await page.locator(`[data-metric="${metric}"] .metric-value`).first().textContent();
    return Number.parseFloat(text || '');
  }));
  const expected = `${(values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(2)}%`;
  await expect(page.locator('.fusion-average .metric-value')).toHaveText(expected);
}

for (const [module, metrics] of combined) test(`${module}：一次提交返回独立指标，刷新和报告下载完整`, async ({ page }) => {
  const submissions = [];
  page.on('request', request => {
    if (request.method() === 'POST' && request.url().includes('/api/v1/evaluation-runs/')) submissions.push(request.url());
  });
  await page.goto('/evaluation/' + module);
  await page.getByRole('button', { name: '加载示例原文与标注' }).click();
  await expect(page.locator('.selected-files')).toContainText('document-1.txt');
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.getByRole('button', { name: '下载完整报告' })).toBeEnabled();
  expect(submissions).toHaveLength(1);
  expect(submissions[0]).toContain('/evaluation-runs/' + module);
  if (module.endsWith('/fusion')) {
    await expect(page.getByRole('button', { name: '查看详细结果' })).toHaveAttribute('aria-expanded', 'false');
    for (const metric of metrics) await expect(page.locator(`[data-metric="${metric}"]`)).toBeHidden();
    await page.getByRole('button', { name: '查看详细结果' }).click();
    await expectFusionAverageMatchesDetails(page, metrics);
  }
  for (const metric of metrics) {
    const section = page.locator(`[data-metric="${metric}"]`);
    await expect(section).toBeVisible();
    await expect(section.locator('.metric-value').first()).not.toHaveText('—');
  }
  await expect(page.locator('.combined-result')).toHaveCount(metrics.length);
  const jobId = new URL(page.url()).searchParams.get('job');
  await page.reload();
  if (module.endsWith('/fusion')) {
    for (const metric of metrics) await expect(page.locator(`[data-metric="${metric}"]`)).toBeHidden();
    await page.getByRole('button', { name: '查看详细结果' }).click();
    await expectFusionAverageMatchesDetails(page, metrics);
  }
  await expect(page.locator('.combined-result')).toHaveCount(metrics.length);
  const waiting = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载完整报告' }).click();
  const file = await waiting;
  const stream = await file.createReadStream();
  const chunks = []; for await (const chunk of stream) chunks.push(chunk);
  const report = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  expect(report.job_id).toBe(jobId);
  expect(Object.keys(report.metrics)).toEqual(metrics);
  expect(report.predictions).toHaveLength(module === 'entity-relation-extraction' ? 1 : 2);
});

test('组合模块直接评分保留各指标输入与结果，并正确统计模块完成数', async ({ page }) => {
  await page.goto('/evaluation/entity-relation-extraction');
  await page.getByRole('button', { name: '高级评分参数' }).click();
  await page.getByRole('button', { name: '开始评测' }).click();
  await expect(page.locator('.result-content')).toContainText('66.67%');
  await page.getByRole('button', { name: '关系抽取', exact: true }).click();
  await page.getByRole('button', { name: '开始评测' }).click();
  await expect(page.locator('.result-content')).toContainText('100.00%');
  await page.getByRole('button', { name: '实体抽取', exact: true }).click();
  await expect(page.locator('.result-content')).toContainText('66.67%');
  await page.locator('.nav-home').click();
  await expect(page.locator('.completed-count')).toHaveText('1/ 7');
});

test('手机组合模块显示全部指标，无横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/evaluation/cross-document/fusion');
  await page.getByRole('button', { name: '加载示例原文与标注' }).click();
  await expect(page.locator('.selected-files')).toContainText('document-1.txt');
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.locator('.fusion-average')).toBeVisible();
  await expect(page.locator('[data-metric="cross-document/entity-fusion"]')).toBeHidden();
  await page.getByRole('button', { name: '查看详细结果' }).click();
  await expect(page.locator('.combined-result')).toHaveCount(3);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.screenshot({ path: 'artifacts/seven-modules-mobile.png', fullPage: true });
});
