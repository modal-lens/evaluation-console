import { test, expect } from '@playwright/test';

const metrics = [
  'entity-extraction', 'relation-extraction', 'domain-entity-extraction',
  'cross-document/entity-fusion', 'cross-document/relation-fusion', 'cross-document/attribute-fusion',
  'cross-language/entity-fusion', 'cross-language/relation-fusion', 'cross-language/attribute-fusion',
  'spatiotemporal-resolution', 'multilabel-classification', 'community-discovery',
];

test('自动评测页面从后端模板读取配置中的默认并发数', async ({ page }) => {
  await page.route('**/api/v1/evaluation-runs/templates/entity-extraction', async route => {
    const response = await route.fetch();
    const body = await response.json();
    body.data.options.max_workers = 6;
    await route.fulfill({ json: body });
  });
  await page.goto('/evaluation/entity-extraction');
  await expect(page.getByLabel('模型请求并发数', { exact: true })).toHaveValue('6');
});

test('模型并发数与运行配置同步并提交到后端', async ({ page }) => {
  await page.goto('/evaluation/entity-extraction');
  await page.getByRole('button', { name: '加载示例原文与标注', exact: true }).click();
  const workers = page.getByLabel('模型请求并发数', { exact: true });
  await expect(workers).toHaveValue('4');
  await workers.fill('0');
  await workers.press('Tab');
  await expect(page.getByRole('alert')).toContainText('1～8');
  await workers.fill('2');
  await workers.press('Tab');
  await expect(page.locator('#run-options')).toHaveValue(/"max_workers": 2/);
  await expect(page.locator('.config-summary')).toContainText('并发 2');
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.getByRole('button', { name: '下载完整报告' })).toBeEnabled();
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: '下载完整报告' }).click()]);
  const stream = await download.createReadStream();
  const chunks = []; for await (const chunk of stream) chunks.push(chunk);
  expect(JSON.parse(Buffer.concat(chunks).toString('utf8')).options.max_workers).toBe(2);
});

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

test('时空页面直接展示多粒度覆盖和消歧标准值对照', async ({ page }) => {
  await page.goto('/evaluation/spatiotemporal-resolution');
  await page.getByRole('button', { name: '加载示例原文与标注', exact: true }).click();
  await expect(page.locator('.selected-files')).toContainText('document-1.txt');
  await page.getByRole('button', { name: '开始自动预测与评测' }).click();
  await expect(page.getByRole('heading', { name: '多粒度评测覆盖', exact: true })).toBeVisible();
  const coverage = page.getByRole('table', { name: '时空多粒度覆盖与准确率', exact: true });
  await expect(coverage.locator('tbody tr')).toHaveCount(7);
  await expect(coverage.getByRole('row').filter({ hasText: '年（year）' })).toContainText('未标注，不计分');
  await expect(coverage.getByRole('row').filter({ hasText: '日（day）' })).toContainText('已评测');
  const comparison = page.getByRole('table', { name: '时空消歧与标准化逐项对照', exact: true });
  await expect(comparison).toContainText('2026年10月21日');
  await expect(comparison).toContainText('2026-10-21');
  await expect(comparison).toContainText('城市（city）');
  await expect(comparison).toContainText('正确');
  await page.locator('.resolution-details').screenshot({ path: 'artifacts/resolution-details.png' });
});

test('时空逐项对照区分错误、漏预测、冲突未定和未标注的预测', async ({ page }) => {
  const items = [
    { sample_id: 'd', filename: 'd.txt', mention: '昨天', dimension: 'time', granularity: 'year', predicted: '2026', gold: '2026', correct: true },
    { sample_id: 'd', filename: 'd.txt', mention: '昨天', dimension: 'time', granularity: 'month', predicted: '2026-09', gold: '2026-10', correct: false },
    { sample_id: 'd', filename: 'd.txt', mention: '昨天', dimension: 'time', granularity: 'day', predicted: null, gold: '2026-10-20', correct: false },
    { sample_id: 'd', filename: 'd.txt', mention: '某地', dimension: 'space', granularity: 'city', predicted: null, gold: '标注城市', correct: false },
  ];
  const metrics = { accuracy: .25, correct: 1, total: 4, by_granularity: items.map(row => ({ dimension: row.dimension, granularity: row.granularity, correct: row.correct ? 1 : 0, total: 1, accuracy: row.correct ? 1 : 0 })) };
  const job = { job_id: 'b'.repeat(32), status: 'succeeded', metric: 'spatiotemporal-resolution', stage: '评测完成', progress: 100, result: {
    metric: 'spatiotemporal-resolution', input_mode: 'text', metrics, annotations: {}, documents: [], options: { reference_date: '2026-10-21', time_granularities: ['year', 'month', 'day'], space_granularities: ['country', 'province', 'city', 'district'] },
    predictions: [{ document_id: 'd', filename: 'd.txt', graph: { processing: { resolution_warnings: [{ mention: '昨天', dimension: 'time', granularity: 'day', reason: '模型标准值不一致且缺少确定的原文解析，未输出该提及，评分时按未预测处理' }] } } }],
    details: { module_calculation: { kind: 'resolution', metrics, items }, normalized_predictions: { d: [{ mention: '额外地名', dimension: 'space', granularity: 'province', value: '预测省级值' }] } },
  } };
  await page.route(`**/api/v1/evaluation-runs/jobs/${job.job_id}`, route => route.fulfill({ json: { success: true, data: job } }));
  await page.goto('/evaluation/spatiotemporal-resolution?job=' + job.job_id);
  const table = page.getByRole('table', { name: '时空消歧与标准化逐项对照', exact: true });
  await expect(table.getByRole('row').filter({ hasText: '年（year）' })).toContainText('正确');
  await expect(table.getByRole('row').filter({ hasText: '月（month）' })).toContainText('标准值不一致（计错）');
  await expect(table.getByRole('row').filter({ hasText: '日（day）' })).toContainText('冲突未定（计错）');
  await expect(table.getByRole('row').filter({ hasText: '城市（city）' })).toContainText('漏预测（计错）');
  await expect(table.getByRole('row').filter({ hasText: '省级（province）' })).toContainText('未标注（不计分）');
  await expect(page.locator('.resolution-details')).toContainText('时间参考日期：2026-10-21');
  await expect(page.getByRole('table', { name: '时空标准化冲突提示', exact: true })).toContainText('模型标准值不一致');
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
