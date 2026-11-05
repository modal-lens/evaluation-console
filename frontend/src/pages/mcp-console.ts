import { element, message } from '../components/dom.js';

export const mcpRoute = '/evaluation/mcp-console';
interface Tool { name: string; title?: string; description?: string; inputSchema: unknown }
interface Event { time: string; level: string; message: string }
interface Service {
  id: string; name: string; url: string; auth_type: 'none' | 'bearer' | 'api_key'; timeout: number; protocol: string; transport_type: 'streamable_http' | 'sse';
  status: string; tools_count: number; has_secret: boolean; needs_credentials: boolean; last_error: string; protocol_version?: string;
  tools?: Tool[]; output?: Event[]; result?: unknown;
}
export interface MCPState { selected?: string; tools?: Record<string, string>; arguments?: Record<string, string> }
const prefix = '/api/v1/mcp-console/services';
const labels: Record<string, string> = { connected: '已连接', disconnected: '未连接', connecting: '连接中', error: '连接异常' };

function readableResult(value: unknown): string {
  const content = value && typeof value === 'object' ? (value as { content?: unknown }).content : undefined;
  if (Array.isArray(content)) {
    const texts = content.flatMap(item => {
      if (!item || typeof item !== 'object' || item.type !== 'text' || typeof item.text !== 'string') return [];
      try { return [JSON.stringify(JSON.parse(item.text) as unknown, null, 2)]; } catch { return [item.text]; }
    });
    if (texts.length) return texts.join('\n\n');
  }
  return JSON.stringify(value, null, 2);
}

export function mountMCP(container: HTMLElement, state: MCPState): () => void {
  let disposed = false, busy = false, editing: string | undefined;
  let services: Service[] = [], selected: Service | undefined, polling: ReturnType<typeof setTimeout> | undefined;
  let detailVersion = 0;
  const life = new AbortController();
  state.tools ??= {}; state.arguments ??= {};
  container.innerHTML = `
    <div class="mcp-page">
      <section class="page-heading"><div class="eyebrow">SERVICE CONSOLE</div><h1>MCP 控制台</h1><p>管理 Streamable HTTP 和 SSE 服务，查看连接状态、发现工具并手动调用。</p></section>
      <p class="error-feedback mcp-feedback" role="alert" hidden></p>
      <div class="mcp-grid">
        <section class="panel mcp-service-panel" aria-label="MCP服务管理"><div class="panel-heading"><h2>MCP 服务管理</h2><button type="button" class="button mcp-primary" data-action="add">＋ 添加服务</button></div><div class="mcp-services"></div><p class="panel-description mcp-service-note">选择服务后查看工具和输出。</p></section>
        <section class="panel mcp-output-panel" aria-label="MCP服务输出"><div class="panel-heading"><div class="mcp-output-title"><h2>输出</h2><span class="mcp-selected-name">尚未选择服务</span></div><div class="mcp-actions"><button type="button" class="button secondary" data-action="clear">清空</button><button type="button" class="button secondary" data-action="copy">复制</button></div></div>
          <div class="mcp-tool-section" hidden><div class="mcp-tool-heading"><label for="mcp-tool">可用工具</label><button type="button" class="button text-button" data-action="refresh-tools">刷新工具</button></div><select id="mcp-tool" aria-label="选择MCP工具"></select><p class="panel-description mcp-tool-description"></p><details class="mcp-schema"><summary>查看工具参数结构</summary><pre></pre></details><label for="mcp-arguments">工具参数 JSON</label><textarea id="mcp-arguments" aria-label="MCP工具参数JSON" spellcheck="false" placeholder="{}"></textarea><div class="mcp-actions"><button type="button" class="button text-button" data-action="format">格式化参数</button><button type="button" class="button mcp-primary" data-action="call">调用工具</button></div></div>
          <div class="mcp-output" tabindex="0" role="region" aria-label="服务运行输出"><pre>添加并连接一个服务后，输出会显示在这里。</pre></div><details class="mcp-raw-result" hidden><summary>查看完整 MCP 返回</summary><pre></pre></details>
          <p class="mcp-status" role="status" aria-live="polite">尚未选择服务</p>
        </section>
      </div>
      <dialog class="mcp-editor" aria-labelledby="mcp-editor-title"><form><div class="panel-heading"><h2 id="mcp-editor-title">添加 MCP 服务</h2><button type="button" class="button text-button" data-action="close-editor" aria-label="关闭服务编辑">关闭</button></div><label>服务名称<input name="name" required maxlength="100" placeholder="例如：知识图谱服务"></label><label>传输方式<select name="transport_type"><option value="streamable_http">Streamable HTTP</option><option value="sse">SSE（旧版 /sse 端点）</option></select></label><label>MCP 服务地址<input name="url" type="url" required placeholder="http://127.0.0.1:8000/mcp"></label><p class="panel-description">填写完整 MCP 地址。call_mcp.py 的 /sse 地址请选择 SSE，协议选择自动协商。</p><label>认证方式<select name="auth_type"><option value="none">无需认证</option><option value="bearer">Bearer Token</option><option value="api_key">X-API-Key</option></select></label><label class="mcp-secret-label">认证令牌<input name="secret" type="password" autocomplete="new-password" maxlength="4096" placeholder="编辑时留空保留现有令牌"></label><p class="panel-description">令牌仅保存在本次后端进程内，重启后需要重新填写。</p><div class="mcp-form-options"><label>请求超时（秒）<input name="timeout" type="number" min="5" max="300" required value="60"></label><label>协议兼容模式<select name="protocol"><option value="auto">自动协商</option><option value="2025-11-25">2025 会话式协议</option><option value="2026-07-28">2026 无状态协议</option></select></label></div><p class="error-feedback mcp-editor-error" role="alert" hidden></p><div class="submit-row"><button type="submit" class="button mcp-primary">保存服务</button><button type="button" class="button secondary" data-action="cancel-editor">取消</button></div></form></dialog>
    </div>`;
  const get = <T extends HTMLElement>(selector: string) => container.querySelector<T>(selector)!;
  const feedback = get<HTMLElement>('.mcp-feedback');
  const editor = get<HTMLDialogElement>('dialog');
  const form = editor.querySelector<HTMLFormElement>('form')!;
  const field = (name: string) => form.elements.namedItem(name) as HTMLInputElement;
  const toolSelect = get<HTMLSelectElement>('#mcp-tool');
  const args = get<HTMLTextAreaElement>('#mcp-arguments');
  const output = get<HTMLPreElement>('.mcp-output pre');
  const showError = (error: unknown) => { feedback.hidden = false; feedback.textContent = message(error); };
  const toolKey = () => `${state.selected}:${toolSelect.value}`;
  const request = async <T>(path = '', body?: unknown): Promise<T> => {
    const response = await fetch(prefix + path, { method: body === undefined ? 'GET' : 'POST', ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }), signal: AbortSignal.any([life.signal, AbortSignal.timeout(330_000)]) });
    let value: { success?: boolean; data?: T; detail?: unknown; error?: { message?: string } };
    try { value = await response.json() as typeof value; } catch { throw new Error('MCP接口没有返回有效JSON，请检查后端服务。'); }
    if (!response.ok || !value.success) throw new Error(typeof value.detail === 'string' ? value.detail : value.error?.message ?? (response.status === 422 ? '服务配置或工具参数格式不正确，请检查填写内容。' : `MCP操作失败（HTTP ${response.status}），请确认后端已更新并启动。`));
    return value.data!;
  };
  const updateTool = () => {
    const tool = selected?.tools?.find(tool => tool.name === toolSelect.value);
    get('.mcp-tool-description').textContent = tool?.description ?? '';
    get('.mcp-schema pre').textContent = JSON.stringify(tool?.inputSchema ?? {}, null, 2);
    args.value = state.arguments![toolKey()] ?? '{}';
    get<HTMLButtonElement>('[data-action=call]').disabled = busy || !tool || selected?.status !== 'connected';
    get<HTMLButtonElement>('[data-action=format]').disabled = busy || !tool;
    args.readOnly = busy;
  };
  const draw = () => {
    const list = get('.mcp-services'); list.replaceChildren();
    if (!services.length) list.append(element('p', 'mcp-empty', '尚未添加服务，点击“添加服务”开始配置。'));
    for (const summary of services) {
      const service = selected?.id === summary.id ? selected : summary;
      const card = element('article', `mcp-service${service.id === state.selected ? ' selected' : ''}`);
      const choose = element('button', 'mcp-service-select'); choose.type = 'button'; choose.disabled = busy;
      choose.setAttribute('aria-label', `选择服务 ${service.name}`);
      const name = element('strong', '', service.name), status = element('span', `mcp-state state-${service.status}`, labels[service.status] ?? service.status);
      choose.append(element('span', 'mcp-service-icon', '◎'), name, status);
      choose.addEventListener('click', () => { state.selected = service.id; selected = undefined; draw(); void loadSelected(); });
      card.append(choose, element('p', 'mcp-transport', 'Streamable HTTP'), element('p', 'mcp-service-url', service.url));
      if (service.needs_credentials) card.append(element('p', 'mcp-credential-note', '需重新填写认证令牌'));
      const actions = element('div', 'mcp-card-actions');
      for (const [label, callback] of [
        [service.status === 'connected' ? '断开' : '连接', () => void operate(service.id, service.status === 'connected' ? '/disconnect' : '/connect')],
        ['编辑', () => openEditor(service)], ['删除', () => void remove(service.id)],
      ] as const) {
        const button = element('button', 'button text-button', label); button.type = 'button'; button.disabled = busy;
        button.setAttribute('aria-label', `${label} ${service.name}`); button.addEventListener('click', callback); actions.append(button);
      }
      card.append(actions); list.append(card);
    }
    get<HTMLButtonElement>('[data-action=add]').disabled = busy;
    get('.mcp-selected-name').textContent = selected?.name ?? '尚未选择服务';
    const connected = selected?.status === 'connected';
    get<HTMLElement>('.mcp-tool-section').hidden = !connected;
    const previous = state.tools![state.selected ?? ''];
    toolSelect.replaceChildren();
    for (const tool of selected?.tools ?? []) { const option = element('option', '', tool.title ? `${tool.title}（${tool.name}）` : tool.name); option.value = tool.name; toolSelect.append(option); }
    if (previous && selected?.tools?.some(t => t.name === previous)) toolSelect.value = previous;
    state.tools![state.selected ?? ''] = toolSelect.value;
    toolSelect.disabled = busy || !toolSelect.options.length;
    if (connected && !toolSelect.options.length) get('.mcp-tool-description').textContent = '服务已连接，当前未提供可调用工具。';
    get<HTMLButtonElement>('[data-action=refresh-tools]').disabled = busy || !connected;
    const events = (selected?.output ?? []).map(event => `[${new Date(event.time).toLocaleTimeString('zh-CN', { hour12: false })}] ${event.level === 'error' ? '错误：' : ''}${event.message}`).join('\n');
    const result = selected?.result == null ? '' : '\n\n最近一次返回：\n' + readableResult(selected.result);
    output.textContent = events + result || (selected ? '暂无输出。连接服务或调用工具后会在此显示。' : '添加并连接一个服务后，输出会显示在这里。');
    get<HTMLElement>('.mcp-raw-result').hidden = selected?.result == null;
    get('.mcp-raw-result pre').textContent = selected?.result == null ? '' : JSON.stringify(selected.result, null, 2);
    get<HTMLButtonElement>('[data-action=clear]').disabled = busy || !selected;
    get<HTMLButtonElement>('[data-action=copy]').disabled = !events && !result;
    const status = get('.mcp-status'); status.className = `mcp-status state-${selected?.status ?? 'disconnected'}`;
    status.textContent = selected ? `${labels[selected.status] ?? selected.status} · ${selected.tools_count} 个工具${selected.protocol_version ? ' · 协议 ' + selected.protocol_version : ''}${busy ? ' · 操作执行中…' : ''}` : '尚未选择服务';
    updateTool();
    if (connected && !toolSelect.options.length) get('.mcp-tool-description').textContent = '服务已连接，当前未提供可调用工具。';
  };
  const loadSelected = async () => {
    const id = state.selected; if (!id) return;
    const version = ++detailVersion;
    try { const service = await request<Service>('/' + id); if (!disposed && state.selected === id && version === detailVersion) { selected = service; draw(); } }
    catch (error) { if (!disposed && state.selected === id && version === detailVersion) showError(error); }
  };
  const reload = async () => {
    const list = await request<Service[]>(); if (disposed) return;
    services = list;
    if (!list.some(service => service.id === state.selected)) { state.selected = list[0]?.id; selected = undefined; }
    draw(); await loadSelected();
  };
  const poll = async () => {
    if (!busy || disposed) return;
    await loadSelected();
    if (busy && !disposed) polling = setTimeout(() => void poll(), 1000);
  };
  const operate = async (id: string, action: string, body: unknown = {}) => {
    if (busy) return;
    if (selected?.id !== id) selected = services.find(service => service.id === id);
    state.selected = id; feedback.hidden = true; busy = true; draw();
    polling = setTimeout(() => void poll(), 500);
    try { const service = await request<Service>('/' + id + action, body); if (!disposed && state.selected === id) { detailVersion++; selected = service; } }
    catch (error) { if (!disposed) showError(error); }
    finally {
      busy = false; if (polling) clearTimeout(polling);
      if (!disposed) { try { await reload(); } catch (error) { showError(error); draw(); } }
    }
  };
  const remove = async (id: string) => {
    if (busy) return;
    busy = true; feedback.hidden = true; draw();
    try { await request('/' + id + '/delete', {}); if (state.selected === id) { state.selected = undefined; selected = undefined; } }
    catch (error) { if (!disposed) showError(error); }
    finally { busy = false; if (!disposed) { try { await reload(); } catch (error) { showError(error); draw(); } } }
  };
  const authVisibility = () => { get<HTMLElement>('.mcp-secret-label').hidden = field('auth_type').value === 'none'; };
  const transportOptions = () => {
    const sse = field('transport_type').value === 'sse';
    const modern = form.querySelector<HTMLOptionElement>('option[value="2026-07-28"]')!;
    modern.disabled = sse;
    if (sse && field('protocol').value === '2026-07-28') field('protocol').value = 'auto';
  };
  const openEditor = (service?: Service) => {
    if (busy) return;
    editing = service?.id; form.reset();
    get('#mcp-editor-title').textContent = service ? '编辑 MCP 服务' : '添加 MCP 服务';
    for (const name of ['name', 'url', 'auth_type', 'timeout', 'protocol', 'transport_type'] as const) if (service) field(name).value = String(service[name]);
    field('secret').value = ''; get<HTMLElement>('.mcp-editor-error').hidden = true;
    authVisibility(); transportOptions(); editor.showModal(); field('name').focus();
  };
  get('[data-action=add]').addEventListener('click', () => openEditor());
  for (const selector of ['[data-action=close-editor]', '[data-action=cancel-editor]']) get(selector).addEventListener('click', () => editor.close());
  editor.addEventListener('close', () => { field('secret').value = ''; });
  field('auth_type').addEventListener('change', authVisibility);
  field('transport_type').addEventListener('change', transportOptions);
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy || !form.reportValidity()) return;
    busy = true;
    for (const control of form.querySelectorAll<HTMLInputElement | HTMLButtonElement>('input,select,button')) control.disabled = true;
    try {
      const service = await request<Service>(editing ? '/' + editing + '/edit' : '', { name: field('name').value.trim(), url: field('url').value.trim(), auth_type: field('auth_type').value, secret: field('auth_type').value === 'none' ? '' : field('secret').value, timeout: Number(field('timeout').value), protocol: field('protocol').value, transport_type: field('transport_type').value });
      if (disposed) return;
      state.selected = service.id; selected = service; editor.close(); feedback.hidden = true;
    } catch (error) { if (!disposed) { get<HTMLElement>('.mcp-editor-error').hidden = false; get('.mcp-editor-error').textContent = message(error); } }
    finally {
      busy = false;
      for (const control of form.querySelectorAll<HTMLInputElement | HTMLButtonElement>('input,select,button')) control.disabled = false;
      if (!disposed) { try { await reload(); } catch (error) { showError(error); draw(); } }
    }
  });
  toolSelect.addEventListener('change', () => { state.tools![state.selected ?? ''] = toolSelect.value; updateTool(); });
  args.addEventListener('input', () => { state.arguments![toolKey()] = args.value; });
  const argumentsValue = () => {
    let value: unknown; try { value = JSON.parse(args.value); } catch { throw new Error('工具参数不是有效JSON，请检查格式。'); }
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('工具参数必须是JSON对象。');
    return value;
  };
  get('[data-action=format]').addEventListener('click', () => { try { args.value = JSON.stringify(argumentsValue(), null, 2); state.arguments![toolKey()] = args.value; feedback.hidden = true; } catch (error) { showError(error); } });
  get('[data-action=call]').addEventListener('click', () => { if (!selected || !toolSelect.value) return; try { void operate(selected.id, '/tools/call', { name: toolSelect.value, arguments: argumentsValue() }); } catch (error) { showError(error); } });
  get('[data-action=refresh-tools]').addEventListener('click', () => { if (selected) void operate(selected.id, '/tools/refresh'); });
  get('[data-action=clear]').addEventListener('click', () => { if (selected) void operate(selected.id, '/output/clear'); });
  get('[data-action=copy]').addEventListener('click', async () => {
    try {
      let copied = false;
      try { if (navigator.clipboard) { await navigator.clipboard.writeText(output.textContent ?? ''); copied = true; } } catch { /* Use the selection fallback below. */ }
      if (!copied) { const temporary = element('textarea'); temporary.value = output.textContent ?? ''; temporary.className = 'mcp-copy-buffer'; container.append(temporary); temporary.select(); copied = document.execCommand('copy'); temporary.remove(); }
      if (!copied) throw new Error('浏览器无法自动复制，请在输出框中选择文本后复制。');
      get('[data-action=copy]').textContent = '已复制';
    } catch (error) { showError(error); }
  });
  draw(); void reload().catch(error => { if (!disposed) showError(error); });
  return () => { disposed = true; life.abort(); if (polling) clearTimeout(polling); field('secret').value = ''; editor.close(); };
}
