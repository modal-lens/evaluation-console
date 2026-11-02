import type { Result } from '../api/client.js';
import { element, table } from './dom.js';

const record = (value: unknown): Result => value && typeof value === 'object' ? value as Result : {};
const number = (value: unknown): number => typeof value === 'number' && Number.isFinite(value) ? value : 0;
const percent = (value: unknown): string => `${(number(value) * 100).toFixed(2)}%`;

function endpoint(value: unknown): string {
  const text = String(value ?? '');
  try {
    const decoded: unknown = JSON.parse(text);
    if (Array.isArray(decoded) && decoded.length === 3 && decoded[0] === 'entity') return `${String(decoded[1])}［${String(decoded[2])}］`;
  } catch { /* Older inputs may use plain endpoint IDs. */ }
  return text;
}

export function extractionFact(value: unknown, kind: string): string {
  if (!value) return '—';
  const row = record(value);
  return kind === 'entity' ? `${String(row.name)}［${String(row.type)}］`
    : `${endpoint(row.head_entity_id)} → ${String(row.relationCode)} → ${endpoint(row.tail_entity_id)}`;
}

export function renderExtractionCalculation(container: HTMLElement, value: unknown): void {
  const calculations = record(value);
  if (!Object.keys(calculations).length) {
    container.append(element('p', 'result-note', '此历史任务未保存计算明细，可用已有预测 JSON 和真实标注重新评分以生成详细过程。'));
    return;
  }
  const section = element('section', 'extraction-calculation');
  const toggle = element('button', 'button secondary', '查看详细计算过程');
  toggle.type = 'button'; toggle.setAttribute('aria-expanded', 'false');
  const content = element('div', 'calculation-content');
  content.id = 'extraction-calculation-content'; content.hidden = true;
  toggle.setAttribute('aria-controls', content.id);
  let rendered = false;
  toggle.addEventListener('click', () => {
    content.hidden = !content.hidden;
    toggle.setAttribute('aria-expanded', String(!content.hidden));
    toggle.textContent = content.hidden ? '查看详细计算过程' : '收起详细计算过程';
    if (rendered) return;
    rendered = true;
    content.append(element('p', 'result-note', '先在每篇文档内按匹配键去重并对齐，再汇总所有文档的 TP、FP、FN 计算微平均。不同文档中的同名对象独立计分，不直接平均各篇的 F1。TP 为预测与标注交集，FP 为仅预测存在，FN 为仅标注存在。'));
    for (const raw of Object.values(calculations)) {
      const calculation = record(raw);
      const kind = calculation.kind === 'entity' ? 'entity' : 'relation';
      const title = kind === 'entity' ? '实体' : '关系';
      const group = element('section', 'calculation-group');
      group.dataset.calculation = kind;
      group.append(element('h3', '', `${title}抽取：匹配与计算`));
      group.append(element('p', 'result-note', kind === 'entity'
        ? '匹配键 = 实体名称 + 实体类型。去除字段首尾空白后精确比较；名称相同但类型不同会产生一条误报和一条漏报。'
        : '匹配键 = 头实体（名称、类型）+ 关系类型 + 尾实体（名称、类型）。先把预测和标注各自的实体 ID 映射为名称及类型，再按方向精确比较，随机 ID 不必相同。关系方向相反会产生一条误报和一条漏报。'));
      const documents = Array.isArray(calculation.documents) ? calculation.documents.map(record) : [];
      const overall = record(calculation.overall);
      const tp = number(overall.tp), fp = number(overall.fp), fn = number(overall.fn);
      group.append(element('p', 'result-note', `全部文档去重后：预测 ${tp + fp} 项，真实标注 ${tp + fn} 项；TP = ${tp}，FP = ${fp}，FN = ${fn}。`));
      group.append(table(['指标', '计算公式与代入值', '结果'], [
        ['Precision', `TP / (TP + FP) = ${tp} / (${tp} + ${fp})`, percent(overall.precision)],
        ['Recall', `TP / (TP + FN) = ${tp} / (${tp} + ${fn})`, percent(overall.recall)],
        ['F1', `2 × TP / (2 × TP + FP + FN) = ${2 * tp} / (${2 * tp} + ${fp} + ${fn})`, percent(overall.f1)],
      ], `${title}指标计算公式`));
      group.append(element('p', 'result-note', '任一公式分母为 0 时，该指标按现有后端规则返回 0。百分比仅在展示时四舍五入到两位小数。'));
      group.append(table(['文档', '预测数', '标注数', '预测重复数', 'TP', 'FP', 'FN', 'F1'], documents.map(doc => {
        const score = record(doc.score);
        return [String(doc.filename), String(doc.predicted_count), String(doc.gold_count), String(doc.predicted_duplicates_removed), String(score.tp), String(score.fp), String(score.fn), percent(score.f1)];
      }), `${title}逐文档汇总`));
      for (const doc of documents) {
        const detail = element('details', 'calculation-document');
        detail.append(element('summary', '', `${String(doc.filename)}：查看${title}匹配明细`));
        detail.addEventListener('toggle', () => {
          if (!detail.open || detail.dataset.rendered) return;
          detail.dataset.rendered = 'true';
          detail.append(element('p', 'result-note', `原始预测 ${String(doc.predicted_raw_count)} 项，去重后 ${String(doc.predicted_count)} 项；原始标注 ${String(doc.gold_raw_count)} 项，去重后 ${String(doc.gold_count)} 项。`));
          const items = Array.isArray(doc.items) ? doc.items.map(record) : [];
          const status: Record<string, string> = { tp: 'TP · 正确', fp: 'FP · 误报', fn: 'FN · 漏报' };
          detail.append(table(['判定', '预测结果', '人工标注'], items.map(item => [status[String(item.status)] ?? '未知', extractionFact(item.predicted, kind), extractionFact(item.gold, kind)]), `${title}匹配明细：${String(doc.filename)}`));
        });
        group.append(detail);
      }
      content.append(group);
    }
  });
  section.append(toggle, content); container.append(section);
}
