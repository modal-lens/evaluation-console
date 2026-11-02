import type { RunReport } from '../api/runs.js';
import type { Result } from '../api/client.js';
import { element, table } from './dom.js';

const obj = (value: unknown): Result => value && typeof value === 'object' ? value as Result : {};
const rows = (value: unknown): Result[] => Array.isArray(value) ? value.map(obj) : [];
const names = (value: unknown): string => Array.isArray(value) ? value.map(String).join('、') : '';
const num = (value: unknown): number => typeof value === 'number' ? value : 0;
const percent = (value: unknown): string => typeof value === 'number' ? `${(value * 100).toFixed(2)}%` : '—';

export function renderDomainEntities(container: HTMLElement, report: RunReport): void {
  const detail = obj(report.details.domain_entities);
  const section = element('section', 'domain-entities');
  section.append(element('h3', '', '领域内实体识别'));
  const types = Array.isArray(report.metrics.domain_entity_types) ? report.metrics.domain_entity_types.map(String) : [];
  const chips = element('div', 'domain-type-tags');
  for (const type of types) chips.append(element('span', 'domain-type-tag', type));
  section.append(chips);
  section.append(element('p', 'result-note', '以上类型属于本次专业领域；不在此列表中的类型仅进入总体统计。实体名称与类型同时匹配才计为正确。'));
  if (!Array.isArray(detail.entities)) {
    section.append(element('p', 'result-note', '此任务尚未保存领域实体清单，请重新评测后查看具体实体。'));
    container.append(section); return;
  }
  const score = obj(report.metrics.domain_only);
  section.append(element('p', 'domain-entity-summary', `领域标注 ${num(score.support)} 项 · 预测 ${num(score.tp) + num(score.fp)} 项 · 正确 ${num(score.tp)} 项 · 误报 ${num(score.fp)} 项 · 漏检 ${num(score.fn)} 项`));
  section.append(table(['领域类型', '正确 TP', '误报 FP', '漏检 FN', 'Precision', 'Recall', 'F1'], rows(detail.per_type).map(row => {
    const empty = num(row.tp) + num(row.fp) + num(row.fn) === 0;
    return [String(row.type), num(row.tp), num(row.fp), num(row.fn), empty ? '无样本' : percent(row.precision), empty ? '无样本' : percent(row.recall), empty ? '无样本' : percent(row.f1)];
  }), '各领域实体类型评测'));
  const statuses: Record<string, string> = { tp: '正确识别', fp: '领域误报', fn: '领域漏检' };
  const entities = rows(detail.entities);
  section.append(table(['实体名称', '领域类型', '预测类型', '真实类型', '评测结果', '基线对比', '来源文档'], entities.map(row => {
    const comparison = !detail.has_baseline ? '未提供基线' : row.new_correct_entity ? '引入后新增正确识别' : row.baseline_recognized ? '基线也预测为该类型' : '基线未预测为该类型';
    return [String(row.name), String(row.domain_type), names(row.predicted_types) || '未抽取', names(row.gold_types) || '未标注', statuses[String(row.status)] ?? '—', comparison, String(row.filename)];
  }), '领域内实体清单'));
  const showAll = element('button', 'button text-button', '查看全部实体的领域归属'); showAll.type = 'button';
  const all = element('div'); all.hidden = true; all.id = 'domain-membership-table';
  showAll.setAttribute('aria-controls', all.id); showAll.setAttribute('aria-expanded', 'false');
  let built = false;
  showAll.addEventListener('click', () => {
    all.hidden = !all.hidden; showAll.setAttribute('aria-expanded', String(!all.hidden));
    showAll.textContent = all.hidden ? '查看全部实体的领域归属' : '收起全部实体领域归属';
    if (built) return; built = true;
    const gold = obj(report.annotations), predicted = report.predictions;
    const goldDocs = new Map(rows(gold.documents).map(doc => [String(doc.document_id), doc]));
    const membership: string[][] = [];
    for (const document of predicted) {
      const matches = new Map<string, { name: string; type: string; predicted: boolean; gold: boolean }>();
      for (const [side, values] of [['predicted', rows(document.graph.entities)], ['gold', rows(goldDocs.get(document.document_id)?.entities)]] as const) {
        for (const value of values) {
          const name = String(value.name).trim(), type = String(value.type).trim(), key = JSON.stringify([name, type]);
          const row = matches.get(key) ?? { name, type, predicted: false, gold: false }; row[side] = true; matches.set(key, row);
        }
      }
      for (const row of matches.values()) membership.push([document.filename, row.name, row.type, types.includes(row.type) ? '领域内' : '领域外（仅计总体）', row.predicted ? '是' : '否', row.gold ? '是' : '否']);
    }
    all.append(table(['文档', '实体', '类型', '领域归属', '预测中存在', '标注中存在'], membership, '全部实体领域归属'));
  });
  section.append(showAll, all); container.append(section);
}
