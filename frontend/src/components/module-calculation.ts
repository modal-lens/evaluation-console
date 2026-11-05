import type { Result } from '../api/client.js';
import { element, table } from './dom.js';
import { extractionFact } from './extraction-calculation.js';
import { granularityLabel } from './resolution-details.js';

const obj = (value: unknown): Result => value && typeof value === 'object' ? value as Result : {};
const rows = (value: unknown): Result[] => Array.isArray(value) ? value.map(obj) : [];
const num = (value: unknown): number => typeof value === 'number' && Number.isFinite(value) ? value : 0;
const pct = (value: unknown): string => `${(num(value) * 100).toFixed(2)}%`;
const text = (value: unknown): string => value == null ? '—' : String(value);
const note = (container: HTMLElement, value: string) => container.append(element('p', 'result-note', value));

function expandable(container: HTMLElement, title: string, render: (target: HTMLElement) => void): void {
  const detail = element('details', 'calculation-document');
  detail.append(element('summary', '', title));
  let rendered = false;
  detail.addEventListener('toggle', () => { if (detail.open && !rendered) { rendered = true; render(detail); } });
  container.append(detail);
}

function f1(container: HTMLElement, score: Result, title: string): void {
  const tp = num(score.tp), fp = num(score.fp), fn = num(score.fn);
  note(container, `${title}：TP = ${tp}，FP = ${fp}，FN = ${fn}；预测正例 ${tp + fp}，标注正例 ${tp + fn}。`);
  container.append(table(['指标', '公式与代入值', '结果'], [
    ['Precision', `TP / (TP + FP) = ${tp} / (${tp} + ${fp})`, pct(score.precision)],
    ['Recall', `TP / (TP + FN) = ${tp} / (${tp} + ${fn})`, pct(score.recall)],
    ['F1', `2 × TP / (2 × TP + FP + FN) = ${2 * tp} / (${2 * tp} + ${fp} + ${fn})`, pct(score.f1)],
  ], `${title}计算公式`));
}

function accuracy(container: HTMLElement, score: Result, title: string): void {
  container.append(table(['指标', '公式与代入值', '结果'], [[title, `正确数 / 总数 = ${num(score.correct)} / ${num(score.total)}`, pct(score.accuracy)]], `${title}计算公式`));
}

function identity(value: unknown): string {
  if (Array.isArray(value)) return value.map(text).join('［') + '］';
  try { const decoded: unknown = JSON.parse(String(value)); if (Array.isArray(decoded)) return `${text(decoded[0])}［${text(decoded[1])}］`; }
  catch { /* A legacy ID can be displayed as-is. */ }
  return text(value);
}

function selector(value: unknown): string {
  if (!Array.isArray(value)) return text(value);
  if (value.length === 2) return `${text(value[0])}［${text(value[1])}］`;
  return Array.isArray(value[2]) ? `${identity(value[0])} → ${text(value[1])} → ${identity(value[2])}`
    : `${identity(value[0])} · ${text(value[1])} = ${text(value[2])}`;
}

function fusion(container: HTMLElement, calculation: Result): void {
  const groups = Object.entries(obj(calculation.groups));
  note(container, '先按对象语义在各文档内对齐，再比较符合场景的对象对是否属于同一组。预测组编号与真实组编号无需相同。对象对 F1 与对象抽取覆盖 F1 分别计算。');
  if (groups.length === 3) {
    const values = groups.map(([, value]) => num(obj(obj(value).score).f1));
    container.append(table(['指标', '公式与代入值', '结果'], [['三项平均 F1', `(${values.map(value => value.toFixed(6)).join(' + ')}) / 3`, pct(values.reduce((a, b) => a + b, 0) / 3)]], '实体关系属性平均 F1 计算公式'));
  }
  for (const [, raw] of groups) {
    const group = obj(raw), score = obj(group.score), counts = obj(group.pair_counts);
    const title = score.object_type === 'entity' ? '实体' : score.object_type === 'relation' ? '关系' : '属性';
    const section = element('section', 'calculation-group'); section.append(element('h3', '', `${title}融合计算过程`));
    note(section, score.scope === 'cross-language' ? '仅比较语言不同的对象对；相同语言的对象对排除。' : '仅比较文档不同的对象对；同文档对象对排除。');
    const objects = rows(group.objects);
    section.append(table(['对象数', '全部对象对', '排除对象对', '可评测对象对', 'TP', 'FP', 'FN', 'TN'], [[objects.length, objects.length * (objects.length - 1) / 2, num(group.excluded_pairs), num(score.eligible_pairs), num(counts.tp), num(counts.fp), num(counts.fn), num(counts.tn)]], `${title}对象对计数`));
    note(section, 'TP：真实同组且预测同组；FP：真实不同组但预测同组；FN：真实同组但预测未同组；TN：真实不同组且预测未同组。TN 不进入 F1 公式。');
    f1(section, score, `${title}对象对 F1`);
    const coverage = obj(group.coverage_counts);
    note(section, `对象覆盖独立对照：匹配 ${num(coverage.matched)}，预测 ${num(coverage.predicted)}，标注 ${num(coverage.gold)}。覆盖 TP = 匹配数，FP = 预测数 − 匹配数，FN = 标注数 − 匹配数。`);
    f1(section, obj(score.object_coverage), `${title}对象覆盖 F1`);
    expandable(section, `查看${title}对象对齐与分组（${objects.length} 项）`, target => {
      const predictedGroups = new Map<unknown, string>(), goldGroups = new Map<unknown, string>();
      const label = (map: Map<unknown, string>, value: unknown, prefix: string) => { if (value == null) return '未分配'; if (!map.has(value)) map.set(value, `${prefix}${map.size + 1}`); return map.get(value)!; };
      target.append(table(['文档', '语言', '对象', '抽取对齐', '预测分组', '真实分组'], objects.map(row => [text(row.document_id), text(row.language), selector(row.selector), row.alignment === 'matched' ? '匹配' : row.alignment === 'missed' ? '漏抽' : '额外抽取', label(predictedGroups, row.predicted_cluster, '预测组'), row.alignment === 'extra' ? '无真实对象' : label(goldGroups, row.gold_cluster, '真实组')]), `${title}对象对齐与分组`));
    });
    const examples = obj(group.pair_examples);
    for (const status of ['tp', 'fp', 'fn', 'tn']) {
      const pairs = rows(examples[status]);
      expandable(section, `${status.toUpperCase()} 对象对：共 ${num(counts[status])} 对，展示 ${pairs.length} 对`, target => {
        note(target, `计数使用全部可评测对象对；明细为按计算顺序保留的前 ${num(group.examples_limit_per_status)} 对，避免大量组合导致页面卡顿。`);
        const name = (index: unknown) => { const row = objects[num(index)]; return row ? `${text(row.document_id)} / ${text(row.language)}：${selector(row.selector)}` : '—'; };
        target.append(table(['对象 A', '对象 B', '真实同组', '预测同组'], pairs.map(pair => [name(pair.left), name(pair.right), pair.gold_same_group ? '是' : '否', pair.predicted_same_group ? '是' : '否']), `${title}${status.toUpperCase()}对象对明细`));
      });
    }
    container.append(section);
  }
}

function domain(container: HTMLElement, calculation: Result): void {
  note(container, '对照实验：引入前不给模型实体、属性和关系类型列表；引入后提供配置列表。两组均与同一份完整真实标注比较，不把领域外实体当作引入前结果。');
  note(container, `领域类型列表：${Array.isArray(calculation.domain_entity_types) ? calculation.domain_entity_types.map(text).join('、') : '—'}。总体包含全部实体；领域内仅保留类型在此列表中的预测与标注。先在文档内去重匹配，再汇总计数。`);
  for (const [title, raw] of Object.entries({ ...obj(calculation.groups), ...obj(calculation.relation_groups) })) {
    const group = obj(raw), section = element('section', 'calculation-group');
    const kind = group.kind === 'relation' ? 'relation' : 'entity';
    const subject = kind === 'relation' ? '关系' : '实体';
    section.append(element('h3', '', title)); f1(section, obj(group.overall), `${title} F1`);
    section.append(table(['文档', `预测${subject}`, `标注${subject}`, 'TP', 'FP', 'FN', 'F1'], rows(group.documents).map(doc => { const score = obj(doc.score); return [text(doc.filename), num(doc.predicted_count), num(doc.gold_count), num(score.tp), num(score.fp), num(score.fn), pct(score.f1)]; }), `${title}逐文档计数`));
    for (const doc of rows(group.documents)) expandable(section, `${title} / ${text(doc.filename)}：${subject}对照`, target => {
      target.append(table(['判定', `预测${subject}`, `真实${subject}`], rows(doc.items).map(row => [text(row.status).toUpperCase(), extractionFact(row.predicted, kind), extractionFact(row.gold, kind)]), `${title}${subject}匹配明细：${text(doc.filename)}`));
    });
    container.append(section);
  }
  const groups = obj(calculation.groups);
  if (typeof calculation.f1_delta === 'number') {
    note(container, `F1 变化 =（当前总体 F1 − 引入前基线 F1）× 100 =（${num(obj(obj(groups['当前总体']).overall).f1).toFixed(6)} − ${num(obj(obj(groups['引入前基线']).overall).f1).toFixed(6)}）× 100 = ${(calculation.f1_delta * 100).toFixed(2)} 个百分点。`);
  } else note(container, '未提供基线预测，仅计算当前总体和领域内指标；不计算 F1 变化。');
  if (typeof calculation.relation_f1_delta === 'number') {
    const relationGroups = obj(calculation.relation_groups);
    note(container, `关系 F1 变化 =（引入后关系 F1 − 引入前关系 F1）× 100 =（${num(obj(obj(relationGroups['引入后关系']).overall).f1).toFixed(6)} − ${num(obj(obj(relationGroups['引入前关系']).overall).f1).toFixed(6)}）× 100 = ${(calculation.relation_f1_delta * 100).toFixed(2)} 个百分点。`);
  }
}

function classification(container: HTMLElement, calculation: Result): void {
  const metrics = obj(calculation.metrics), names = obj(calculation.label_names);
  const label = (id: unknown) => `${text(names[text(id)] ?? '未提供名称')}（${text(id)}）`;
  const labels = (values: unknown) => Array.isArray(values) ? values.map(label).join('、') || '无' : '无';
  note(container, '各文档的标签先去重。TP 为预测与标注标签交集，FP 为多预测的标签，FN 为漏预测的标签。不同文档独立计数，所有文档汇总后计算 Micro F1。');
  f1(container, obj(metrics.micro), 'Micro F1');
  container.append(table(['指标', '公式与代入值', '结果'], [
    ['Macro F1', `各标签 F1 之和 / 标签数 = ${num(calculation.macro_f1_sum).toFixed(6)} / ${num(calculation.label_count)}`, pct(metrics.macro_f1)],
    ['整组准确率', `预测与标注集合完全一致的文档数 / 文档总数 = ${num(calculation.exact_matches)} / ${num(calculation.total_documents)}`, pct(metrics.subset_accuracy)],
  ], '多标签汇总计算公式'));
  note(container, '每个类别分别用自己的 TP、FP、FN 代入相同的 Precision、Recall、F1 公式。Macro F1 的标签范围是预测和标注中出现过的标签并集，包含只被误报的标签；不是按标签出现次数加权。');
  container.append(table(['类别', 'TP', 'FP', 'FN', 'Precision', 'Recall', 'F1'], Object.entries(obj(metrics.per_label)).map(([id, raw]) => { const score = obj(raw); return [label(id), num(score.tp), num(score.fp), num(score.fn), pct(score.precision), pct(score.recall), pct(score.f1)]; }), '逐类别计算明细'));
  container.append(table(['文档', '预测类别', '真实类别', 'TP 标签', 'FP 标签', 'FN 标签', '完全一致', '预测重复数', '标注重复数'], rows(calculation.documents).map(doc => [text(doc.filename), labels(doc.predicted), labels(doc.gold), labels(doc.tp_labels), labels(doc.fp_labels), labels(doc.fn_labels), doc.exact_match ? '是' : '否', num(doc.predicted_duplicates_removed), num(doc.gold_duplicates_removed)]), '逐文档标签集合计算'));
}

function resolution(container: HTMLElement, calculation: Result): void {
  const metrics = obj(calculation.metrics);
  note(container, '按文档、原文提及、时空维度和粒度对齐，再精确比较预测标准值与人工标准值；无预测按错误计数。准确率的分母为人工标注项数。');
  accuracy(container, metrics, '总体时空准确率');
  container.append(table(['维度', '粒度', '正确数', '总数', '公式', '准确率'], rows(metrics.by_granularity).map(row => [row.dimension === 'time' ? '时间' : '空间', granularityLabel(row.granularity), num(row.correct), num(row.total), `${num(row.correct)} / ${num(row.total)}`, pct(row.accuracy)]), '各粒度准确率计算'));
  container.append(table(['文档', '原文提及', '维度', '粒度', '预测标准值', '真实标准值', '判定'], rows(calculation.items).map(row => [text(row.filename), text(row.mention), row.dimension === 'time' ? '时间' : '空间', granularityLabel(row.granularity), text(row.predicted), text(row.gold), row.correct ? '正确' : row.predicted == null ? '漏预测' : '标准值不一致']), '时空标准值逐项对照'));
  note(container, `另有 ${num(calculation.unscored_predictions)} 个预测提及未匹配到标注项。沿用当前评分方式：这些额外预测不进入准确率分母，也不另加扣分。`);
}

function community(container: HTMLElement, calculation: Result): void {
  note(container, '节点按实体名称和类型对齐，同名同类型节点跨文档去重。先构造预测社区 × 真实社区的节点重叠矩阵，再用匈牙利算法求最优一一映射，使正确节点总数最大。分组编号不同不直接判错，未分配节点计为错误。');
  accuracy(container, obj(calculation.metrics), '社区发现准确率');
  const mapping = obj(calculation.mapping);
  container.append(table(['预测社区', '最优对应真实社区'], Object.entries(mapping).map(([key, value]) => [key, text(value)]), '最优社区映射'));
  note(container, '若有多个同样最优的映射，此处展示实际评分采用的其中一个；正确总数和准确率一致。');
  expandable(container, '查看社区重叠矩阵', target => {
    const predicted = Array.isArray(calculation.predicted_groups) ? calculation.predicted_groups.map(text) : [];
    const gold = Array.isArray(calculation.gold_groups) ? calculation.gold_groups.map(text) : [];
    const weights = Array.isArray(calculation.weights) ? calculation.weights : [];
    target.append(table(['预测社区 / 真实社区', ...gold], predicted.map((name, index) => [name, ...(Array.isArray(weights[index]) ? weights[index].map(value => num(value)) : [])]), '社区重叠矩阵'));
  });
  expandable(container, `查看全部 ${rows(calculation.items).length} 个节点的判定`, target => {
    target.append(table(['实体', '预测社区', '映射后社区', '真实社区', '判定'], rows(calculation.items).map(row => [identity(row.node_id), text(row.predicted_community), text(row.mapped_community), text(row.gold_community), row.correct ? '正确' : row.predicted_community == null ? '未分配' : '映射后不匹配']), '社区节点逐项判定'));
  });
}

export function renderModuleCalculation(container: HTMLElement, value: unknown): void {
  const calculation = obj(value);
  if (!calculation.kind) { note(container, '此历史任务未保存计算明细，可用已有预测 JSON 和真实标注重新评分生成，无需重新调用算法。'); return; }
  const section = element('section', 'extraction-calculation');
  const button = element('button', 'button secondary', '查看详细计算过程'); button.type = 'button';
  const content = element('div', 'calculation-content'); content.id = 'module-calculation-content'; content.hidden = true;
  button.setAttribute('aria-controls', content.id); button.setAttribute('aria-expanded', 'false');
  let rendered = false;
  button.addEventListener('click', () => {
    content.hidden = !content.hidden; button.setAttribute('aria-expanded', String(!content.hidden));
    button.textContent = content.hidden ? '查看详细计算过程' : '收起详细计算过程';
    if (rendered) return; rendered = true;
    if (calculation.kind === 'fusion') fusion(content, calculation);
    else if (calculation.kind === 'domain') domain(content, calculation);
    else if (calculation.kind === 'classification') classification(content, calculation);
    else if (calculation.kind === 'resolution') resolution(content, calculation);
    else if (calculation.kind === 'community') community(content, calculation);
    if (['fusion', 'domain', 'classification'].includes(text(calculation.kind))) note(content, 'F1 / Precision / Recall 的公式分母为零时返回 0；展示百分比四舍五入到两位小数。');
  });
  section.append(button, content); container.append(section);
}
