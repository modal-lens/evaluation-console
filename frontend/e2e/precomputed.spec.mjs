import { test, expect } from '@playwright/test';

const modules = [
  'entity-relation-extraction', 'cross-document/fusion', 'cross-language/fusion',
  'domain-entity-extraction', 'spatiotemporal-resolution', 'multilabel-classification', 'community-discovery',
];

for (const metric of modules) test(`${metric}: 上传预测 JSON，无需原文并可下载复用`, async ({ page }) => {
  const response = await page.request.get(`/api/v1/evaluation-runs/templates/${metric}`);
  const { data } = await response.json();
  if (metric === 'multilabel-classification') data.prediction_example.label_names = { '1': '测试类别' };
  await page.addInitScript(() => {
    const originalFetch = window.fetch;
    window.fetch = (input, init) => {
      if (init?.body instanceof FormData) window.uploadedPredictionFields = Array.from(init.body.keys());
      return originalFetch(input, init);
    };
  });
  await page.goto(`/evaluation/${metric}`);
  await page.getByRole('button', { name: '上传抽取结果 JSON', exact: true }).first().click();
  await expect(page.locator('[data-input=text]')).toBeHidden();
  await page.locator('[data-file=predictions]').setInputFiles({
    name: 'predictions.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(data.prediction_example)),
  });
  await page.locator('[data-file=gold]').setInputFiles({
    name: 'gold.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(data.annotations)),
  });
  await expect(page.locator('#gold-json')).toHaveValue(/documents/);
  const submission = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith(`/evaluation-runs/${metric}`));
  await page.getByRole('button', { name: '使用已有结果计算指标' }).click();
  await submission;
  const fields = await page.evaluate(() => window.uploadedPredictionFields);
  expect(fields).toContain('predictions');
  expect(fields).not.toContain('texts');
  await expect(page.getByRole('button', { name: '下载抽取结果 JSON' })).toBeEnabled();
  if (metric === 'multilabel-classification') {
    await expect(page.getByRole('table', { name: '逐标签评测结果' })).toContainText('测试类别');
    await expect(page.getByRole('table', { name: '逐文档分类结果' })).toContainText('测试类别（1）');
  }
  const waiting = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载抽取结果 JSON' }).click();
  const file = await waiting;
  const stream = await file.createReadStream();
  const chunks = []; for await (const chunk of stream) chunks.push(chunk);
  const exported = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  expect(exported.metric).toBe(metric);
  expect(exported.prediction_input.documents).toHaveLength(data.annotations.documents.length);
  await expect(page.getByRole('alert')).toBeHidden();
});

test('已有结果模式缺少预测文件时阻止提交', async ({ page }) => {
  await page.goto('/evaluation/entity-relation-extraction');
  await page.getByRole('button', { name: '上传抽取结果 JSON', exact: true }).first().click();
  await page.getByRole('button', { name: '使用已有结果计算指标' }).click();
  await expect(page.getByRole('alert')).toContainText('请先上传已有抽取结果 JSON');
});

test('错传 options.json 提示运行配置位置，并支持直接上传配置', async ({ page }) => {
  const { data } = await (await page.request.get('/api/v1/evaluation-runs/templates/entity-relation-extraction')).json();
  await page.goto('/evaluation/entity-relation-extraction');
  const config = { ...data.options, block_size: 2345 };
  const file = { name: 'options.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(config)) };
  await page.locator('[data-file=gold]').setInputFiles(file);
  await expect(page.getByRole('alert')).toContainText('这是运行配置 options.json');
  await expect(page.locator('#gold-json')).toHaveValue('');
  await page.locator('[data-mode=precomputed]').click();
  await page.locator('[data-file=predictions]').setInputFiles(file);
  await expect(page.getByRole('alert')).toContainText('公开运行配置');
  await page.locator('.options-editor > summary').click();
  await page.locator('[data-file=options]').setInputFiles(file);
  await expect(page.locator('#run-options')).toHaveValue(/2345/);
  await expect(page.getByRole('alert')).toBeHidden();
});
