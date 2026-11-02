import type { Result } from '../api/client.js';
import { metricPage, type PageDefinition, type ResultKind } from '../pages/catalog.js';
import { element, table } from './dom.js';

const score = (value: unknown): string => typeof value === 'number' ? `${(value * 100).toFixed(2)}%` : '—';
const count = (value: unknown): string => typeof value === 'number' ? value.toLocaleString('zh-CN') : '—';
const record = (value: unknown): Result => value && typeof value === 'object' ? value as Result : {};

function card(label: string, value: string, hint: string, primary = false): HTMLElement {
  const item = element('div', `metric-card${primary ? ' primary' : ''}`);
  item.append(element('div', 'metric-label', label), element('strong', 'metric-value', value), element('span', 'metric-hint', hint));
  return item;
}

function f1Cards(data: Result): HTMLElement {
  const grid = element('div', 'metrics-grid');
  grid.append(card('F1 分数', score(data.f1), '精确率与召回率的调和平均', true), card('Precision', score(data.precision), '预测正例中的正确比例'), card('Recall', score(data.recall), '标注正例的覆盖比例'));
  return grid;
}

function counts(data: Result): HTMLElement {
  const row = element('div', 'counts');
  for (const [key, label] of [['tp', '正确 TP'], ['fp', '误报 FP'], ['fn', '漏报 FN'], ['support', '标注正例']]) {
    const item = element('div'); item.append(element('span', '', label), element('strong', '', count(data[key!]))); row.append(item);
  }
  return row;
}

export function renderModuleResults(container: HTMLElement, page: PageDefinition, data: Result): void {
  if (!page.metrics) { renderResults(container, page.kind, data); return; }
  container.replaceChildren();
  const details = element('div', 'combined-details');
  if (page.kind === 'fusion') {
    const values = page.metrics.map(metric => record(data[metric]));
    const f1Values = values.map(value => value.f1).filter((value): value is number => typeof value === 'number');
    const average = f1Values.length === 3 ? f1Values.reduce((sum, value) => sum + value, 0) / 3 : undefined;
    const summary = element('div', 'fusion-average');
    summary.append(card('实体 / 关系 / 属性平均 F1', score(average), '三项 F1 的算术平均', true));
    const toggle = element('button', 'button secondary fusion-details-toggle', '查看详细结果');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', 'false');
    const detailsId = 'combined-fusion-details';
    details.id = detailsId;
    details.hidden = true;
    toggle.setAttribute('aria-controls', detailsId);
    toggle.addEventListener('click', () => {
      details.hidden = !details.hidden;
      toggle.setAttribute('aria-expanded', String(!details.hidden));
      toggle.textContent = details.hidden ? '查看详细结果' : '收起详细结果';
    });
    container.append(summary, toggle, details);
  }
  for (const metric of page.metrics) {
    const child = metricPage(metric);
    const section = element('section', 'combined-result');
    section.dataset.metric = metric;
    section.append(element('h3', '', child.title + ' F1'));
    const content = element('div');
    renderResults(content, child.kind, record(data[metric]));
    section.append(content); (page.kind === 'fusion' ? details : container).append(section);
  }
}

export function renderResults(container: HTMLElement, kind: ResultKind, data: Result): void {
  container.replaceChildren();
  if (kind === 'extraction' || kind === 'fusion') {
    container.append(f1Cards(data), counts(data));
    if (kind === 'fusion') {
      container.append(element('p', 'result-note', `符合场景的对象对：${count(data.eligible_pairs)} · 按对象对计算，不比较簇编号文本。`));
      if (data.object_coverage) {
        const coverage = record(data.object_coverage);
        container.append(element('p', 'result-note', '对象抽取覆盖：单独衡量对象是否被抽取，避免合并 F1 掩盖漏抽。'));
        container.append(table(['对象覆盖 F1', 'Precision', 'Recall', '正确', '额外', '漏抽'], [[score(coverage.f1), score(coverage.precision), score(coverage.recall), count(coverage.tp), count(coverage.fp), count(coverage.fn)]], '消融对象抽取覆盖'));
      }
    }
  } else if (kind === 'domain') {
    const overall = record(data.overall);
    const domain = record(data.domain_only);
    const baseline = record(data.baseline);
    const relations = record(data.relations);
    const baselineRelations = record(data.baseline_relations);
    const grid = element('div', 'metrics-grid');
    const delta = (value: unknown): string => typeof value === 'number' ? `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)} pp` : '未提供基线';
    grid.append(card('引入后实体 F1', score(overall.f1), '全部实体类型', true), card('领域内 F1', score(domain.f1), Array.isArray(data.domain_entity_types) ? data.domain_entity_types.map(String).join('、') : '指定领域类型'), card('引入前实体 F1', data.baseline ? score(baseline.f1) : '未提供基线', '不引入类型列表的基线'), card('实体 F1 变化', delta(data.f1_delta), '引入后减引入前，以百分点表示'));
    if (data.relations) grid.append(card('引入前关系 F1', baselineRelations.support ? score(baselineRelations.f1) : '未评估', '需提供基线预测和关系真实标注'), card('引入后关系 F1', relations.support ? score(relations.f1) : '无关系标注', '使用相同关系真实标注'), card('关系 F1 变化', relations.support && baselineRelations.support ? delta(data.relation_f1_delta) : '未评估', '引入后减引入前，以百分点表示'));
    container.append(grid);
    container.append(element('p', 'result-note', '文本模式：引入前不传实体、属性、关系类型列表，开放关系名按原文作通用规范化；引入后使用配置列表。两组对照同一份完整标注。已有结果模式只按上传预测计算，原始生成配置需由上传方确认。'));
    const rows = [['引入后实体（总体）', overall], ['引入后实体（领域内）', domain], ...(data.baseline ? [['引入前实体', baseline]] : []), ...(data.relations ? [['引入后关系', relations]] : []), ...(data.baseline_relations ? [['引入前关系', baselineRelations]] : [])] as [string, Result][];
    container.append(table(['范围', 'F1', 'Precision', 'Recall', 'TP', 'FP', 'FN'], rows.map(([label, item]) => [label, score(item.f1), score(item.precision), score(item.recall), count(item.tp), count(item.fp), count(item.fn)]), '领域与基线比较'));
    const acceptance = record(data.acceptance);
    if (acceptance.checks) {
      const names: Record<string, string> = { baseline_entity: '引入前实体 F1', baseline_relation: '引入前关系 F1', configured_entity: '引入后实体 F1' };
      container.append(table(['指标', '要求', '实际值', '判定'], Object.entries(record(acceptance.checks)).map(([key, raw]) => {
        const row = record(raw);
        return [names[key] ?? key, `${score(row.lower_exclusive)} < F1${typeof row.upper_exclusive === 'number' ? ` < ${score(row.upper_exclusive)}` : ''}`, row.f1 == null ? '未评估' : score(row.f1), row.status === 'passed' ? '达标' : row.status === 'not_evaluable' ? '缺少基线或真实标注' : '未达标'];
      }), '领域抽取指标验收'));
    }
  } else if (kind === 'classification') {
    const micro = record(data.micro);
    const grid = element('div', 'metrics-grid');
    grid.append(card('Micro F1', score(micro.f1), '汇总所有标签的 TP / FP / FN', true), card('Macro F1', score(data.macro_f1), '各标签 F1 的算术平均'), card('整组准确率', score(data.subset_accuracy), '样本标签集合完全一致'));
    container.append(grid, counts(micro));
    const names = record(data.label_names);
    container.append(table(['类别 ID', '类别名称', 'F1', 'Precision', 'Recall', '标注正例'], Object.entries(record(data.per_label)).map(([label, item]) => { const value = record(item); return [label, String(names[label] ?? '未提供名称'), score(value.f1), score(value.precision), score(value.recall), count(value.support)]; }), '逐标签评测结果'));
    if (Array.isArray(data.classification_samples)) {
      const labels = (items: unknown): string => Array.isArray(items) ? items.map(item => { const tag = record(item); return `${String(tag.name ?? '未提供名称')}（${String(tag.id)}）`; }).join('、') || '无' : '无';
      container.append(table(['文档', '预测类别', '真实类别'], data.classification_samples.map(item => { const row = record(item); return [String(row.filename), labels(row.predicted), labels(row.gold)]; }), '逐文档分类结果'));
    }
  } else {
    const grid = element('div', 'metrics-grid');
    grid.append(card('准确率', score(data.accuracy), kind === 'community' ? '社区标签最优匹配后的正确比例' : '相同维度与粒度下精确匹配', true), card('正确数量', count(data.correct), kind === 'community' ? '匹配正确的节点' : '消歧正确的标注'), card('标注总数', count(data.total), '包含漏预测项'));
    container.append(grid);
    if (kind === 'resolution') container.append(table(['维度', '粒度', '准确率', '正确', '总数'], (Array.isArray(data.by_granularity) ? data.by_granularity : []).map(item => { const row = record(item); return [row.dimension === 'time' ? '时间' : '空间', String(row.granularity), score(row.accuracy), count(row.correct), count(row.total)]; }), '时空消歧粒度明细'));
    else container.append(element('p', 'result-note', '采用社区标签最优一一匹配。未分配节点计为错误，社区编号不同不影响正确分组的得分。'));
  }
}
