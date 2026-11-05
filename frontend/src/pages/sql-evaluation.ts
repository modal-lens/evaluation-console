import { element, message, table } from '../components/dom.js';
import { renderSQLCalculation, renderSQLLogic, renderSQLPredictions, sqlCorrectnessBadge, type SQLMetrics, type SQLPrediction } from '../components/sql-calculation.js';

export const sqlRoute = '/evaluation/natural-language-sql';
const prefix = '/api/v1/sql-evaluation';
interface Case { id: string; database: string; category: string; input: { database: string; question: string; schema: string }; standard_sql: string; expected_results: Record<string, unknown> }
interface Job { job_id: string; status: string; completed: number; total: number; error: string; created_at: string; result?: { metrics: SQLMetrics; results: SQLPrediction[] } }
export interface SQLState { job?: Job; url?: string; flowId?: string; cases?: Case[]; datasetFilename?: string }

function parseCases(value: unknown): Case[] {
  if (!Array.isArray(value) || !value.length || value.length > 100) throw new Error('测试数据必须是包含 1～100 条用例的 JSON 数组。');
  const ids = new Set<string>();
  const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
  const nonempty = (v: unknown): v is string => typeof v === 'string' && !!v.trim();
  for (const [index, item] of value.entries()) {
    const label = `第 ${index + 1} 条用例`;
    if (!object(item) || !nonempty(item.id) || !nonempty(item.category) || !nonempty(item.standard_sql)
      || !['Oracle', 'PostgreSQL', 'SQL Server', 'MySQL'].includes(String(item.database))
      || !object(item.input) || item.input.database !== item.database || !nonempty(item.input.question) || !nonempty(item.input.schema)) {
      throw new Error(`${label} 缺少有效的 id、database、category、input（database、question、schema）或 standard_sql。`);
    }
    if (ids.has(item.id)) throw new Error(`${label} 的 ID 重复：${item.id}`);
    ids.add(item.id);
    const expected = item.expected_results;
    if (!object(expected) || Object.keys(expected).sort().join(',') !== '0,1,2'
      || !Object.values(expected).every(rows => Array.isArray(rows) && rows.every(row => Array.isArray(row)
        && row.every(cell => cell === null || ['string', 'number', 'boolean'].includes(typeof cell))))) {
      throw new Error(`${label} 的 expected_results 必须包含 0、1、2 三组二维结果数组。`);
    }
  }
  return value as Case[];
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(prefix + path, { ...init, signal: AbortSignal.timeout(15_000) });
  const value = await response.json() as { success?: boolean; data?: T; detail?: unknown };
  if (!response.ok || !value.success) {
    const detail = typeof value.detail === 'string' ? value.detail : response.status === 422 ? '配置或测试数据格式不正确，请检查地址、流程 ID、数量及用例字段。' : `请求失败（HTTP ${response.status}）`;
    throw new Error(detail);
  }
  return value.data!;
}

export function mountSQL(container: HTMLElement, state: SQLState): () => void {
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let busy = false;
  let importing = false;
  let importVersion = 0;
  let configReady = false;
  const active = () => ['queued', 'running'].includes(state.job?.status ?? '');
  container.innerHTML = `
    <section class="page-heading"><div class="eyebrow">WORKFLOW EVALUATION</div><h1>自然语言转 SQL 评测</h1><p>通过本机 Python 脚本调用工作流，比较生成 SQL 与标准答案的查询结果。</p></section>
    <div class="rule-banner"><span class="rule-icon">i</span><p>上传包含测试问题与标准答案的 JSON 后开始评测。工作流只生成 SQL，评分与报告在本地完成，标准答案不会发送给工作流。</p></div>
    <div class="workspace-grid sql-evaluation">
      <section class="panel"><div class="panel-heading"><h2>工作流与测试配置</h2></div>
        <form class="sql-form">
          <label>工作流服务地址<input name="url" type="url" required></label>
          <label>流程 ID<input name="flow" required></label>
          <label>工作流 API Key<input name="key" type="password" autocomplete="off" placeholder="留空使用后端默认 Key 或环境变量"></label>
          <div class="sql-options"><label>数据库<select name="database" aria-label="数据库"><option value="">全部四种</option><option>Oracle</option><option>PostgreSQL</option><option>SQL Server</option><option>MySQL</option></select></label>
          <label>最多评测条数<input name="limit" type="number" value="100" min="1" max="100" required></label>
          <label>并发数<input name="workers" type="number" min="1" max="8" required placeholder="读取 config.yml…"></label></div>
          <p class="panel-description">API Key 仅用于本次任务，不保存到配置文件或浏览器存储。每条请求只发送数据库、表结构和问题。</p>
          <div class="submit-row"><button class="button primary-button" type="submit">开始本地脚本评测</button><button class="button secondary" type="button" data-action="cancel" hidden>取消任务</button></div>
        </form><p class="error-feedback" role="alert" hidden></p>
        <details class="raw-result"><summary>在命令行运行同一评测</summary><pre>python -m pip install -r requirements-sql-evaluation.txt
# 先设置 LANGFLOW_API_KEY 环境变量
python scripts/evaluate_sql_workflow.py</pre></details>
        <div class="sql-history"><h3>最近任务</h3><div class="history-items">正在加载…</div></div>
      </section>
      <section class="panel result-panel" aria-label="SQL评测结果"><div class="panel-heading"><h2>本地评测结果</h2><button type="button" class="button secondary" data-action="refresh">刷新进度</button></div>
        <p class="result-status" role="status" aria-live="polite">尚未发起评测</p><progress max="100" value="0" aria-label="SQL评测进度"></progress><div class="sql-results"></div>
        <p class="result-note">结果等价率基于三组模拟数据；系统目录也在本地模拟。它不等于四种真实数据库的原生执行通过率。</p>
      </section>
    </div>
    <details class="panel field-reference sql-logic"><summary>计算逻辑与评测口径 <span>展开查看</span></summary><div class="sql-logic-content"></div></details>
    <section class="panel sql-dataset"><div class="panel-heading"><h2>测试数据与标准答案</h2><div><button type="button" class="button secondary" data-action="import-cases">上传测试数据 JSON</button><button type="button" class="button text-button" data-action="clear-cases">清空数据</button><a class="button secondary" href="${prefix}/dataset/json" download="test_cases_100.json">下载 JSON 格式示例</a></div></div><input type="file" accept=".json,application/json" hidden aria-label="上传测试数据 JSON"><p class="panel-description dataset-status" aria-live="polite"></p><p class="panel-description">JSON 数组，1～100 条，最大 5 MB。每条包含 id、database、category、input（database、question、schema）、standard_sql 和 expected_results（0、1、2 三组二维结果数组）。评分使用现有本地模拟数据库；示例中的 fixture_profile 等字段请一并保留。</p><div class="dataset-rows"></div></section>`;
  const form = container.querySelector<HTMLFormElement>('form')!;
  renderSQLLogic(container.querySelector<HTMLElement>('.sql-logic-content')!);
  const input = (name: string) => form.elements.namedItem(name) as HTMLInputElement;
  input('workers').disabled = true;
  input('url').value = state.url ?? 'http://192.168.31.60:7865';
  input('flow').value = state.flowId ?? '5d87aa5b-aeaf-4860-a7b6-9ccf93e2e04a';
  const feedback = container.querySelector<HTMLElement>('[role="alert"]')!;
  const status = container.querySelector<HTMLElement>('[role="status"]')!;
  const results = container.querySelector<HTMLElement>('.sql-results')!;
  const submit = form.querySelector<HTMLButtonElement>('[type="submit"]')!;
  const cancel = form.querySelector<HTMLButtonElement>('[data-action="cancel"]')!;
  const showError = (error: unknown) => { feedback.hidden = false; feedback.textContent = message(error); };
  const clearError = () => { feedback.hidden = true; feedback.textContent = ''; };

  const draw = () => {
    const job = state.job;
    submit.disabled = busy || active() || importing || !configReady || !state.cases?.length; cancel.hidden = !active();
    for (const button of container.querySelectorAll<HTMLButtonElement>('[data-action="import-cases"], [data-action="clear-cases"]')) button.disabled = busy || active();
    results.replaceChildren();
    if (!job) return;
    const labels: Record<string, string> = { queued: '等待本地 Python 启动', running: '本地脚本正在请求工作流', succeeded: '评测完成', cancelled: '评测已取消', failed: '评测失败' };
    status.textContent = `${labels[job.status] ?? job.status} · ${job.completed}/${job.total}`;
    const progress = container.querySelector('progress')!;
    progress.max = job.total || 100; progress.value = job.completed;
    if (job.error) results.append(element('p', 'error-feedback', job.error));
    if (!job.result) return;
    const m = job.result.metrics;
    const cards = element('div', 'metrics-grid');
    for (const [label, value] of [['结果等价率', `${m.accuracy_percent.toFixed(1)}%`], ['正确数', `${m.execution_correct}/${m.n}`], ['解析通过', `${m.syntax_pass}/${m.n}`]]) {
      const card = element('div', 'metric-card'); card.append(element('span', 'metric-label', label), element('strong', 'metric-value', value)); cards.append(card);
    }
    results.append(cards, table(['数据库', '正确数', '等价率'], Object.entries(m.by_database).map(([db, v]) => [db, `${v.correct}/${v.n}`, `${(v.correct / v.n * 100).toFixed(1)}%`]), '按数据库评分'));
    if (m.flow_unchanged !== undefined) results.append(element('p', 'result-note', m.flow_unchanged ? '已核对：工作流节点、连线与配置未变化。' : '评测期间检测到工作流版本变化。'));
    const links = element('div', 'submit-row');
    for (const [file, label] of [['results.csv', '逐条评分 CSV'], ['results.json', '完整 JSON'], ['评测报告.txt', '评测报告']]) {
      const a = element('a', 'button secondary', label); a.href = `${prefix}/jobs/${job.job_id}/files/${encodeURIComponent(file!)}`; a.download = file!; links.append(a);
    }
    results.append(links);
    renderSQLCalculation(results, m, job.result.results);
    renderSQLPredictions(results, job.result.results);
    const details = element('details', 'raw-result'); details.append(element('summary', '', '查看逐条 SQL 与标准答案'));
    for (const r of job.result.results) {
      const row = element('details', 'sql-case');
      const summary = element('summary');
      summary.append(sqlCorrectnessBadge(r.execution_match), document.createTextNode(` ${r.id} · ${r.database} · ${r.question}`));
      row.append(summary, element('pre', '', `判定：${r.execution_match ? '正确（三组结果全部匹配）' : '不正确'}\n\n标准 SQL\n${r.standard_sql}\n\n生成 SQL\n${r.generated_sql || '未生成'}${r.error ? '\n\n原因：' + r.error : ''}`)); details.append(row);
    }
    results.append(details);
  };

  const history = async () => {
    try {
      const jobs = await api<Job[]>('/jobs'); if (disposed) return;
      const host = container.querySelector<HTMLElement>('.history-items')!; host.replaceChildren();
      if (!jobs.length) host.textContent = '尚无 SQL 评测记录。';
      for (const job of jobs.slice(0, 5)) {
        const button = element('button', 'button secondary', `${job.created_at} · ${job.status} · ${job.completed}/${job.total}`);
        button.type = 'button'; button.addEventListener('click', () => { state.job = job; void poll(); }); host.append(button);
      }
    } catch (error) { if (!disposed) showError(error); }
  };

  const poll = async () => {
    if (timer) clearTimeout(timer);
    if (!state.job || disposed) return;
    const id = state.job.job_id;
    try { const job = await api<Job>(`/jobs/${id}`); if (disposed || state.job?.job_id !== id) return; state.job = job; draw(); if (active()) timer = setTimeout(() => void poll(), 1200); else void history(); }
    catch (error) { if (!disposed) { showError(error); if (active()) timer = setTimeout(() => void poll(), 5000); } }
  };
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy || active() || importing || !configReady) return;
    if (!state.cases?.length) { showError(new Error('请先上传测试数据 JSON。')); return; }
    if (input('database').value && !state.cases.some(item => item.database === input('database').value)) { showError(new Error('上传数据中没有所选数据库的测试用例。')); return; }
    clearError(); busy = true; draw();
    const payload = { url: input('url').value.trim(), flow_id: input('flow').value.trim(), api_key: input('key').value, database: input('database').value, limit: Number(input('limit').value), workers: Number(input('workers').value), cases: state.cases };
    state.url = payload.url; state.flowId = payload.flow_id;
    try { const job = await api<Job>('/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); state.job = job; input('key').value = ''; if (!disposed) { draw(); void poll(); } }
    catch (error) { if (!disposed) showError(error); }
    finally { busy = false; if (!disposed) draw(); }
  });
  cancel.addEventListener('click', async () => {
    if (!state.job) return;
    try { state.job = await api<Job>(`/jobs/${state.job.job_id}/cancel`, { method: 'POST' }); if (!disposed) { draw(); void history(); } }
    catch (error) { if (!disposed) showError(error); }
  });
  container.querySelector('[data-action="refresh"]')!.addEventListener('click', () => { clearError(); void poll(); });
  const fileInput = container.querySelector<HTMLInputElement>('.sql-dataset input[type=file]')!;
  const importButton = container.querySelector<HTMLButtonElement>('[data-action="import-cases"]')!;
  const clearButton = container.querySelector<HTMLButtonElement>('[data-action="clear-cases"]')!;
  const drawDataset = () => {
    container.querySelector('.dataset-status')!.textContent = importing ? '正在读取文件…' : state.cases?.length ? `已上传 ${state.datasetFilename} · ${state.cases.length} 条测试数据` : '尚未上传测试数据，请选择 JSON 文件。';
    importButton.disabled = busy || active(); clearButton.disabled = busy || active();
    const host = container.querySelector('.dataset-rows')!;
    host.replaceChildren();
    for (const item of state.cases ?? []) {
      const details = element('details', 'sql-case'); details.append(element('summary', '', `${item.database} · ${item.input.question}`), element('pre', '', `表结构与范围\n${item.input.schema}\n\n标准 SQL\n${item.standard_sql}\n\n三组预期结果\n${JSON.stringify(item.expected_results, null, 2)}`)); host.append(details);
    }
  };
  importButton.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async () => {
    const file = fileInput.files?.[0]; fileInput.value = '';
    if (!file || busy || active()) return;
    const version = ++importVersion;
    importing = true; clearError(); draw(); drawDataset();
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error('JSON 文件不能超过 5 MB。');
      const text = (await file.text()).replace(/^\uFEFF/, '');
      if (disposed || version !== importVersion) return;
      let value: unknown;
      try { value = JSON.parse(text); } catch { throw new Error('JSON 格式不正确，请检查文件。'); }
      const cases = parseCases(value);
      state.cases = cases; state.datasetFilename = file.name;
    } catch (error) { if (!disposed && version === importVersion) showError(error); }
    finally { if (!disposed && version === importVersion) { importing = false; draw(); drawDataset(); } }
  });
  clearButton.addEventListener('click', () => {
    if (busy || active()) return;
    importVersion++; importing = false; state.cases = undefined; state.datasetFilename = undefined;
    clearError(); draw(); drawDataset();
  });
  void api<{ max_workers: number }>('/config').then(config => {
    if (disposed) return;
    if (!Number.isInteger(config.max_workers) || config.max_workers < 1 || config.max_workers > 8) throw new Error('后端并发配置无效，请检查 config.yml 的 max_workers。');
    input('workers').value = String(config.max_workers); input('workers').disabled = false; configReady = true; draw();
  }).catch(error => { if (!disposed) showError(error); });
  draw(); drawDataset(); void history(); if (state.job) void poll();
  return () => { disposed = true; importVersion++; if (timer) clearTimeout(timer); input('key').value = ''; };
}
