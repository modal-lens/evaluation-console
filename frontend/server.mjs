import { createServer, request as httpRequest } from 'node:http';
import { request as httpsRequest } from 'node:https';
import { readFile, stat } from 'node:fs/promises';
import { watch } from 'node:fs';
import { extname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml' };
const maxBody = 5 * 1024 * 1024;

function json(response, status, message) {
  if (response.headersSent || response.destroyed) return;
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  response.end(JSON.stringify({ success: false, data: null, error: { message } }));
}

export function createFrontendServer({ root, backendUrl = 'http://127.0.0.1:50111', timeoutMs = 65_000 }) {
  const directory = resolve(root);
  const backend = new URL(backendUrl);
  if (!['http:', 'https:'].includes(backend.protocol) || backend.pathname !== '/' || backend.search || backend.hash || backend.username || backend.password) throw new Error('BACKEND_URL 必须是无路径、无账号密码的 HTTP(S) 服务地址。');
  return createServer(async (incoming, outgoing) => {
    outgoing.setHeader('X-Content-Type-Options', 'nosniff');
    outgoing.setHeader('X-Frame-Options', 'SAMEORIGIN');
    outgoing.setHeader('Referrer-Policy', 'same-origin');
    let path;
    try { path = decodeURIComponent(new URL(incoming.url, 'http://localhost').pathname); }
    catch { json(outgoing, 400, '请求地址不正确'); return; }
    if (path.startsWith('/api/') || ['/health', '/ready', '/openapi.json', '/docs', '/docs/oauth2-redirect', '/redoc'].includes(path)) {
      const target = new URL(incoming.url, backend);
      // 锁定上游地址，拒绝双斜杠 URL 改写目标主机。
      if (target.origin !== backend.origin) { json(outgoing, 400, '代理请求地址不正确'); return; }
      if (!['GET', 'HEAD', 'POST'].includes(incoming.method)) { json(outgoing, 405, '不支持该请求方法'); return; }
      const transport = backend.protocol === 'https:' ? httpsRequest : httpRequest;
      // A prediction file may be 10 MB, plus gold/config and multipart boundaries.
      const bodyLimit = path.startsWith('/api/v1/evaluation-runs/') ? 12 * 1024 * 1024 : maxBody;
      let upstream;
      try {
        const chunks = [];
        let length = 0;
        for await (const chunk of incoming) {
          length += chunk.length;
          if (length > bodyLimit) { json(outgoing, 413, `请求数据不能超过 ${bodyLimit / 1024 / 1024} MB`); return; }
          chunks.push(chunk);
        }
        const body = Buffer.concat(chunks);
        upstream = transport(target, {
          method: incoming.method, timeout: path.startsWith('/api/v1/mcp-console/') ? 330_000 : timeoutMs,
          headers: { 'Content-Type': incoming.headers['content-type'] || 'application/json', Accept: incoming.headers.accept || '*/*', ...(body.length ? { 'Content-Length': body.length } : {}) },
        }, response => {
          outgoing.statusCode = response.statusCode || 502;
          outgoing.setHeader('Content-Type', response.headers['content-type'] || 'application/json');
          outgoing.setHeader('Cache-Control', 'no-store');
          if (response.headers.location) outgoing.setHeader('Location', response.headers.location);
          response.on('error', () => outgoing.destroy());
          response.pipe(outgoing);
        });
        upstream.on('timeout', () => upstream.destroy(new Error('timeout')));
        upstream.on('error', error => json(outgoing, error.message === 'timeout' ? 504 : 502, error.message === 'timeout' ? '后端响应超时，请稍后重试' : '无法连接后端服务，请检查 BACKEND_URL 与服务状态'));
        outgoing.on('close', () => { if (!outgoing.writableFinished) upstream.destroy(); });
        upstream.end(body);
      } catch { upstream?.destroy(); json(outgoing, 400, '请求传输中断'); }
      return;
    }
    if (!['GET', 'HEAD'].includes(incoming.method)) { json(outgoing, 405, '不支持该请求方法'); return; }
    if (path.includes('\0') || path.includes('\\')) { json(outgoing, 400, '请求地址不正确'); return; }
    const filename = resolve(directory, `.${path}`);
    if (filename !== directory && !filename.startsWith(directory + sep)) { json(outgoing, 403, '禁止访问'); return; }
    const spa = path === '/' || path.startsWith('/evaluation/');
    const selected = spa ? resolve(directory, 'index.html') : filename;
    try {
      if (!(await stat(selected)).isFile()) { json(outgoing, 404, '资源不存在'); return; }
      const content = await readFile(selected);
      outgoing.setHeader('Content-Type', mime[extname(selected)] || 'application/octet-stream');
      outgoing.setHeader('Cache-Control', 'no-store');
      outgoing.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'");
      outgoing.end(incoming.method === 'HEAD' ? undefined : content);
    } catch { json(outgoing, 404, '资源不存在，请先运行 npm run build'); }
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = fileURLToPath(new URL('./dist', import.meta.url));
  const host = process.env.HOST || '0.0.0.0';
  const port = Number(process.env.PORT || '50112');
  const backendUrl = process.env.BACKEND_URL || 'http://127.0.0.1:50111';
  const server = createFrontendServer({ root, backendUrl });
  server.on('error', error => { console.error(`[${new Date().toISOString()}] 前端服务启动失败：${error.message}`); process.exitCode = 1; });
  server.listen(port, host, () => console.log(`[${new Date().toISOString()}] 前端服务启动完成：进程ID=${process.pid}，页面=http://${host}:${server.address().port}，后端代理=${backendUrl}`));
  let compiler;
  let watcher;
  if (process.argv.includes('--dev')) {
    compiler = spawn(process.execPath, [fileURLToPath(new URL('./node_modules/typescript/bin/tsc', import.meta.url)), '--watch', '--preserveWatchOutput'], { cwd: fileURLToPath(new URL('./', import.meta.url)), stdio: 'inherit', windowsHide: true });
    watcher = watch(fileURLToPath(new URL('./', import.meta.url)), (_, filename) => {
      if (filename === 'index.html' || filename === 'styles.css') {
        import('node:fs/promises').then(fs => fs.copyFile(new URL(filename, import.meta.url), new URL(`dist/${filename}`, import.meta.url))).catch(console.error);
      }
    });
  }
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => { console.log(`[${new Date().toISOString()}] 前端服务收到停止信号：${signal}`); compiler?.kill(); watcher?.close(); server.close(); });
}
