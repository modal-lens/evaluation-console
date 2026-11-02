import { test, expect } from '@playwright/test';

test('上传原文标注不会悄悄替换默认配置，页面明确提示上传配套 options.json', async ({ page }) => {
  const { data } = await (await page.request.get('/api/v1/evaluation-runs/templates/entity-relation-extraction')).json();
  await page.goto('/evaluation/entity-relation-extraction');
  await expect(page.locator('[data-action=submit]')).toBeEnabled();
  await page.locator('[data-file=gold]').setInputFiles({ name: 'gold.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(data.annotations)) });
  await expect(page.locator('.config-summary')).toBeVisible();
  await expect(page.locator('.config-summary')).toContainText('页面默认示例配置');
  await expect(page.locator('.config-summary')).toContainText('同目录 options.json');
  await expect(page.locator('.config-summary')).toContainText('1000');
});

test('上传和编辑配置的来源可见，实际提交仍使用用户给定配置', async ({ page }) => {
  const { data } = await (await page.request.get('/api/v1/evaluation-runs/templates/entity-relation-extraction')).json();
  await page.goto('/evaluation/entity-relation-extraction');
  await page.getByRole('button', { name: '加载示例原文与标注', exact: true }).click();
  await expect(page.locator('.selected-files')).toContainText('document-1.txt');
  const config = { ...data.options, block_size: 4000, entity_attributes: {} };
  await page.locator('[data-file=options]').setInputFiles({ name: 'options.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(config)) });
  await expect(page.locator('.config-summary')).toContainText('已上传 options.json');
  await expect(page.locator('.config-summary')).toContainText('4000');
  await expect(page.locator('.config-summary')).toContainText('属性配置 0 项');
  await page.locator('.options-editor > summary').click();
  config.block_size = 3500;
  await page.locator('#run-options').fill(JSON.stringify(config));
  await expect(page.locator('.config-summary')).toContainText('手工编辑的配置');
  await expect(page.locator('.config-summary')).not.toContainText('已上传');
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.locator('[data-action=download]')).toBeEnabled();
  const jobId = new URL(page.url()).searchParams.get('job');
  const job = await (await page.request.get(`/api/v1/evaluation-runs/jobs/${jobId}`)).json();
  expect(job.data.result.options).toEqual(config);
});
