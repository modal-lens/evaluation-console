import { test, expect } from '@playwright/test';

const metrics = [
  'entity-extraction', 'relation-extraction', 'domain-entity-extraction',
  'cross-document/entity-fusion', 'cross-document/relation-fusion', 'cross-document/attribute-fusion',
  'cross-language/entity-fusion', 'cross-language/relation-fusion', 'cross-language/attribute-fusion',
  'spatiotemporal-resolution', 'multilabel-classification', 'community-discovery',
];

for (const metric of metrics) test(`自动任务 ${metric}：上传原文与标注后展示预测和指标`, async ({ page }) => {
  await page.goto('/evaluation/' + metric);
  await page.getByRole('button', { name: '加载示例原文与标注', exact: true }).click();
  await expect(page.locator('.selected-files')).toContainText('document-1.txt');
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.getByRole('button', { name: '下载完整报告' })).toBeEnabled({ timeout: 15_000 });
  await expect(page.locator('.metric-value').first()).toBeVisible();
  await expect(page.locator('.job-stage')).toHaveText('评测完成');
  await expect(page).toHaveURL(/\?job=[a-f0-9]{32}$/);
  await page.getByText('查看算法预测结果', { exact: true }).click();
  await expect(page.locator('[data-raw=predictions] pre')).toContainText('doc-1');
  if (metric === 'entity-extraction') {
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: 'artifacts/automatic-desktop.png', fullPage: true });
    await page.reload();
    await expect(page.getByRole('button', { name: '下载完整报告' })).toBeEnabled();
    await expect(page.locator('.job-stage')).toHaveText('评测完成');
  }
});

test('上传真实文件与标注，下载包含实际原文、标注和预测的报告', async ({ page }) => {
  await page.goto('/evaluation/entity-extraction');
  const gold = { documents: [{ document_id: 'my-doc', filename: 'my-text.txt', language: 'zh', entities: [
    { entity_id: 'manual-1', name: '张三', type: '人物' },
    { entity_id: 'manual-2', name: '南京大学', type: '组织' },
  ] }] };
  await page.locator('[data-file=texts]').setInputFiles({ name: 'my-text.txt', mimeType: 'text/plain', buffer: Buffer.from('张三在南京大学任职。') });
  await page.locator('[data-file=gold]').setInputFiles({ name: 'gold.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(gold)) });
  await expect(page.getByRole('textbox', { name: '真实结果标注 JSON' })).toHaveValue(/manual-1/);
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.getByRole('button', { name: '下载完整报告' })).toBeEnabled();
  const waiting = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载完整报告' }).click();
  const download = await waiting;
  const stream = await download.createReadStream();
  const chunks = []; for await (const chunk of stream) chunks.push(chunk);
  const report = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  expect(report.documents[0].filename).toBe('my-text.txt');
  expect(report.annotations.documents[0].entities[0].entity_id).toBe('manual-1');
  expect(report.predictions[0].graph.entities[0].entity_id).toBe('p-person');
  expect(report.metrics.f1).toBe(.8);
  await page.getByRole('button', { name: '清理当前任务数据' }).click();
  await expect(page.locator('.result-content')).toContainText('已清理');
  await expect(page).not.toHaveURL(/\?job=/);
});

test('缺少文本、错误 JSON 和文件名不对应时明确报错', async ({ page }) => {
  await page.goto('/evaluation/entity-extraction');
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.getByRole('alert')).toContainText('选择原文');
  await page.getByRole('button', { name: '加载示例原文与标注' }).click();
  await expect(page.locator('.selected-files')).toContainText('document-1.txt');
  await page.getByRole('textbox', { name: '真实结果标注 JSON' }).fill('{');
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.getByRole('alert')).toContainText('JSON');
  await page.getByRole('textbox', { name: '真实结果标注 JSON' }).fill(JSON.stringify({ documents: [{ document_id: 'd', filename: 'wrong.txt' }] }));
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.getByRole('alert')).toContainText('一一对应');
});

test('手机自动上传页面无横向溢出，可完成评测', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/evaluation/spatiotemporal-resolution');
  await page.getByRole('button', { name: '加载示例原文与标注' }).click();
  await expect(page.locator('.selected-files')).toContainText('document-1.txt');
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.getByRole('button', { name: '下载完整报告' })).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'artifacts/automatic-mobile.png', fullPage: true });
});

test('新任务取消后保留旧报告且下载任务 ID 不串用', async ({ page }) => {
  await page.goto('/evaluation/entity-extraction');
  await page.getByRole('button', { name: '加载示例原文与标注' }).click();
  await expect(page.locator('.selected-files')).toContainText('document-1.txt');
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.getByRole('button', { name: '下载完整报告' })).toBeEnabled();
  const firstId = new URL(page.url()).searchParams.get('job');
  await page.locator('[data-file=texts]').setInputFiles({ name: 'document-1.txt', mimeType: 'text/plain', buffer: Buffer.from('[slow-test]张三在南京大学任职。') });
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await page.getByRole('button', { name: '取消任务', exact: true }).click();
  await expect(page.locator('.job-stage')).toContainText('已取消');
  await page.getByRole('button', { name: '高级评分参数' }).click();
  await page.getByRole('button', { name: '上传文本或抽取结果' }).click();
  await expect(page.getByRole('button', { name: '下载完整报告' })).toBeEnabled();
  await expect(page.locator('.metric-value').first()).toBeVisible();
  const waiting = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载完整报告' }).click();
  const file = await waiting;
  const stream = await file.createReadStream();
  const chunks = []; for await (const chunk of stream) chunks.push(chunk);
  const report = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  expect(report.job_id).toBe(firstId);
  expect(report.documents[0].text).not.toContain('[slow-test]');
});

test('清理响应等待期间不能提交新任务', async ({ page }) => {
  await page.goto('/evaluation/entity-extraction');
  await page.getByRole('button', { name: '加载示例原文与标注' }).click();
  await expect(page.locator('.selected-files')).toContainText('document-1.txt');
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.getByRole('button', { name: '下载完整报告' })).toBeEnabled();
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/jobs/*/delete', async route => {
    const response = await route.fetch();
    await gate;
    await route.fulfill({ response });
  });
  try {
    await page.getByRole('button', { name: '清理当前任务数据' }).click();
    await expect(page.getByRole('button', { name: '正在清理' })).toBeDisabled();
    await expect(page.getByRole('button', { name: '加载示例原文与标注' })).toBeDisabled();
  } finally { release(); }
  await expect(page.locator('.result-content')).toContainText('已清理');
  await expect(page.getByRole('button', { name: '开始自动预测与评测' })).toBeEnabled();
});

test('旧任务取消的延迟响应不覆盖新任务', async ({ page }) => {
  await page.goto('/evaluation/entity-extraction');
  await page.getByRole('button', { name: '加载示例原文与标注' }).click();
  await expect(page.locator('.selected-files')).toContainText('document-1.txt');
  await page.locator('[data-file=texts]').setInputFiles({ name: 'document-1.txt', mimeType: 'text/plain', buffer: Buffer.from('[slow-test]张三') });
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/jobs/*/cancel', async route => {
    const response = await route.fetch();
    await gate;
    await route.fulfill({ response });
  });
  let completedId;
  const responseArrived = page.waitForResponse(response => response.url().endsWith('/cancel'));
  try {
    await page.getByRole('button', { name: '取消任务', exact: true }).click();
    await expect(page.locator('.job-stage')).toContainText('已取消');
    await page.getByRole('button', { name: '加载示例原文与标注' }).click();
    await page.getByRole('button', { name: '开始自动预测与评测' }).click();
    await expect(page.getByRole('button', { name: '下载完整报告' })).toBeEnabled();
    completedId = new URL(page.url()).searchParams.get('job');
  } finally { release(); }
  await responseArrived;
  await expect(page.locator('.job-stage')).toHaveText('评测完成');
  await expect(page).toHaveURL(new RegExp(completedId + '$'));
});

test('提交前未返回的示例请求不能覆盖已提交的原文', async ({ page }) => {
  await page.goto('/evaluation/entity-extraction');
  await page.getByRole('button', { name: '加载示例原文与标注' }).click();
  await expect(page.locator('.selected-files')).toContainText('document-1.txt');
  await page.locator('[data-file=texts]').setInputFiles({ name: 'document-1.txt', mimeType: 'text/plain', buffer: Buffer.from('[slow-test]保留当前原文') });
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/templates/entity-extraction', async route => {
    const response = await route.fetch();
    await gate;
    await route.fulfill({ response });
  });
  try {
    await page.getByRole('button', { name: '加载示例原文与标注' }).click();
    await page.getByRole('button', { name: '开始自动预测与评测' }).click();
    await expect(page.locator('.job-progress')).toBeVisible();
  } finally { release(); }
  await expect(page.getByRole('button', { name: '下载完整报告' })).toBeEnabled();
  await expect(page.locator('.selected-files')).toContainText('0.0 KB');
});
