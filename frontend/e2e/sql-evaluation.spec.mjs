import { test, expect } from '@playwright/test';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';

const cases = JSON.parse(await readFile(new URL('../../data/sql_evaluation/test_cases_100.json', import.meta.url), 'utf8'));

test('SQL page loads its worker default from backend configuration', async ({ page }) => {
  await page.route('**/api/v1/sql-evaluation/config', route => route.fulfill({ json: { success: true, data: { max_workers: 6 } } }));
  await page.goto('/evaluation/natural-language-sql');
  await expect(page.getByLabel('并发数', { exact: true })).toHaveValue('6');
});

test('SQL page runs a local Python job, scores a workflow response, and downloads the report', async ({ page }) => {
  const submissions = [];
  const server = createServer(async (req, res) => {
    res.setHeader('Content-Type', 'application/json');
    if (req.method === 'GET') {
      res.end(JSON.stringify({ data: { nodes: [{ id: 'input' }, { id: 'sql' }, { id: 'output' }], edges: [] } }));
      return;
    }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    submissions.push(JSON.parse(Buffer.concat(chunks).toString()));
    res.end(JSON.stringify({ outputs: [{ outputs: [{ results: { message: { text: 'SELECT COUNT(*) FROM USER_TABLES;' } } }] }] }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    await page.goto('/evaluation/natural-language-sql');
    await expect(page.getByRole('heading', { name: '自然语言转 SQL 评测', exact: true })).toBeVisible();
    await page.locator('.sql-logic > summary').click();
    await expect(page.locator('.sql-logic')).toContainText('总体结果等价率 = ΣCᵢ / N');
    await expect(page.locator('.dataset-rows > details')).toHaveCount(0);
    await expect(page.getByRole('button', { name: '开始本地脚本评测' })).toBeDisabled();
    const uploaded = { ...cases[0], id: 'browser-upload-case', input: { ...cases[0].input, question: '上传问题：当前用户有多少张表？' } };
    await page.getByLabel('上传测试数据 JSON', { exact: true }).setInputFiles({ name: 'custom.json', mimeType: 'application/json', buffer: Buffer.from('\uFEFF' + JSON.stringify([uploaded])) });
    await expect(page.locator('.dataset-rows > details')).toHaveCount(1);
    await expect(page.locator('.dataset-status')).toContainText('custom.json');
    await expect(page.locator('.dataset-rows')).toContainText(uploaded.input.question);
    await page.getByLabel('工作流服务地址').fill(`http://127.0.0.1:${server.address().port}`);
    await page.getByLabel('工作流 API Key', { exact: true }).fill('browser-test-key');
    await page.getByLabel('数据库', { exact: true }).selectOption('Oracle');
    await page.getByLabel('最多评测条数').fill('1');
    await page.getByRole('button', { name: '开始本地脚本评测' }).click();
    await expect(page.locator('.result-status')).toContainText('评测完成', { timeout: 25_000 });
    await expect(page.locator('.sql-results')).toContainText('100.0%');
    await expect(page.locator('.sql-results')).toContainText('工作流节点、连线与配置未变化');
    await expect(page.getByRole('table', { name: 'SQL逐条预测正确性' })).toContainText('✓ 正确');
    await expect(page.getByRole('table', { name: 'SQL逐条预测正确性' })).toContainText('三组结果全部匹配');
    await expect(page.getByLabel('工作流 API Key', { exact: true })).toHaveValue('');
    await page.getByRole('button', { name: '查看详细计算过程', exact: true }).click();
    await expect(page.getByRole('table', { name: 'SQL总体指标计算公式' })).toContainText('1 / 1 × 100%');
    await page.locator('.sql-case-calculation > summary').click();
    await expect(page.locator('.sql-case-calculation')).toContainText('Cᵢ = 1');
    await expect(page.getByRole('table', { name: 'browser-upload-case三组判定' })).toContainText('匹配（1）');
    await page.getByText('模拟数据组 1：匹配（1）', { exact: true }).click();
    await expect(page.locator('.sql-case-calculation')).toContainText('预期结果（完整，按行和列位置）');
    await expect(page.locator('.sql-case-calculation')).toContainText('实际执行的 SQLite SQL');
    expect(submissions).toHaveLength(1);
    const input = JSON.parse(submissions[0].input_request.input_value);
    expect(Object.keys(input).sort()).toEqual(['database', 'question', 'schema']);
    expect(input.question).toBe(uploaded.input.question);
    const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: '完整 JSON' }).click()]);
    expect(download.suggestedFilename()).toBe('results.json');
    await page.screenshot({ path: 'artifacts/sql-evaluation.png', fullPage: false });
    await page.locator('.nav-home').click();
    await expect(page.locator('.catalog-card')).toHaveCount(8);
    await page.getByRole('button', { name: '工作流评测', exact: true }).click();
    await expect(page.locator('.catalog-card')).toHaveCount(1);
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});

test('SQL dataset import validates files, preserves valid data on failure, and clears data', async ({ page }) => {
  await page.goto('/evaluation/natural-language-sql');
  const upload = page.getByLabel('上传测试数据 JSON', { exact: true });
  const set = async value => upload.setInputFiles({ name: 'cases.json', mimeType: 'application/json', buffer: Buffer.from(value) });
  await set('{');
  await expect(page.getByRole('alert')).toContainText('JSON 格式不正确');
  await set(JSON.stringify([cases[0]]));
  await expect(page.locator('.dataset-rows > details')).toHaveCount(1);
  await set(JSON.stringify([cases[0], cases[0]]));
  await expect(page.getByRole('alert')).toContainText('ID 重复');
  await expect(page.locator('.dataset-rows > details')).toHaveCount(1);
  await set(JSON.stringify([{ ...cases[0], expected_results: {} }]));
  await expect(page.getByRole('alert')).toContainText('expected_results');
  await page.getByRole('button', { name: '清空数据', exact: true }).click();
  await expect(page.locator('.dataset-rows > details')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '开始本地脚本评测' })).toBeDisabled();
});

test('SQL calculation shows a failed comparison and supports historical reports', async ({ page }) => {
  const fixture = (seed, expected) => ({ seed, status: expected === 3 ? 'matched' : 'mismatched', actual_rows: [[3]], expected_rows: [[expected]], first_difference: expected === 3 ? null : { row_number: 1, actual_row: [3], expected_row: [expected] }, error: '' });
  const item = {
    id: 'partial-match', database: 'Oracle', question: '三组对照测试', standard_sql: 'SELECT COUNT(*) FROM USER_TABLES', generated_sql: 'SELECT 3',
    syntax_valid: true, exact_match: false, execution_match: false, error: 'fixture 1 查询结果不同',
    calculation: { version: 1, stage: 'complete', normalized_generated_sql: 'SELECT 3', normalized_standard_sql: 'SELECT COUNT(*) FROM user_tables', sqlite_sql: 'SELECT 3', fixtures: [fixture(0, 3), fixture(1, 4), fixture(2, 5)] },
  };
  const job = {
    job_id: 'a'.repeat(32), status: 'succeeded', total: 1, completed: 1, created_at: '2026-11-03', error: '',
    result: { metrics: { n: 1, execution_correct: 0, syntax_pass: 1, exact_match: 0, accuracy_percent: 0, by_database: { Oracle: { n: 1, correct: 0, syntax_pass: 1 } } }, results: [item] },
  };
  await page.route('**/api/v1/sql-evaluation/jobs**', route => route.fulfill({ json: { success: true, data: route.request().url().endsWith('/jobs') ? [job] : job } }));
  await page.goto('/evaluation/natural-language-sql');
  await page.locator('.history-items button').click();
  await expect(page.getByRole('table', { name: 'SQL逐条预测正确性' })).toContainText('✕ 不正确');
  await expect(page.getByRole('table', { name: 'SQL逐条预测正确性' })).toContainText('fixture 1 查询结果不同');
  await page.getByRole('button', { name: '查看详细计算过程', exact: true }).click();
  await page.locator('.sql-case-calculation > summary').click();
  await expect(page.locator('.sql-case-calculation')).toContainText('Cᵢ = 0');
  await page.getByText('模拟数据组 1：不匹配（0）', { exact: true }).click();
  await expect(page.locator('.sql-case-calculation')).toContainText('首个差异：第 1 行；实际 [3]；预期 [4]');
  await page.locator('.sql-calculation').screenshot({ path: 'artifacts/sql-calculation.png' });
  delete item.calculation;
  await page.reload();
  await page.locator('.history-items button').click();
  await page.getByRole('button', { name: '查看详细计算过程', exact: true }).click();
  await page.locator('.sql-case-calculation > summary').click();
  await expect(page.locator('.sql-case-calculation')).toContainText('此历史任务未保存三组计算明细');
});
