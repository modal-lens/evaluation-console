import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import { createFrontendServer } from '../server.mjs';

const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${server.address().port}`)));
const close = server => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });

test('预测 JSON 上传代理允许大于 5 MB，并限制整个评测请求为 12 MB', async () => {
  const backend = createServer(async (request, response) => {
    let length = 0;
    for await (const chunk of request) length += chunk.length;
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify({ length }));
  });
  const backendUrl = await listen(backend);
  const frontend = createFrontendServer({ root: '.', backendUrl });
  const base = await listen(frontend);
  try {
    const body = 'x'.repeat(6 * 1024 * 1024);
    const result = await fetch(`${base}/api/v1/evaluation-runs/entity-relation-extraction`, { method: 'POST', body });
    assert.equal(result.status, 200);
    assert.equal((await result.json()).length, body.length);
    assert.equal((await fetch(`${base}/api/v1/evaluation/entity-extraction`, { method: 'POST', body })).status, 413);
    assert.equal((await fetch(`${base}/api/v1/evaluation-runs/entity-relation-extraction`, { method: 'POST', body: 'x'.repeat(13 * 1024 * 1024) })).status, 413);
  } finally { await close(frontend); await close(backend); }
});

test('前端子页面刷新可访问，评测请求保留路径、参数及后端错误', async () => {
  const backend = createServer(async (request, response) => {
    let body = '';
    for await (const chunk of request) body += chunk;
    response.setHeader('Content-Type', 'application/json');
    if (body.includes('invalid')) { response.statusCode = 422; response.end(JSON.stringify({ detail: '人工标注不完整' })); return; }
    response.end(JSON.stringify({ success: true, data: { path: request.url, payload: body && JSON.parse(body) }, error: null }));
  });
  const backendUrl = await listen(backend);
  const frontend = createFrontendServer({ root: fileURLToPath(new URL('../dist', import.meta.url)), backendUrl });
  const base = await listen(frontend);
  try {
    const page = await fetch(`${base}/evaluation/cross-language/entity-fusion`);
    assert.equal(page.status, 200);
    assert.match(await page.text(), /智析/);
    const payload = { samples: [{ sample_id: '中文', predicted: [], gold: [] }] };
    const result = await fetch(`${base}/api/v1/evaluation/entity-extraction`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    assert.deepEqual((await result.json()).data, { path: '/api/v1/evaluation/entity-extraction', payload });
    const error = await fetch(`${base}/api/v1/evaluation/entity-extraction`, { method: 'POST', body: 'invalid' });
    assert.equal(error.status, 422);
    assert.deepEqual(await error.json(), { detail: '人工标注不完整' });
    assert.equal((await fetch(`${base}/missing.js`)).status, 404);
  } finally { await close(frontend); await close(backend); }
});
