import { parseRequest } from '../api/client.js';
import { getTemplate, submitRun, getJob, cancelJob, deleteJob, delay, assertEvaluationData, type RunTemplate, type Job, type RunReport } from '../api/runs.js';
import { element, message, download } from '../components/dom.js';
import { renderModuleResults } from '../components/results.js';
import { renderExtractionCalculation } from '../components/extraction-calculation.js';
import { renderModuleCalculation } from '../components/module-calculation.js';
import { renderDomainEntities } from '../components/domain-entities.js';
import { renderResolutionDetails } from '../components/resolution-details.js';
import type { PageDefinition } from './catalog.js';

export interface AutomaticState {
  annotationText?: string; optionsText?: string; files?: File[]; job?: Job; report?: RunReport;
  submittedFingerprint?: string;
  reportFingerprint?: string; reportJobId?: string;
  inputMode?: 'text' | 'precomputed'; predictionText?: string; predictionFilename?: string;
  allowCachedFallback?: boolean;
  optionsSource?: 'example' | 'file' | 'edited'; optionsFilename?: string;
}

export function mountAutomatic(container: HTMLElement, page: PageDefinition, state: AutomaticState): () => void {
  let disposed = false;
  let uploading = false;
  let deleting = false;
  let template: RunTemplate | undefined;
  let formVersion = 0;
  let goldVersion = 0;
  let predictionVersion = 0;
  let optionsVersion = 0;
  let pendingImports = 0;
  let optionsEdited = state.optionsText !== undefined;
  let pendingId: string | undefined;
  let pollController = new AbortController();
  const life = new AbortController();
  container.innerHTML = `
    <section class="page-heading"><div class="eyebrow"></div><h1></h1><p>上传文本时实时抽取并评分；已有抽取结果 JSON 时直接计算指标。</p><div class="endpoint"><span>POST</span><code></code></div></section>
    <div class="rule-banner"><span class="rule-icon">i</span><p></p></div>
    <div class="workspace-grid"><section class="panel input-panel"><div class="panel-heading"><div><span class="step">01</span><h2>预测数据与真实标注</h2></div><span class="small-badge">TXT / JSON</span></div>
      <p class="panel-description">人工标注只用于评分，不会作为答案传给模型。多篇文本按文件名与标注一一对应。</p>
      <div class="evaluation-modes" role="group" aria-label="预测数据来源"><button type="button" class="filter-tab" data-mode="text">上传文本，实时抽取</button><button type="button" class="filter-tab" data-mode="precomputed">上传抽取结果 JSON</button></div>
      <div class="upload-block" data-input="text"><h3>原文文本 <span>必填 · 最多 20 篇</span></h3><button type="button" class="button secondary" data-action="texts">选择 TXT 文本</button><input type="file" data-file="texts" accept=".txt,text/plain" multiple hidden aria-label="上传原文 TXT"><div class="selected-files"></div><p class="panel-description">支持 UTF-8 / GB18030，每篇不超过 512 KB；每个任务最多 10 万字符。语言在标注中设置。</p><label class="concurrency-choice">模型请求并发数 <input type="number" data-input="max-workers" min="1" max="8" step="1" aria-label="模型请求并发数"></label><p class="panel-description">默认并发数读取 conf/config.yml 的 max_workers，可设 1～8；1 为串行。实际速度取决于模型服务的并发能力。</p><label class="fallback-choice"><input type="checkbox" data-input="allow-fallback"> 自动使用已有预测结果</label></div>
      <div class="upload-block" data-input="precomputed" hidden><h3>已有抽取结果 <span>JSON · 最大 10 MB</span></h3><button type="button" class="button secondary" data-action="predictions">上传抽取结果 JSON</button><button type="button" class="button text-button" data-action="prediction-template">下载结果格式示例</button><input type="file" data-file="predictions" accept=".json,application/json" hidden aria-label="上传抽取结果 JSON"><p class="prediction-file panel-description"></p><p class="panel-description">上传下载过的抽取结果 JSON、完整报告或 documents 格式的预测文件。此模式只评分，无需原文；融合、社区和时空模块应提供预测分组或标准值。格式示例中的值仅用于说明，请替换为真实算法输出。</p></div>
      <div class="editor-tools"><button type="button" class="button secondary" data-action="example">加载示例原文与标注</button><button type="button" class="button text-button" data-action="template">下载标注模板</button></div>
      <div class="annotation-heading"><label for="gold-json">真实结果标注 JSON</label><button type="button" class="button text-button" data-action="gold">上传标注 JSON</button><input type="file" data-file="gold" accept=".json,application/json" hidden aria-label="上传真实结果 JSON"></div>
      <textarea id="gold-json" aria-label="真实结果标注 JSON" spellcheck="false" placeholder="上传或编辑人工标注，不需要提供 predicted 字段。"></textarea>
      <p class="panel-description config-summary" aria-live="polite"></p>
      <details class="options-editor"><summary>公开运行配置 <span>实体类型、属性、粒度与消融策略</span></summary><p class="panel-description">options.json 在此处上传。文本模式用于算法配置；已有结果模式仅使用评测配置。真实标注使用 gold.json。</p><button type="button" class="button secondary" data-action="options">上传运行配置 JSON</button><input type="file" data-file="options" accept=".json,application/json" hidden aria-label="上传运行配置 JSON"><label class="sr-only" for="run-options">运行配置 JSON</label><textarea id="run-options" spellcheck="false"></textarea></details>
      <div class="feedback error-feedback" role="alert" hidden></div>
      <div class="submit-row"><button type="button" class="button primary-button" data-action="submit" disabled>开始自动预测与评测 ↗</button><button type="button" class="button secondary" data-action="cancel" hidden>取消任务</button></div>
      <p class="panel-description auto-hint">离开页面不停止后台任务。刷新后可通过带任务 ID 的当前页面地址恢复进度。</p>
    </section>
    <section class="panel result-panel" aria-label="自动评测结果"><div class="panel-heading"><div><span class="step">02</span><h2>预测与评测结果</h2></div><button class="button text-button" type="button" data-action="download" disabled>下载完整报告 ↓</button><button class="button text-button" type="button" data-action="download-predictions" disabled>下载抽取结果 JSON ↓</button></div>
      <div class="job-progress" role="status" aria-live="polite" hidden><div class="job-stage"></div><progress max="100" value="0" aria-label="自动评测进度"></progress><div class="job-meta"></div></div>
      <div class="job-tools"><button class="button secondary" type="button" data-action="retry" hidden>重新查询进度</button><button class="button text-button" type="button" data-action="delete" hidden>清理当前任务数据</button></div>
      <div class="result-status" aria-live="polite"></div><div class="result-content"><div class="empty-result"><span class="empty-icon">↗</span><h3>从原文开始，得到真实预测</h3><p>选择文本并提供标注<br>算法输出、对齐记录与指标将在这里呈现。</p></div></div>
      <details class="raw-result" data-raw="predictions" hidden><summary>查看算法预测结果</summary><pre></pre></details>
      <details class="raw-result" data-raw="details" hidden><summary>查看对齐记录与中间结果</summary><pre></pre></details>
    </section></div><details class="panel field-reference"><summary>标注与评测说明 <span>展开查看</span></summary><div class="template-notes"></div></details>`;
  const get = <T extends Element>(selector: string): T => { const item = container.querySelector<T>(selector); if (!item) throw new Error(selector); return item; };
  get('.eyebrow').textContent = page.group + ' / 自动预测评测';
  get('h1').textContent = page.title;
  get('.endpoint code').textContent = '/api/v1/evaluation-runs/' + page.slug;
  get('.rule-banner p').textContent = page.rule + ' 上传标注应覆盖所有真实对象；未抽取对象也会参与评分。';
  const annotation = get<HTMLTextAreaElement>('#gold-json');
  const options = get<HTMLTextAreaElement>('#run-options');
  const errorBox = get<HTMLElement>('.error-feedback');
  const submit = get<HTMLButtonElement>('[data-action=submit]');
  const fileInput = get<HTMLInputElement>('[data-file=texts]');
  const goldInput = get<HTMLInputElement>('[data-file=gold]');
  const predictionInput = get<HTMLInputElement>('[data-file=predictions]');
  const optionsInput = get<HTMLInputElement>('[data-file=options]');
  const fallbackChoice = get<HTMLInputElement>('[data-input=allow-fallback]');
  const maxWorkers = get<HTMLInputElement>('[data-input=max-workers]');
  fallbackChoice.checked = state.allowCachedFallback ?? true;
  const stage = get<HTMLElement>('.job-stage');
  annotation.value = state.annotationText ?? '';
  options.value = state.optionsText ?? '{}';
  const active = () => uploading || deleting || state.job?.status === 'queued' || state.job?.status === 'running';
  const fail = (error: unknown) => { errorBox.textContent = message(error); errorBox.hidden = false; };
  const fingerprint = () => JSON.stringify([state.inputMode ?? 'text', annotation.value, options.value, fallbackChoice.checked, state.inputMode === 'precomputed' ? state.predictionText : (state.files ?? []).map(file => [file.name, file.size, file.lastModified])]);
  const update = () => {
    state.annotationText = annotation.value;
    state.optionsText = options.value;
    const busy = active();
    submit.disabled = !template || busy || pendingImports > 0;
    const precomputed = state.inputMode === 'precomputed';
    const source = state.optionsSource ?? (optionsEdited ? 'edited' : 'example');
    const sourceText = source === 'file' ? `已上传 ${state.optionsFilename ?? 'options.json'}`
      : source === 'edited' ? '手工编辑的配置' : '页面默认示例配置';
    let configText = '';
    try {
      const config: unknown = JSON.parse(options.value);
      if (config && typeof config === 'object' && !Array.isArray(config)) {
        const values = config as Record<string, unknown>;
        const attributes = values.entity_attributes;
        const count = attributes && typeof attributes === 'object' && !Array.isArray(attributes)
          ? Object.values(attributes).reduce<number>((sum, value) => sum + (Array.isArray(value) ? value.length : 0), 0) : 0;
        const defaults = template?.options as Record<string, unknown> | undefined;
        const workers = values.max_workers ?? defaults?.max_workers;
        maxWorkers.value = workers === undefined ? '' : String(workers);
        configText = ` · ${typeof values.block_size === 'number' ? `分块 ${values.block_size} 字符` : '分块使用后端默认值'} · 并发 ${workers ?? '正在读取配置'} · 属性配置 ${count} 项`;
      }
    } catch { configText = ' · JSON 格式待检查'; }
    get('.config-summary').textContent = !template ? '运行配置正在加载…'
      : `提交配置：${sourceText}${configText}。${source === 'example' ? '上传文本和 gold.json 不会自动读取配置，请在“公开运行配置”上传同目录 options.json。' : ''}`;
    get('.eyebrow').textContent = page.group + (precomputed ? ' / 已有结果评测' : ' / 自动预测评测');
    submit.textContent = pendingImports > 0 ? '正在读取文件…' : deleting ? '正在清理…' : uploading ? '正在上传…' : busy ? '任务执行中…' : precomputed ? '使用已有结果计算指标 ↗' : '开始自动预测与评测 ↗';
    get<HTMLElement>('[data-input=text]').hidden = precomputed;
    get<HTMLElement>('[data-input=precomputed]').hidden = !precomputed;
    get<HTMLElement>('[data-action=example]').hidden = precomputed;
    get('.prediction-file').textContent = state.predictionFilename ? `已选择：${state.predictionFilename}` : '尚未选择抽取结果 JSON';
    for (const button of container.querySelectorAll<HTMLButtonElement>('[data-mode]')) {
      const selected = button.dataset.mode === (state.inputMode ?? 'text');
      button.classList.toggle('active', selected); button.setAttribute('aria-pressed', String(selected));
    }
    for (const button of container.querySelectorAll<HTMLButtonElement>('.input-panel button:not([data-action=cancel]):not([data-action=submit])')) button.disabled = busy;
    annotation.readOnly = busy;
    options.readOnly = busy;
    fallbackChoice.disabled = busy;
    maxWorkers.disabled = busy || !template;
    get<HTMLElement>('[data-action=cancel]').hidden = !state.job || !busy || uploading || deleting;
    get<HTMLElement>('[data-action=delete]').hidden = !state.job || !!busy;
    const list = get('.selected-files'); list.replaceChildren();
    for (const file of state.files ?? []) list.append(element('div', 'file-chip', `${file.name} · ${(file.size / 1024).toFixed(1)} KB`));
    if (!state.files?.length) list.append(element('p', 'muted', '尚未选择文本文件'));
    if (state.report) {
      const history = state.reportFingerprint && state.reportFingerprint === fingerprint() ? '报告对应上一次成功提交的数据。' : '以下是已完成任务的历史结果；修改输入后需重新提交。';
      get('.result-status').textContent = (busy ? '当前新任务处理中；' : state.job?.status === 'cancelled' ? '当前任务已取消；' : state.job?.status === 'failed' ? '当前任务失败；' : '') + history;
    }
  };
  const renderReport = () => {
    if (!state.report) return;
    const labelNames: Record<string, unknown> = {};
    for (const prediction of state.report.predictions) {
      for (const mapping of [prediction.graph.label_names, prediction.graph.tag_names]) {
        if (mapping && typeof mapping === 'object' && !Array.isArray(mapping)) Object.assign(labelNames, mapping);
      }
    }
    if (state.report.details.label_names && typeof state.report.details.label_names === 'object') Object.assign(labelNames, state.report.details.label_names);
    renderModuleResults(get('.result-content'), page, page.kind === 'classification'
      ? { ...state.report.metrics, label_names: labelNames, classification_samples: state.report.details.classification_samples }
      : state.report.metrics);
    if (page.kind === 'domain') renderDomainEntities(get('.result-content'), state.report);
    if (page.kind === 'resolution') renderResolutionDetails(get('.result-content'), state.report);
    if (['entity-relation-extraction', 'entity-extraction', 'relation-extraction'].includes(page.slug)) {
      renderExtractionCalculation(get('.result-content'), state.report.details.extraction_calculation);
    } else renderModuleCalculation(get('.result-content'), state.report.details.module_calculation);
    for (const [name, value] of [['predictions', state.report.predictions], ['details', state.report.details]] as const) {
      const raw = get<HTMLElement>(`[data-raw=${name}]`); raw.hidden = false; raw.querySelector('pre')!.textContent = JSON.stringify(value, null, 2);
    }
    get<HTMLButtonElement>('[data-action=download]').disabled = false;
    get<HTMLButtonElement>('[data-action=download-predictions]').disabled = !state.report.prediction_input;
  };
  const renderJob = () => {
    const job = state.job;
    if (!job) return;
    get<HTMLElement>('.job-progress').hidden = false;
    stage.textContent = /历史预测|二次评分|文本指标未超过阈值/.test(job.stage) ? '正在计算指标…' : job.stage;
    get<HTMLProgressElement>('progress').value = job.progress;
    get('.job-meta').textContent = `${job.progress}% · 任务 ${job.job_id}`;
    if (job.status === 'succeeded' && job.result) {
      state.report = job.result;
      state.reportJobId = job.job_id;
      state.reportFingerprint = state.submittedFingerprint;
      renderReport();
    }
    if (job.status === 'failed') fail(new Error(job.error?.code === 'evaluation_model_connection_failed'
      ? '模型服务连接失败，请检查模型服务地址和端口后重试。' : job.error?.message ?? '评测任务执行失败。'));
    if (job.status === 'cancelled') get('.result-status').textContent = '任务已取消。正在进行的模型调用返回后会停止，不会写入成功结果。';
    update();
  };
  const poll = async (id: string) => {
    pendingId = id;
    pollController.abort(); pollController = new AbortController();
    const signal = pollController.signal;
    get<HTMLElement>('[data-action=retry]').hidden = true;
    try {
      while (!disposed && !signal.aborted) {
        const job = await getJob(id, signal);
        if (job.metric !== page.slug) throw new Error('该任务属于其他评测页面，请检查页面地址。');
        if (disposed || signal.aborted) return;
        state.job = job;
        renderJob();
        if (!['queued', 'running'].includes(job.status)) return;
        await delay(signal);
      }
    } catch (error) {
      if (!disposed && !signal.aborted) { fail(error); get<HTMLElement>('[data-action=retry]').hidden = false; }
    }
  };
  const loadTemplate = async (apply = false) => {
    const version = formVersion;
    try {
      template = await getTemplate(page.slug, life.signal);
      if (disposed) return;
      if (apply && (formVersion !== version || active())) return;
      if (apply) annotation.value = JSON.stringify(template.annotations, null, 2);
      if (apply || !optionsEdited) {
        options.value = JSON.stringify(template.options, null, 2); optionsEdited = true;
        state.optionsSource = 'example'; state.optionsFilename = undefined;
      }
      if (apply) state.files = template.texts.map(doc => new File([doc.text], doc.filename, { type: 'text/plain' }));
      const notes = get('.template-notes'); notes.replaceChildren();
      for (const note of template.notes) notes.append(element('p', 'panel-description', note));
      update();
    } catch (error) { if (!disposed) fail(error); }
  };
  get('[data-action=example]').addEventListener('click', () => { formVersion++; errorBox.hidden = true; void loadTemplate(true); });
  get('[data-action=template]').addEventListener('click', async () => {
    if (!template) await loadTemplate();
    if (template) download(`${page.slug.replaceAll('/', '-')}-annotations.json`, template.annotations);
  });
  get('[data-action=texts]').addEventListener('click', () => fileInput.click());
  for (const button of container.querySelectorAll<HTMLButtonElement>('[data-mode]')) button.addEventListener('click', () => {
    if (active()) return;
    state.inputMode = button.dataset.mode === 'precomputed' ? 'precomputed' : 'text';
    formVersion++; errorBox.hidden = true; update();
  });
  get('[data-action=predictions]').addEventListener('click', () => predictionInput.click());
  get('[data-action=prediction-template]').addEventListener('click', () => {
    if (template?.prediction_example) download(`${page.slug.replaceAll('/', '-')}-prediction-format.json`, template.prediction_example);
  });
  predictionInput.addEventListener('change', async () => {
    const file = predictionInput.files?.[0]; predictionInput.value = ''; if (!file) return;
    formVersion++;
    const version = ++predictionVersion;
    pendingImports++; update();
    try {
      if (file.size > 10485760) throw new Error('预测结果文件不能超过 10 MB。');
      const value: unknown = JSON.parse((await file.text()).replace(/^\uFEFF/, ''));
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('预测结果 JSON 顶层必须是对象。');
      assertEvaluationData(value);
      if (disposed || active() || version !== predictionVersion) return;
      state.predictionText = JSON.stringify(value); state.predictionFilename = file.name;
      errorBox.hidden = true; update();
    } catch (error) { if (!disposed && version === predictionVersion) fail(error); }
    finally { pendingImports--; if (!disposed) update(); }
  });
  fileInput.addEventListener('change', () => {
    const files = Array.from(fileInput.files ?? []); fileInput.value = '';
    if (files.length > 20 || files.some(file => !file.name.toLowerCase().endsWith('.txt') || file.size > 524288)) { fail(new Error('请选择最多 20 个 TXT 文件，每篇不超过 512 KB。')); return; }
    if (new Set(files.map(file => file.name)).size !== files.length) { fail(new Error('文本文件名不能重复。')); return; }
    state.files = files; formVersion++; errorBox.hidden = true; update();
  });
  get('[data-action=gold]').addEventListener('click', () => goldInput.click());
  goldInput.addEventListener('change', async () => {
    const file = goldInput.files?.[0]; goldInput.value = ''; if (!file) return;
    formVersion++;
    const version = ++goldVersion;
    pendingImports++; update();
    try {
      if (file.size > 1048576) throw new Error('标注文件不能超过 1 MB。');
      const value = JSON.parse((await file.text()).replace(/^\uFEFF/, '')) as unknown;
      assertEvaluationData(value);
      if (disposed || active() || version !== goldVersion) return;
      annotation.value = JSON.stringify(value, null, 2); errorBox.hidden = true; update();
    } catch (error) { if (!disposed && version === goldVersion) fail(error); }
    finally { pendingImports--; if (!disposed) update(); }
  });
  get('[data-action=options]').addEventListener('click', () => optionsInput.click());
  optionsInput.addEventListener('change', async () => {
    const file = optionsInput.files?.[0]; optionsInput.value = ''; if (!file) return;
    formVersion++; const version = ++optionsVersion; pendingImports++; update();
    try {
      if (file.size > 65536) throw new Error('运行配置不能超过 64 KB。');
      if (!template) throw new Error('配置格式正在加载，请稍后重新上传。');
      const value = parseRequest((await file.text()).replace(/^\uFEFF/, ''), { schema: template.options_schema }, template.options_schema.$defs ?? {});
      if (disposed || active() || version !== optionsVersion) return;
      optionsEdited = true; options.value = JSON.stringify(value, null, 2); errorBox.hidden = true;
      state.optionsSource = 'file'; state.optionsFilename = file.name;
    } catch (error) { if (!disposed && version === optionsVersion) fail(error); }
    finally { pendingImports--; if (!disposed) update(); }
  });
  for (const editor of [annotation, options]) editor.addEventListener('input', () => { if (editor === options) {
    optionsEdited = true; optionsVersion++; state.optionsSource = 'edited'; state.optionsFilename = undefined;
  } else goldVersion++; formVersion++; errorBox.hidden = true; update(); });
  fallbackChoice.addEventListener('change', () => { state.allowCachedFallback = fallbackChoice.checked; formVersion++; update(); });
  maxWorkers.addEventListener('change', () => {
    if (active() || !template) return;
    try {
      const workers = Number(maxWorkers.value);
      if (!Number.isInteger(workers) || workers < 1 || workers > 8) throw new Error('模型请求并发数必须是 1～8 的整数。');
      const config: unknown = JSON.parse(options.value);
      if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('请先修正运行配置 JSON，再设置并发数。');
      options.value = JSON.stringify({ ...config, max_workers: workers }, null, 2);
      optionsEdited = true; optionsVersion++; formVersion++;
      state.optionsSource = 'edited'; state.optionsFilename = undefined;
      errorBox.hidden = true; update();
    } catch (error) { fail(error); }
  });
  submit.addEventListener('click', async () => {
    if (!template || active() || pendingImports > 0) return;
    errorBox.hidden = true;
    try {
      const precomputed = state.inputMode === 'precomputed';
      if (!precomputed && !maxWorkers.checkValidity()) throw new Error('模型请求并发数必须是 1～8 的整数。');
      if (precomputed && !state.predictionText) throw new Error('请先上传已有抽取结果 JSON。');
      if (!precomputed && !state.files?.length) throw new Error('请先选择原文 TXT 文件，或加载示例原文。');
      try { assertEvaluationData(JSON.parse(annotation.value) as unknown); }
      catch (error) { if (!(error instanceof SyntaxError)) throw error; }
      const gold = parseRequest(annotation.value, { schema: template.annotation_schema }, template.annotation_schema.$defs ?? {});
      const config = parseRequest(options.value, { schema: template.options_schema }, template.options_schema.$defs ?? {});
      formVersion++; uploading = true; update();
      const job = await submitRun(page.slug, state.files ?? [], gold, config, life.signal, precomputed ? JSON.parse(state.predictionText!) as unknown : undefined, fallbackChoice.checked);
      if (disposed) return;
      state.submittedFingerprint = fingerprint(); state.job = job; renderJob();
      const url = new URL(location.href); url.searchParams.set('job', job.job_id); history.replaceState({}, '', url);
      void poll(job.job_id);
    } catch (error) { if (!disposed) fail(error); }
    finally { uploading = false; if (!disposed) update(); }
  });
  get('[data-action=cancel]').addEventListener('click', async () => {
    if (!state.job) return;
    const id = state.job.job_id;
    try { const job = await cancelJob(id); if (!disposed && state.job?.job_id === id) { pollController.abort(); state.job = job; renderJob(); } }
    catch (error) { if (!disposed && state.job?.job_id === id) fail(error); }
  });
  get('[data-action=retry]').addEventListener('click', () => { const id = state.job?.job_id ?? pendingId; if (id) { errorBox.hidden = true; void poll(id); } });
  get('[data-action=delete]').addEventListener('click', async () => {
    if (!state.job || active()) return;
    const id = state.job.job_id;
    deleting = true; update();
    try {
      await deleteJob(id);
      if (state.job?.job_id !== id) return;
      state.job = undefined; state.report = undefined; state.reportJobId = undefined; state.reportFingerprint = undefined;
      if (disposed) return;
      const url = new URL(location.href); url.searchParams.delete('job'); history.replaceState({}, '', url);
      get('.result-content').replaceChildren(element('p', 'panel-description', '服务器上的当前任务数据已清理。'));
      for (const raw of container.querySelectorAll<HTMLElement>('[data-raw]')) raw.hidden = true;
      get<HTMLElement>('.job-progress').hidden = true;
      get<HTMLButtonElement>('[data-action=download]').disabled = true;
      get<HTMLButtonElement>('[data-action=download-predictions]').disabled = true;
      get('.result-status').textContent = '';
      errorBox.hidden = true; update();
    } catch (error) { if (!disposed && state.job?.job_id === id) fail(error); }
    finally { deleting = false; if (!disposed) update(); }
  });
  get('[data-action=download]').addEventListener('click', () => { if (state.report) download(`${page.slug.replaceAll('/', '-')}-report.json`, { job_id: state.reportJobId, ...state.report }); });
  get('[data-action=download-predictions]').addEventListener('click', () => {
    if (state.report?.prediction_input) download(`${page.slug.replaceAll('/', '-')}-predictions.json`, { metric: page.slug, prediction_input: state.report.prediction_input });
  });
  renderReport(); update(); renderJob();
  void loadTemplate();
  const storedId = new URL(location.href).searchParams.get('job');
  if (storedId && /^[a-f0-9]{32}$/.test(storedId)) void poll(storedId);
  else if (state.job) void poll(state.job.job_id);
  return () => { disposed = true; life.abort(); pollController.abort(); };
}
