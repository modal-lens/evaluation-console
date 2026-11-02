import { test, expect } from '@playwright/test';

for (const mismatch of [false, true]) test(`专业领域实体清单：${mismatch ? '标明类型错分与误报漏检' : '标明领域类型与新增正确识别'}`, async ({ page }) => {
  const { data } = await (await page.request.get('/api/v1/evaluation-runs/templates/domain-entity-extraction')).json();
  const prediction = structuredClone(data.prediction_example);
  prediction.baseline_documents = structuredClone(prediction.documents);
  prediction.baseline_documents[0].entities.pop();
  prediction.baseline_documents[0].relations = [];
  if (mismatch) prediction.documents[0].entities[1].type = '技术';
  await page.goto('/evaluation/domain-entity-extraction');
  await page.locator('[data-mode=precomputed]').click();
  await page.locator('[data-file=predictions]').setInputFiles({ name: 'predictions.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(prediction)) });
  await page.locator('[data-file=gold]').setInputFiles({ name: 'gold.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(data.annotations)) });
  await page.getByRole('button', { name: '使用已有结果计算指标' }).click();
  await expect(page.getByRole('heading', { name: '领域内实体识别', exact: true })).toBeVisible();
  await expect(page.locator('.domain-type-tags')).toContainText('装备');
  await expect(page.locator('.domain-type-tags')).toContainText('技术');
  const entities = page.getByRole('table', { name: '领域内实体清单', exact: true });
  await expect(entities).toContainText('相控阵雷达');
  await expect(entities).not.toContainText('南京大学');
  if (mismatch) {
    await expect(entities).toContainText('领域误报');
    await expect(entities).toContainText('领域漏检');
    await expect(entities).not.toContainText('引入后新增正确识别');
  } else {
    await expect(entities).toContainText('正确识别');
    await expect(entities).toContainText('引入后新增正确识别');
    await expect(page.getByRole('table', { name: '各领域实体类型评测', exact: true })).toContainText('无样本');
  }
  await page.getByRole('button', { name: '查看全部实体的领域归属', exact: true }).click();
  const membership = page.getByRole('table', { name: '全部实体领域归属', exact: true });
  await expect(membership).toContainText('南京大学');
  await expect(membership).toContainText('领域外（仅计总体）');
  await expect(membership).toContainText('相控阵雷达');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});
