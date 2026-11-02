import { evaluate, parseRequest, resolveSchema, type Contract, type Result, type Schema } from '../api/client.js';
import { download, element, message, table } from '../components/dom.js';
import { renderResults } from '../components/results.js';
import type { PageDefinition } from './catalog.js';

export interface PageState { text: string; result?: Result; submittedText?: string; evaluatedAt?: string }

function fieldRows(raw: Schema, contract: Contract, prefix = '', depth = 0): string[][] {
  if (depth > 3) return [];
  const schema = resolveSchema(raw, contract.schemas);
  return Object.entries(schema.properties ?? {}).flatMap(([name, field]) => {
    const resolved = resolveSchema(field, contract.schemas);
    const path = prefix ? `${prefix}.${name}` : name;
    const type = resolved.type ?? (resolved.anyOf ? '可为空' : 'object');
    const row = [path, type, schema.required?.includes(name) ? '必填' : '可选', field.description ?? resolved.description ?? ''];
    return [row, ...(resolved.items ? fieldRows(resolved.items, contract, `${path}[]`, depth + 1) : [])];
  });
}

export function mountEvaluation(container: HTMLElement, page: PageDefinition, contract: Contract, state: PageState): () => void {
  const operation = contract.operations[page.slug];
  if (!operation) throw new Error('接口定义缺失，请重新生成前端契约。');
  let disposed = false;
  let busy = false;
  let importVersion = 0;
  let controller: AbortController | undefined;
  container.innerHTML = `
    <section class="page-heading"><div class="eyebrow"></div><h1></h1><p></p><div class="endpoint"><span>POST</span><code></code></div></section>
    <div class="rule-banner"><span class="rule-icon">i</span><p></p></div>
    <div class="workspace-grid">
      <section class="panel input-panel"><div class="panel-heading"><div><span class="step">01</span><h2>准备评测数据</h2></div><span class="small-badge">JSON</span></div>
        <p class="panel-description">提供算法预测结果与人工标注，或先加载示例体验完整流程。</p>
        <div class="editor-tools"><button type="button" class="button secondary" data-action="example">加载示例</button><button type="button" class="button secondary" data-action="import">导入 JSON</button><button type="button" class="button text-button" data-action="format">格式化</button><input type="file" accept=".json,application/json" hidden aria-label="导入 JSON 文件"></div>
        <label class="sr-only" for="request-json">请求参数 JSON</label><textarea id="request-json" spellcheck="false" autocapitalize="off" autocomplete="off"></textarea>
        <div class="editor-footer"><span class="input-status">示例数据已就绪</span><span class="char-count"></span></div>
        <div class="feedback error-feedback" role="alert" hidden></div>
        <div class="submit-row"><button type="button" class="button primary-button" data-action="submit">开始评测 <span aria-hidden="true">↗</span></button><button type="button" class="button secondary" data-action="cancel" hidden>取消请求</button></div>
        <p class="example-description"></p>
      </section>
      <section class="panel result-panel" aria-label="评测结果"><div class="panel-heading"><div><span class="step">02</span><h2>评测结果</h2></div><button type="button" class="button text-button" data-action="download" disabled>下载结果 ↓</button></div>
        <div class="result-status" role="status" aria-live="polite"></div><div class="result-content"><div class="empty-result"><span class="empty-icon">◷</span><h3>等待一次新的评测</h3><p>准备好数据后，点击“开始评测”<br>结果将在这里呈现。</p><div class="empty-bars"><i></i><i></i><i></i><i></i><i></i></div></div></div>
        <details class="raw-result" hidden><summary>查看原始响应</summary><pre></pre></details>
      </section>
    </div>
    <details class="panel field-reference"><summary>参数说明与评测口径 <span>展开查看</span></summary><p class="panel-description">必填字段及数组长度依据后端定义校验；重复 ID、基线一致性等业务规则由接口最终校验。F1 分母为零时返回 0；请提交完整标注集。</p><div class="fields"></div></details>`;
  const get = <T extends Element>(selector: string): T => { const node = container.querySelector<T>(selector); if (!node) throw new Error(`页面元素不存在：${selector}`); return node; };
  get('.eyebrow').textContent = `${page.group} / 评测工作台`;
  get('h1').textContent = page.title;
  get('.page-heading > p').textContent = page.description;
  get('.endpoint code').textContent = operation.path;
  get('.rule-banner p').textContent = page.rule;
  get('.example-description').textContent = operation.exampleDescription;
  get('.fields').append(table(['字段', '类型', '要求', '说明'], fieldRows(operation.schema, contract), '接口参数定义'));
  const editor = get<HTMLTextAreaElement>('textarea');
  const fileInput = get<HTMLInputElement>('input[type=file]');
  const feedback = get<HTMLElement>('.error-feedback');
  const status = get<HTMLElement>('.result-status');
  const resultContent = get<HTMLElement>('.result-content');
  const submit = get<HTMLButtonElement>('[data-action=submit]');
  const cancel = get<HTMLButtonElement>('[data-action=cancel]');
  const downloadButton = get<HTMLButtonElement>('[data-action=download]');
  const raw = get<HTMLDetailsElement>('.raw-result');
  editor.value = state.text;

  const fail = (error: unknown) => { feedback.textContent = message(error); feedback.hidden = false; };
  const update = () => {
    state.text = editor.value;
    get('.char-count').textContent = `${editor.value.length.toLocaleString('zh-CN')} 字符`;
    const stale = state.result && state.submittedText !== state.text;
    get('.input-status').textContent = busy ? '正在评测，请稍候…' : stale ? '数据已修改，请重新评测' : '预测与标注均需提交';
    if (!busy) status.textContent = stale ? '以下结果对应上一次提交的数据。' : state.result ? `评测完成 · ${new Date(state.evaluatedAt!).toLocaleTimeString('zh-CN')}` : '';
  };
  const showResult = () => {
    if (!state.result) return;
    renderResults(resultContent, page.kind, state.result);
    raw.hidden = false;
    raw.querySelector('pre')!.textContent = JSON.stringify({ success: true, data: state.result, error: null }, null, 2);
    downloadButton.disabled = false;
    update();
  };
  const setBusy = (value: boolean) => {
    busy = value;
    submit.disabled = value;
    submit.textContent = value ? '正在评测…' : '开始评测 ↗';
    cancel.hidden = !value;
    editor.readOnly = value;
    for (const button of container.querySelectorAll<HTMLButtonElement>('.editor-tools button')) button.disabled = value;
    resultContent.setAttribute('aria-busy', String(value));
    update();
  };
  editor.addEventListener('input', () => { importVersion++; feedback.hidden = true; update(); });
  get('[data-action=example]').addEventListener('click', () => { importVersion++; editor.value = JSON.stringify(operation.example, null, 2); feedback.hidden = true; update(); });
  get('[data-action=format]').addEventListener('click', () => {
    try { const value = JSON.parse(editor.value) as unknown; editor.value = JSON.stringify(value, null, 2); update(); feedback.hidden = true; }
    catch { fail(new Error('JSON 格式不正确，无法格式化。')); }
  });
  get('[data-action=import]').addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async () => {
    const file = fileInput.files?.[0];
    fileInput.value = '';
    if (!file) return;
    const version = ++importVersion;
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error('JSON 文件不能超过 5 MB。');
      const text = (await file.text()).replace(/^\uFEFF/, '');
      if (disposed || busy || version !== importVersion) return;
      const payload = parseRequest(text, operation, contract.schemas);
      editor.value = JSON.stringify(payload, null, 2);
      feedback.hidden = true;
      update();
      get('.input-status').textContent = `已导入 ${file.name}`;
    } catch (error) { if (!disposed && version === importVersion) fail(error); }
  });
  submit.addEventListener('click', async () => {
    if (busy) return;
    feedback.hidden = true;
    let payload: unknown;
    try { payload = parseRequest(editor.value, operation, contract.schemas); }
    catch (error) { fail(error); editor.focus(); return; }
    importVersion++;
    const submittedText = editor.value;
    controller = new AbortController();
    setBusy(true);
    status.textContent = '正在请求评测服务…';
    try {
      const result = await evaluate(operation.path, payload, controller.signal);
      if (disposed) return;
      state.result = result;
      state.submittedText = submittedText;
      state.evaluatedAt = new Date().toISOString();
      showResult();
    } catch (error) { if (!disposed) { fail(error); status.textContent = '本次评测未完成。'; } }
    finally { if (!disposed) { setBusy(false); if (!feedback.hidden) status.textContent = state.result ? '本次评测未完成；以下保留上一次成功结果。' : '本次评测未完成，请查看错误提示。'; } }
  });
  cancel.addEventListener('click', () => controller?.abort());
  downloadButton.addEventListener('click', () => {
    if (state.result) download(`${page.slug.replaceAll('/', '-')}-${state.evaluatedAt!.replaceAll(':', '-')}.json`, {
      interface: operation.path, evaluated_at: state.evaluatedAt, request: JSON.parse(state.submittedText!) as unknown,
      response: { success: true, data: state.result, error: null },
    });
  });
  showResult();
  update();
  return () => { disposed = true; importVersion++; controller?.abort(); };
}
