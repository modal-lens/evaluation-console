import { test, expect } from '@playwright/test';
import { createServer } from 'node:http';

async function remoteServer({ unauthorized = false, sse = false } = {}) {
  const calls = [];
  const credentials = [];
  let eventStream;
  const tools = ['echo', 'status', 'ping'].map(name => ({ name, description: name === 'echo' ? '回显输入文本' : '读取服务状态', inputSchema: { type: 'object', properties: { text: { type: 'string' } } } }));
  const server = createServer(async (req, res) => {
    credentials.push(req.headers['x-api-key']);
    if (unauthorized) { res.writeHead(401); res.end('unauthorized'); return; }
    if (sse && req.method === 'GET') {
      eventStream = res;
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      res.write('event: endpoint\ndata: /messages?session_id=browser-sse\n\n');
      return;
    }
    if (req.method === 'DELETE') { res.writeHead(204); res.end(); return; }
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString()); calls.push(body);
    res.setHeader('Content-Type', 'application/json');
    const send = result => {
      const message = JSON.stringify({ jsonrpc: '2.0', id: body.id, result });
      if (sse) { eventStream.write('event: message\ndata: ' + message + '\n\n'); res.writeHead(202); res.end(); }
      else res.end(message);
    };
    if (body.method === 'initialize') {
      res.setHeader('MCP-Session-Id', 'browser-session');
      send({ protocolVersion: '2025-11-25', capabilities: { tools: {} }, serverInfo: { name: 'browser-mcp', version: '1' } });
    } else if (body.method === 'notifications/initialized') { res.writeHead(202); res.end(); }
    else if (body.method === 'tools/list') send({ tools });
    else if (body.method === 'tools/call') {
      if (sse) { send({ content: [{ type: 'text', text: body.params.arguments.text }], isError: false }); return; }
      res.setHeader('Content-Type', 'text/event-stream');
      const data = { jsonrpc: '2.0', id: body.id, result: { content: [{ type: 'text', text: JSON.stringify({ status: 'success', message: body.params.arguments.text || '服务运行正常', tools_count: 3 }) }], isError: false } };
      res.end('event: message\ndata: ' + JSON.stringify(data) + '\n\n');
    } else send({});
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { url: `http://127.0.0.1:${server.address().port}/${sse ? 'sse' : 'mcp'}`, calls, credentials, close: async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); } };
}

async function addService(page, url, name, sse = false) {
  await page.getByRole('button', { name: '添加服务', exact: false }).click();
  await page.getByLabel('服务名称', { exact: true }).fill(name);
  await page.getByLabel('MCP 服务地址', { exact: true }).fill(url);
  if (sse) {
    await page.getByRole('combobox', { name: '传输方式', exact: true }).selectOption('sse');
    await page.getByRole('combobox', { name: '认证方式', exact: true }).selectOption('api_key');
    await page.getByLabel('认证令牌', { exact: true }).fill('browser-sse-test-key');
  }
  await page.getByRole('button', { name: '保存服务', exact: true }).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.getByRole('button', { name: `连接 ${name}`, exact: true })).toBeEnabled();
}

async function cleanup(page) {
  const response = await page.request.get('/api/v1/mcp-console/services');
  const data = await response.json();
  for (const service of data.data ?? []) await page.request.post(`/api/v1/mcp-console/services/${service.id}/delete`, { data: {} });
}

test('MCP服务真实连接、工具调用、复制清空、编辑和断开', async ({ page, context }) => {
  const remote = await remoteServer();
  try {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.goto('/');
    await page.getByRole('link', { name: /MCP 控制台/ }).click();
    await expect(page.getByRole('heading', { name: 'MCP 控制台', exact: true })).toBeVisible();
    await addService(page, remote.url, '知识图谱服务');
    await page.getByRole('button', { name: '连接 知识图谱服务', exact: true }).click();
    await expect(page.locator('.mcp-status')).toContainText('已连接');
    await expect(page.locator('.mcp-status')).toContainText('3 个工具');
    await expect(page.getByLabel('选择MCP工具')).toHaveValue('echo');
    await page.getByLabel('MCP工具参数JSON').fill('{');
    await page.getByRole('button', { name: '调用工具', exact: true }).click();
    await expect(page.locator('.mcp-feedback')).toContainText('有效JSON');
    expect(remote.calls.filter(c => c.method === 'tools/call')).toHaveLength(0);
    await page.getByLabel('MCP工具参数JSON').fill(JSON.stringify({ text: '页面协议测试 <script>window.injected=true</script>' }));
    await page.getByRole('button', { name: '调用工具', exact: true }).click();
    await expect(page.getByRole('region', { name: '服务运行输出', exact: true })).toContainText('页面协议测试');
    await expect(page.getByRole('region', { name: '服务运行输出', exact: true })).toContainText('调用完成');
    expect(await page.evaluate(() => window.injected)).toBeUndefined();
    expect(remote.calls.filter(c => c.method === 'tools/call')).toHaveLength(1);
    await page.getByRole('button', { name: '复制', exact: true }).click();
    await expect(page.getByRole('button', { name: '已复制', exact: true })).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toContain('页面协议测试');
    await page.screenshot({ path: 'artifacts/mcp-console-desktop.png', fullPage: true });
    await page.getByRole('button', { name: '清空', exact: true }).click();
    await expect(page.getByRole('region', { name: '服务运行输出', exact: true })).toContainText('暂无输出');
    await page.reload();
    await expect(page.getByRole('button', { name: '断开 知识图谱服务', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '编辑 知识图谱服务', exact: true }).click();
    await page.getByLabel('服务名称', { exact: true }).fill('已编辑服务');
    await page.getByRole('button', { name: '保存服务', exact: true }).click();
    await expect(page.getByRole('button', { name: '连接 已编辑服务', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: '连接 已编辑服务', exact: true }).click();
    await expect(page.getByRole('button', { name: '断开 已编辑服务', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: '断开 已编辑服务', exact: true }).click();
    await expect(page.locator('.mcp-status')).toContainText('未连接');
    await page.getByRole('button', { name: '删除 已编辑服务', exact: true }).click();
    await expect(page.locator('.mcp-empty')).toContainText('尚未添加服务');
  } finally { try { await cleanup(page); } finally { await remote.close(); } }
});

test('旧版SSE通过页面连接、鉴权、调用及保存传输方式', async ({ page }) => {
  const remote = await remoteServer({ sse: true });
  try {
    await page.goto('/evaluation/mcp-console');
    await addService(page, remote.url, 'Langflow SSE', true);
    await page.getByRole('button', { name: '连接 Langflow SSE', exact: true }).click();
    await expect(page.locator('.mcp-status')).toContainText('已连接');
    await page.getByLabel('MCP工具参数JSON').fill(JSON.stringify({ text: 'SSE页面调用成功' }));
    await page.getByRole('button', { name: '调用工具', exact: true }).click();
    await expect(page.getByRole('region', { name: '服务运行输出', exact: true })).toContainText('SSE页面调用成功');
    expect(remote.calls.filter(c => c.method === 'tools/call')).toHaveLength(1);
    expect(remote.credentials.every(value => value === 'browser-sse-test-key')).toBe(true);
    await page.getByRole('button', { name: '编辑 Langflow SSE', exact: true }).click();
    await expect(page.getByRole('combobox', { name: '传输方式', exact: true })).toHaveValue('sse');
    await page.getByRole('button', { name: '取消', exact: true }).click();
    await page.getByRole('button', { name: '断开 Langflow SSE', exact: true }).click();
    await expect(page.locator('.mcp-status')).toContainText('未连接');
  } finally { try { await cleanup(page); } finally { await remote.close(); } }
});

test('MCP连接失败显示实际错误，手机页面无横向溢出', async ({ page }) => {
  const remote = await remoteServer({ unauthorized: true });
  try {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/evaluation/mcp-console');
    await addService(page, remote.url, '认证失败服务');
    await page.getByRole('button', { name: '连接 认证失败服务', exact: true }).click();
    await expect(page.locator('.mcp-feedback')).toContainText('认证失败');
    await expect(page.locator('.mcp-status')).toContainText('连接异常');
    await expect(page.locator('.mcp-tool-section')).toBeHidden();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    await page.screenshot({ path: 'artifacts/mcp-console-mobile.png', fullPage: true });
  } finally { try { await cleanup(page); } finally { await remote.close(); } }
});
