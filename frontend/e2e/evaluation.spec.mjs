import { test, expect } from '@playwright/test';

// 已人工计算的现有后端示例预期，不从页面或接口响应推导。
const cases = [
  ['entity-extraction', '实体抽取', '66.67%'],
  ['relation-extraction', '关系抽取', '100.00%'],
  ['domain-entity-extraction', '专业领域实体抽取', '+100.00 pp'],
  ['cross-document/entity-fusion', '实体消融', '100.00%'],
  ['cross-document/relation-fusion', '关系消融', '100.00%'],
  ['cross-document/attribute-fusion', '属性消融', '100.00%'],
  ['cross-language/entity-fusion', '实体消融', '100.00%'],
  ['cross-language/relation-fusion', '关系消融', '100.00%'],
  ['cross-language/attribute-fusion', '属性消融', '100.00%'],
  ['spatiotemporal-resolution', '多粒度时空消歧', '66.67%'],
  ['multilabel-classification', '多标签分类', '80.00%'],
  ['community-discovery', '社区网络发现', '75.00%'],
];

test('概览保留 7 个算法模块并新增 SQL 工作流，分类筛选和菜单导航正常', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '选择一项评测' })).toBeVisible();
  await expect(page.locator('.catalog-card')).toHaveCount(8);
  await expect(page.locator('.overview-stats')).toContainText('保留 12 项指标');
  await page.screenshot({ path: 'artifacts/overview-desktop.png', fullPage: true });
  await page.getByRole('button', { name: '跨文档消融', exact: true }).click();
  await expect(page.locator('.catalog-card')).toHaveCount(1);
  await page.locator('.catalog-card').click();
  await expect(page).toHaveURL(/\/evaluation\/cross-document\/fusion$/);
  await expect(page.getByRole('heading', { name: '跨文档消融', exact: true })).toBeVisible();
});

for (const [slug, heading, expected] of cases) test(`${slug}：刷新后加载示例并调用真实接口`, async ({ page }) => {
  const consoleErrors = [];
  page.on('pageerror', error => consoleErrors.push(error.message));
  await page.goto(`/evaluation/${slug}`);
  await page.reload();
  await page.getByRole('button', { name: '高级评分参数', exact: true }).click();
  await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
  await page.getByRole('button', { name: '加载示例', exact: true }).click();
  const request = page.waitForResponse(response => response.url().endsWith(`/api/v1/evaluation/${slug}`) && response.request().method() === 'POST');
  await page.getByRole('button', { name: '开始评测' }).click();
  expect((await request).status()).toBe(200);
  await expect(page.locator('.result-content')).toContainText(expected);
  await expect(page.getByRole('button', { name: /下载结果/ })).toBeEnabled();
  if (slug === 'domain-entity-extraction') await page.screenshot({ path: 'artifacts/domain-desktop.png', fullPage: true });
  expect(consoleErrors).toEqual([]);
});

test('参数错误不发请求，后端业务错误可见，结果不会误报成功', async ({ page }) => {
  await page.goto('/evaluation/entity-extraction');
  await page.getByRole('button', { name: '高级评分参数', exact: true }).click();
  const editor = page.getByRole('textbox', { name: '请求参数 JSON' });
  let posts = 0;
  page.on('request', request => { if (request.method() === 'POST') posts++; });
  await editor.fill('{');
  await page.getByRole('button', { name: '开始评测' }).click();
  await expect(page.getByRole('alert')).toContainText('JSON 格式不正确');
  expect(posts).toBe(0);
  await editor.fill(JSON.stringify({ samples: [{ sample_id: 'x', predicted: [] }] }));
  await page.getByRole('button', { name: '开始评测' }).click();
  await expect(page.getByRole('alert')).toContainText('gold');
  expect(posts).toBe(0);
  await page.goto('/evaluation/cross-document/entity-fusion');
  await page.getByRole('button', { name: '高级评分参数', exact: true }).click();
  const payload = { items: [
    { item_id: 'a', document_id: 'same', language: 'zh', gold_cluster: 'g' },
    { item_id: 'b', document_id: 'same', language: 'en', gold_cluster: 'g' },
  ] };
  await page.getByRole('textbox', { name: '请求参数 JSON' }).fill(JSON.stringify(payload));
  await page.getByRole('button', { name: '开始评测' }).click();
  await expect(page.getByRole('alert')).toContainText('没有满足指定');
  await expect(page.locator('.result-status')).toContainText('未完成');
});

test('导入 JSON、下载结果以及编辑后的结果状态一致', async ({ page }) => {
  await page.goto('/evaluation/entity-extraction');
  await page.getByRole('button', { name: '高级评分参数', exact: true }).click();
  const payload = { samples: [{ sample_id: 'uploaded', predicted: [], gold: [{ name: '南京大学', type: '组织' }] }] };
  await page.locator('input[type=file]').setInputFiles({ name: 'evaluation.json', mimeType: 'application/json', buffer: Buffer.from('\uFEFF' + JSON.stringify(payload)) });
  await expect(page.getByRole('textbox', { name: '请求参数 JSON' })).toHaveValue(/uploaded/);
  await page.getByRole('button', { name: '开始评测' }).click();
  await expect(page.locator('.metric-value').first()).toHaveText('0.00%');
  await page.getByRole('textbox', { name: '请求参数 JSON' }).fill('{"samples":[]}');
  await expect(page.locator('.result-status')).toContainText('上一次提交');
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: /下载结果/ }).click();
  const downloaded = await downloadPromise;
  const stream = await downloaded.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const report = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  expect(report.request).toEqual(payload);
  expect(report.response.data.f1).toBe(0);
});

test('请求期间禁止重复提交，取消后可以再次评测，网络异常有提示', async ({ page }) => {
  await page.goto('/evaluation/entity-extraction');
  await page.getByRole('button', { name: '高级评分参数', exact: true }).click();
  await page.route('**/api/v1/evaluation/entity-extraction', async route => { await new Promise(resolve => setTimeout(resolve, 1000)); await route.continue().catch(() => {}); });
  await page.getByRole('button', { name: '开始评测' }).click();
  await expect(page.getByRole('button', { name: '正在评测' })).toBeDisabled();
  await page.getByRole('button', { name: '取消请求' }).click();
  await expect(page.getByRole('alert')).toContainText('已取消');
  await expect(page.getByRole('button', { name: '开始评测' })).toBeEnabled();
  await page.unrouteAll({ behavior: 'wait' });
  // 使用浏览器离线模式模拟实际网络断开，不受已取消拦截请求的释放时序影响。
  await page.context().setOffline(true);
  await page.getByRole('button', { name: '开始评测' }).click();
  await expect(page.getByRole('alert')).toContainText('无法连接');
  await page.context().setOffline(false);
});

test('小屏导航、结果表格与页面布局可用', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: '展开导航' }).click();
  await page.locator('.sidebar').getByRole('link', { name: /多标签分类/ }).click();
  await page.getByRole('button', { name: '高级评分参数', exact: true }).click();
  await page.getByRole('button', { name: '开始评测' }).click();
  await expect(page.locator('.result-content')).toContainText('80.00%');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  expect(overflow).toBe(false);
  await page.screenshot({ path: 'artifacts/classification-mobile.png', fullPage: true });
});
