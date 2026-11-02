import { test, expect } from '@playwright/test';

for (const metric of ['cross-document/fusion', 'cross-language/fusion', 'domain-entity-extraction', 'spatiotemporal-resolution', 'multilabel-classification', 'community-discovery']) {
  test(`${metric}: 展开对应公式和判定明细，收起后保留结果`, async ({ page }) => {
    const { data } = await (await page.request.get(`/api/v1/evaluation-runs/templates/${metric}`)).json();
    const prediction = structuredClone(data.prediction_example);
    if (metric === 'domain-entity-extraction') {
      prediction.baseline_documents = structuredClone(prediction.documents);
      prediction.baseline_documents[0].entities.pop();
      prediction.baseline_documents[0].relations = [];
    } else if (metric === 'spatiotemporal-resolution') prediction.documents[0].resolutions = [];
    else if (metric === 'multilabel-classification') {
      prediction.label_names = { '1': '测试类别', extra: '额外类别' };
      prediction.documents[0].tags.push('extra');
    } else if (metric === 'community-discovery') prediction.documents[0].entities.at(-1).community = null;
    await page.goto(`/evaluation/${metric}`);
    await page.locator('[data-mode=precomputed]').click();
    await page.locator('[data-file=predictions]').setInputFiles({ name: 'predictions.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(prediction)) });
    await page.locator('[data-file=gold]').setInputFiles({ name: 'gold.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(data.annotations)) });
    await page.getByRole('button', { name: '使用已有结果计算指标' }).click();
    const button = page.getByRole('button', { name: '查看详细计算过程', exact: true });
    await expect(button).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#module-calculation-content')).toBeHidden();
    await button.click();
    if (metric.endsWith('/fusion')) {
      await expect(page.getByRole('table', { name: '实体关系属性平均 F1 计算公式' })).toContainText('100.00%');
      await expect(page.getByRole('table', { name: '实体对象对计数' })).toBeVisible();
      await expect(page.getByRole('table', { name: '属性对象覆盖 F1计算公式' })).toContainText('100.00%');
      await page.getByText('查看实体对象对齐与分组', { exact: false }).click();
      await expect(page.getByRole('table', { name: '实体对象对齐与分组' })).toContainText('预测组');
      await page.locator('.calculation-group').first().locator('summary').filter({ hasText: /^TP 对象对/ }).click();
      await expect(page.getByRole('table', { name: '实体TP对象对明细' })).toBeVisible();
      await expect(page.locator('#module-calculation-content')).toContainText(metric.startsWith('cross-language') ? '语言不同' : '文档不同');
    } else if (metric === 'domain-entity-extraction') {
      await expect(page.getByRole('table', { name: '领域内 F1计算公式' })).toBeVisible();
      await expect(page.getByRole('table', { name: '引入前基线 F1计算公式' })).toContainText('66.67%');
      await expect(page.locator('#module-calculation-content')).toContainText('33.33 个百分点');
      await page.locator('#module-calculation-content summary').first().click();
      await expect(page.getByRole('table', { name: '当前总体实体匹配明细：document-1.txt' })).toBeVisible();
    } else if (metric === 'spatiotemporal-resolution') {
      await expect(page.getByRole('table', { name: '总体时空准确率计算公式' })).toContainText('0 / 2');
      await expect(page.getByRole('table', { name: '时空标准值逐项对照' })).toContainText('漏预测');
      await expect(page.getByRole('table', { name: '各粒度准确率计算' })).toBeVisible();
    } else if (metric === 'multilabel-classification') {
      await expect(page.getByRole('table', { name: '多标签汇总计算公式' })).toContainText('50.00%');
      await expect(page.getByRole('table', { name: '逐文档标签集合计算' })).toContainText('额外类别（extra）');
      await expect(page.getByRole('table', { name: 'Micro F1计算公式' })).toContainText('66.67%');
    } else {
      await expect(page.getByRole('table', { name: '社区发现准确率计算公式' })).toContainText('2 / 3');
      await expect(page.getByRole('table', { name: '最优社区映射' })).toBeVisible();
      await page.getByText('查看社区重叠矩阵', { exact: true }).click();
      await expect(page.getByRole('table', { name: '社区重叠矩阵', exact: true })).toBeVisible();
      await page.getByText('查看全部 3 个节点的判定', { exact: true }).click();
      await expect(page.getByRole('table', { name: '社区节点逐项判定' })).toContainText('未分配');
    }
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    await page.getByRole('button', { name: '收起详细计算过程', exact: true }).click();
    await expect(page.locator('#module-calculation-content')).toBeHidden();
    await expect(page.getByRole('button', { name: '下载完整报告' })).toBeEnabled();
    await expect(page.getByRole('alert')).toBeHidden();
  });
}
