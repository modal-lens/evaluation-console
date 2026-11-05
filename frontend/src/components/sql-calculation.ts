import { element, table } from './dom.js';

type Rows = (string | number | boolean | null)[][];
interface FixtureCalculation { seed: number; status: 'matched' | 'mismatched' | 'error' | 'skipped'; actual_rows: Rows | null; expected_rows: Rows; first_difference: { row_number: number; actual_row: Rows[number] | null; expected_row: Rows[number] | null } | null; error: string }
export interface SQLPrediction {
  id: string; database: string; question: string; standard_sql: string; generated_sql: string;
  syntax_valid: boolean; exact_match: boolean; execution_match: boolean; error: string;
  calculation?: { version: number; stage: string; normalized_generated_sql: string | null; normalized_standard_sql: string | null; sqlite_sql: string | null; fixtures: FixtureCalculation[] };
}
export interface SQLMetrics {
  n: number; execution_correct: number; syntax_pass: number; exact_match?: number; accuracy_percent: number; flow_unchanged?: boolean;
  by_database: Record<string, { n: number; correct: number; syntax_pass: number }>;
  by_kind?: Record<string, { n: number; correct: number }>;
}
const note = (host: HTMLElement, text: string) => host.append(element('p', 'result-note', text));
const percent = (correct: number, total: number) => total ? `${(correct / total * 100).toFixed(2)}%` : '—';
const verdict = (value: boolean) => value ? '1（通过）' : '0（未通过）';
const stages: Record<string, string> = { workflow: '工作流请求', extract: 'SQL 提取', parse: '方言解析与只读检查', normalize: '标准 SQL 解析与规范化', translate: '转换为 SQLite SQL', execute: '模拟数据库执行', complete: '三组结果比较完成' };

export function sqlCorrectnessBadge(correct: boolean): HTMLElement {
  return element('span', `sql-correctness ${correct ? 'is-correct' : 'is-incorrect'}`, correct ? '✓ 正确' : '✕ 不正确');
}

export function renderSQLPredictions(host: HTMLElement, predictions: SQLPrediction[]): void {
  const section = element('section', 'sql-prediction-list');
  section.append(element('h3', '', '每条预测结果的正确性'));
  note(section, '按三组模拟数据的结果等价判定；三组全部匹配标为正确，否则标为不正确。转换或执行失败的原因也会在下方列出。');
  const list = table(['序号', '用例 ID', '数据库', '问题', '是否正确', '原因'], predictions.map((item, index) => [
    index + 1, item.id, item.database, item.question, '', item.execution_match ? '三组结果全部匹配' : item.error || '未通过三组结果等价检查',
  ]), 'SQL逐条预测正确性');
  for (const [index, row] of Array.from(list.querySelectorAll('tbody tr')).entries()) {
    if (predictions[index]) row.children[4]!.append(sqlCorrectnessBadge(predictions[index]!.execution_match));
  }
  section.append(list); host.append(section);
}

export function renderSQLLogic(host: HTMLElement): void {
  const steps = element('ol', 'sql-calculation-steps');
  for (const text of [
    '确定分母 N：先按所选数据库筛选上传用例，再取前“最多评测条数”条。每条用例权重相同；工作流请求失败、未生成 SQL、解析失败、转换或执行失败均计入 N，结果等价得分为 0。',
    '提取 SQL：优先提取第一个代码块内容，否则使用去除首尾空白的完整输出。必须以 SELECT 或 WITH 开头；按目标数据库方言解析，只接受单条只读查询，并拒绝写入等语句。通过后语法分 Sᵢ = 1，否则为 0。',
    '规范化匹配：生成 SQL 与标准 SQL 分别按目标方言解析并规范化，再比较文本；相同记 Eᵢ = 1，否则为 0。它是辅助指标，不参与结果等价得分。',
    '执行与比对：将生成 SQL 转换为 SQLite SQL，使用用例的 fixture_profile 选择模拟场景，在种子 0、1、2 三组数据上执行，与上传 JSON 的 expected_results 对应组比较。上传的预期结果是评分依据，运行评分时不重新执行标准 SQL 生成答案。',
    '比较规则：按行顺序和列位置比较值，保留重复行，忽略列别名；实际数值结果先四舍五入到 5 位小数，再与预期值比较。任意行数、列数、顺序或值不一致，该组为 0；完全一致为 1。',
    '单条得分 Cᵢ：三组结果全部匹配才为 1，任意不匹配或报错均为 0。总体结果等价率 = ΣCᵢ / N × 100%；解析通过率 = ΣSᵢ / N × 100%；规范化严格匹配率 = ΣEᵢ / N × 100%。',
    '分组统计：每种数据库和问题类型分别以组内用例数为分母。总体按全部用例计数，不直接平均各组百分比；分母为 0 时显示“—”。',
  ]) steps.append(element('li', '', text));
  host.append(steps);
  note(host, '该评分使用 SQLGlot 和本地 SQLite 模拟数据，不代表 Oracle、PostgreSQL、SQL Server、MySQL 真实实例的原生执行通过率。解析通过不保证转换或执行成功；转换、模拟函数不兼容也会导致该条结果等价得分为 0。');
}

export function renderSQLCalculation(host: HTMLElement, metrics: SQLMetrics, predictions: SQLPrediction[]): void {
  const section = element('section', 'extraction-calculation sql-calculation');
  const button = element('button', 'button secondary', '查看详细计算过程'); button.type = 'button';
  const content = element('div', 'calculation-content'); content.id = 'sql-calculation-content'; content.hidden = true;
  button.setAttribute('aria-controls', content.id); button.setAttribute('aria-expanded', 'false');
  let rendered = false;
  button.addEventListener('click', () => {
    content.hidden = !content.hidden; button.setAttribute('aria-expanded', String(!content.hidden));
    button.textContent = content.hidden ? '查看详细计算过程' : '收起详细计算过程';
    if (rendered) return; rendered = true;
    const n = metrics.n;
    const exact = metrics.exact_match;
    content.append(element('h3', '', '总体指标计算'));
    content.append(table(['指标', '公式与代入值', '结果'], [
      ['结果等价率', `三组全部匹配的用例数 / N × 100% = ${metrics.execution_correct} / ${n} × 100%`, percent(metrics.execution_correct, n)],
      ['解析通过率', `解析及只读检查通过数 / N × 100% = ${metrics.syntax_pass} / ${n} × 100%`, percent(metrics.syntax_pass, n)],
      ['规范化严格匹配率', exact === undefined ? '历史报告未保存此计数' : `规范化 SQL 相同数 / N × 100% = ${exact} / ${n} × 100%`, exact === undefined ? '—' : percent(exact, n)],
    ], 'SQL总体指标计算公式'));
    note(content, `本次分母 N = ${n}，结果不等价或评分失败 ${n - metrics.execution_correct} 条。三组模拟数据共同决定每条用例的 0/1 得分，分母不是 ${n * 3}。`);
    content.append(table(['数据库', '组内用例数', '结果等价数', '结果等价率计算', '解析通过率计算'], Object.entries(metrics.by_database).map(([db, group]) => [db, group.n, group.correct, `${group.correct} / ${group.n} × 100% = ${percent(group.correct, group.n)}`, `${group.syntax_pass} / ${group.n} × 100% = ${percent(group.syntax_pass, group.n)}`]), 'SQL按数据库计算过程'));
    if (metrics.by_kind) content.append(table(['问题类型', '正确数', '用例数', '结果等价率计算'], Object.entries(metrics.by_kind).map(([kind, group]) => [kind === 'catalog' ? '系统目录查询' : kind === 'personnel' ? '人员统计' : kind, group.correct, group.n, `${group.correct} / ${group.n} × 100% = ${percent(group.correct, group.n)}`]), 'SQL按问题类型计算过程'));
    content.append(element('h3', '', '逐条得分与三组结果对照'));
    for (const item of predictions) {
      const detail = element('details', 'calculation-document sql-case-calculation');
      const summary = element('summary');
      summary.append(sqlCorrectnessBadge(item.execution_match), document.createTextNode(` ${item.id} · ${item.database} · Cᵢ = ${Number(item.execution_match)} · ${item.question}`));
      detail.append(summary);
      let populated = false;
      detail.addEventListener('toggle', () => {
        if (!detail.open || populated) return; populated = true;
        note(detail, `解析分 Sᵢ = ${verdict(item.syntax_valid)}；严格匹配分 Eᵢ = ${verdict(item.exact_match)}；结果等价分 Cᵢ = ${verdict(item.execution_match)}。`);
        detail.append(element('pre', 'sql-calculation-code', `标准 SQL\n${item.standard_sql}\n\n生成 SQL\n${item.generated_sql || '未生成'}`));
        if (item.error) note(detail, `原因：${item.error}`);
        const calc = item.calculation;
        if (!calc || calc.version !== 1) { note(detail, '此历史任务未保存三组计算明细。可使用本次测试集和已有预测结果离线重算，无需再次调用工作流。'); return; }
        note(detail, `${calc.stage === 'complete' ? '完成阶段' : '停止阶段'}：${stages[calc.stage] ?? calc.stage}。`);
        detail.append(element('pre', 'sql-calculation-code', `规范化标准 SQL\n${calc.normalized_standard_sql ?? '未计算'}\n\n规范化生成 SQL\n${calc.normalized_generated_sql ?? '未计算'}\n\n实际执行的 SQLite SQL\n${calc.sqlite_sql ?? '未转换'}`));
        const statuses = { matched: '匹配（1）', mismatched: '不匹配（0）', error: '执行错误（0）', skipped: '未执行（不算匹配）' };
        detail.append(table(['模拟数据组', '预期行数', '实际行数', '判定'], calc.fixtures.map(f => [f.seed, f.expected_rows.length, f.actual_rows === null ? '未取得' : f.actual_rows.length, statuses[f.status]]), `${item.id}三组判定`));
        note(detail, `Cᵢ = I(组 0 匹配 且 组 1 匹配 且 组 2 匹配) = ${Number(item.execution_match)}。`);
        for (const fixture of calc.fixtures) {
          const group = element('details', 'calculation-document');
          group.append(element('summary', '', `模拟数据组 ${fixture.seed}：${statuses[fixture.status]}`));
          if (fixture.error) note(group, `执行错误：${fixture.error}`);
          if (fixture.status === 'skipped') note(group, '因前序阶段或前一组执行失败，本组未执行。');
          if (fixture.first_difference) {
            const diff = fixture.first_difference;
            note(group, `首个差异：第 ${diff.row_number} 行；实际 ${diff.actual_row === null ? '缺少此行' : JSON.stringify(diff.actual_row)}；预期 ${diff.expected_row === null ? '无此行（实际多出）' : JSON.stringify(diff.expected_row)}。`);
          }
          group.append(element('pre', 'sql-calculation-code', `预期结果（完整，按行和列位置）\n${JSON.stringify(fixture.expected_rows, null, 2)}\n\n实际结果（数值保留 5 位小数）\n${fixture.actual_rows === null ? '未取得执行结果' : JSON.stringify(fixture.actual_rows, null, 2)}`));
          detail.append(group);
        }
      });
      content.append(detail);
    }
    note(content, '上述计数与逐条判定来自本次后端报告；计算明细也包含在“完整 JSON”下载文件中。');
  });
  section.append(button, content); host.append(section);
}
