import type { Result } from '../api/client.js';
import type { RunReport } from '../api/runs.js';
import { element, table } from './dom.js';

const obj = (value: unknown): Result => value && typeof value === 'object' && !Array.isArray(value) ? value as Result : {};
const rows = (value: unknown): Result[] => Array.isArray(value) ? value.map(obj) : [];
const text = (value: unknown) => value == null ? '—' : String(value);
const number = (value: unknown) => typeof value === 'number' ? value : 0;
const levels: Record<string, string> = { year: '年', month: '月', day: '日', country: '国家', province: '省级', city: '城市', district: '区县' };
const levelOrder = Object.keys(levels);
export const granularityLabel = (value: unknown): string => levels[String(value)] ? `${levels[String(value)]}（${String(value)}）` : text(value);
const dimensionLabel = (value: unknown) => value === 'time' ? '时间' : value === 'space' ? '空间' : text(value);
const percent = (correct: number, total: number) => total ? `${(correct / total * 100).toFixed(2)}%` : '—';
const note = (host: HTMLElement, value: string) => host.append(element('p', 'result-note', value));

interface Comparison {
  documentId: string; filename: string; mention: string; dimension: string; granularity: string;
  predicted: unknown; gold: unknown; annotated: boolean; correct: boolean;
}

export function renderResolutionDetails(container: HTMLElement, report: RunReport): void {
  const section = element('section', 'resolution-details');
  const options = obj(report.options), calculation = obj(report.details.module_calculation);
  const normalized = obj(report.details.normalized_predictions);
  const comparisons = new Map<string, Comparison>();
  const key = (doc: unknown, mention: unknown, dimension: unknown, granularity: unknown) => JSON.stringify([doc, mention, dimension, granularity]);
  for (const item of rows(calculation.items)) {
    comparisons.set(key(item.sample_id, item.mention, item.dimension, item.granularity), {
      documentId: text(item.sample_id), filename: text(item.filename), mention: text(item.mention),
      dimension: text(item.dimension), granularity: text(item.granularity), predicted: item.predicted,
      gold: item.gold, annotated: true, correct: item.correct === true,
    });
  }
  const filenames = new Map(report.predictions.map(doc => [doc.document_id, doc.filename]));
  for (const [documentId, values] of Object.entries(normalized)) for (const item of rows(values)) {
    const id = key(documentId, item.mention, item.dimension, item.granularity);
    if (!comparisons.has(id)) comparisons.set(id, {
      documentId, filename: filenames.get(documentId) ?? documentId, mention: text(item.mention),
      dimension: text(item.dimension), granularity: text(item.granularity), predicted: item.value,
      gold: null, annotated: false, correct: false,
    });
  }
  const values = [...comparisons.values()];
  const mentionPositions = new Map<string, number>();
  const mentionKey = (row: Comparison) => JSON.stringify([row.documentId, row.dimension, row.mention]);
  for (const row of values) if (!mentionPositions.has(mentionKey(row))) mentionPositions.set(mentionKey(row), mentionPositions.size);
  values.sort((a, b) => mentionPositions.get(mentionKey(a))! - mentionPositions.get(mentionKey(b))! || levelOrder.indexOf(a.granularity) - levelOrder.indexOf(b.granularity));
  const groups = rows(report.metrics.by_granularity);
  const configured = new Set<string>();
  for (const [dimension, raw] of [['time', options.time_granularities], ['space', options.space_granularities]] as const) {
    if (Array.isArray(raw)) for (const level of raw) configured.add(JSON.stringify([dimension, level]));
  }
  const allGroups = new Set([...configured, ...groups.map(g => JSON.stringify([g.dimension, g.granularity])), ...values.map(v => JSON.stringify([v.dimension, v.granularity]))]);
  const sorted = [...allGroups].map(value => JSON.parse(value) as [string, string]).sort((a, b) => (a[0] === b[0] ? 0 : a[0] === 'time' ? -1 : 1) || levelOrder.indexOf(a[1]) - levelOrder.indexOf(b[1]));
  section.append(element('h3', '', '多粒度评测覆盖'));
  note(section, '时间按年、月、日分别比较；空间按国家、省级、城市、区县分别比较。同一个原文提及可以有多个粒度的标准值，每个已标注粒度独立计分。');
  section.append(table(['维度', '粒度', '评测状态', '正确/标注', '准确率', '预测项数', '本次配置'], sorted.map(([dimension, granularity]) => {
    const group = groups.find(g => g.dimension === dimension && g.granularity === granularity);
    const total = number(group?.total), correct = number(group?.correct);
    const predicted = values.filter(v => v.dimension === dimension && v.granularity === granularity && v.predicted != null).length;
    return [dimensionLabel(dimension), granularityLabel(granularity), total ? '已评测' : '未标注，不计分', `${correct}/${total}`, percent(correct, total), predicted, configured.has(JSON.stringify([dimension, granularity])) ? '已配置' : '未配置/历史数据'];
  }), '时空多粒度覆盖与准确率'));
  const evaluated = groups.filter(g => number(g.total) > 0).length;
  note(section, `本次有标注的“维度 × 粒度”共 ${evaluated} 组${configured.size ? `，配置共 ${configured.size} 组` : ''}。未标注粒度不显示为正确或错误；若要评价该粒度，需要在真实结果 JSON 中添加对应标注。`);

  section.append(element('h3', '', '消歧与标准化结果'));
  note(section, `时间参考日期：${options.reference_date ? text(options.reference_date) : '未设置'}。${report.input_mode === 'precomputed' ? '本次使用已有预测 JSON，只对照评分；参考日期仅展示上传配置，不在本次重新生成标准值。' : '相对时间按原文上下文及公开参考日期标准化，空间提及按配置粒度输出标准值；缺少依据时不补全。'}`);
  note(section, '下表直接展示“原文提及 → 粒度 → 预测标准值 → 标注标准值 → 判定”。当前报告不包含候选列表、推理链或置信度；这里展示实际标准化结果与冲突提示。');
  if (!Array.isArray(calculation.items)) {
    note(section, '此历史报告缺少提及级对照明细，可使用已有预测 JSON 和真实标注重新评分后查看。');
    container.append(section); return;
  }
  const warnings: (Result & { documentId: string; filename: string })[] = report.predictions.flatMap(doc => rows(obj(doc.graph.processing).resolution_warnings).map(warning => ({ ...warning, documentId: doc.document_id, filename: doc.filename })));
  const comparison = table(['文档 / 原文提及', '维度 / 粒度', '预测标准值', '标注标准值', '判定'], values.map(row => {
    const conflict = warnings.some(w => w.documentId === row.documentId && w.mention === row.mention && w.dimension === row.dimension && w.granularity === row.granularity && String(w.reason).includes('不一致'));
    const status = !row.annotated ? '未标注（不计分）' : row.correct ? '正确' : row.predicted == null ? conflict ? '冲突未定（计错）' : '漏预测（计错）' : '标准值不一致（计错）';
    return [`${row.filename}\n${row.mention}`, `${dimensionLabel(row.dimension)}\n${granularityLabel(row.granularity)}`, row.predicted == null ? '未预测' : text(row.predicted), row.annotated ? text(row.gold) : '未标注', status];
  }), '时空消歧与标准化逐项对照');
  comparison.classList.add('resolution-comparison');
  section.append(comparison);
  if (warnings.length) {
    section.append(element('h4', '', '冲突与无效输出提示'));
    section.append(table(['文档', '原文提及', '维度', '粒度', '处理说明'], warnings.map(w => [w.filename, text(w.mention), dimensionLabel(w.dimension), granularityLabel(w.granularity), text(w.reason)]), '时空标准化冲突提示'));
  }
  note(section, '正确数与分组分母沿用后端评分。额外预测不进入准确率分母，漏预测和已标注项的标准值不一致计为错误。');
  container.append(section);
}
