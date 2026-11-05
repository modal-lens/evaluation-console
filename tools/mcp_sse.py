"""旧版 MCP HTTP+SSE：GET 长连接接收消息，POST 到服务声明的同源端点。"""
import json
import socket
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic
from urllib.parse import urljoin, urlsplit

import httpx

from tools.mcp_client import MAX_RESPONSE, MCPError


class SSEChannel:
    def __init__(self, client, url, headers, timeout):
        self.client, self.url, self.headers, self.timeout = client, url, headers, timeout
        self.queue, self.stopped = Queue(maxsize=256), Event()
        self.response, self.endpoint, self.error = None, None, None
        self.thread = Thread(target=self._read, name='mcp-sse', daemon=True)

    def _emit(self, value):
        try: self.queue.put_nowait(value)
        except Full: raise MCPError('SSE消息积压过多，请重新连接', transport=True)

    def _frame(self, lines):
        event, data = 'message', []
        for line in lines:
            if line.startswith(':'): continue
            field, _, value = line.partition(':')
            if value.startswith(' '): value = value[1:]
            if field == 'event': event = value
            elif field == 'data': data.append(value)
        if not data: return
        data = '\n'.join(data)
        if event == 'endpoint':
            endpoint = urljoin(self.url, data)
            source, target = urlsplit(self.url), urlsplit(endpoint)
            def origin(parts): return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80)
            if origin(source) != origin(target) or target.username or target.password or target.fragment:
                raise MCPError('SSE服务返回的消息端点必须与服务地址同源', transport=True)
            if self.endpoint and endpoint != self.endpoint: raise MCPError('SSE消息端点发生变化，请重新连接', transport=True)
            if not self.endpoint:
                self.endpoint = endpoint
                self._emit(('endpoint', endpoint))
        elif event == 'message': self._emit(('message', json.loads(data)))

    def _read(self):
        try:
            with self.client.stream('GET', self.url, headers={'Accept': 'text/event-stream', **self.headers}) as response:
                self.response = response
                if response.status_code != 200:
                    message = '认证失败，请检查认证方式和令牌' if response.status_code in (401, 403) else f'SSE连接返回 HTTP {response.status_code}'
                    raise MCPError(message, http_status=response.status_code, transport=True)
                if response.headers.get('content-type', '').split(';')[0].strip().lower() != 'text/event-stream':
                    raise MCPError('服务未返回SSE事件流，请检查传输方式和 /sse 地址', transport=True)
                buffer, lines, size = b'', [], 0
                for chunk in response.iter_bytes():
                    if self.stopped.is_set(): return
                    buffer += chunk
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        size += len(line) + 1
                        if size > MAX_RESPONSE: raise MCPError('SSE单条事件超过2MB', transport=True)
                        text = line.rstrip(b'\r').decode('utf-8')
                        if text: lines.append(text)
                        else:
                            self._frame(lines)
                            lines, size = [], 0
                    if size + len(buffer) > MAX_RESPONSE: raise MCPError('SSE单条事件超过2MB', transport=True)
                raise MCPError('SSE连接已关闭，请重新连接', transport=True)
        except Exception as exc:
            if not self.stopped.is_set():
                self.error = exc if isinstance(exc, MCPError) else MCPError('SSE连接中断、超时或消息格式无效，请重新连接', transport=True)

    def next(self, deadline):
        while True:
            if monotonic() >= deadline: raise MCPError('SSE请求超时，请检查服务或调整超时秒数', transport=True)
            try: return self.queue.get_nowait()
            except Empty:
                if self.error: raise self.error
                remaining = deadline - monotonic()
                if remaining <= 0: raise MCPError('SSE请求超时，请检查服务或调整超时秒数', transport=True)
                try: return self.queue.get(timeout=min(remaining, 0.1))
                except Empty: pass

    def open(self):
        self.thread.start()
        deadline = monotonic() + self.timeout
        kind, _ = self.next(deadline)
        if kind != 'endpoint': raise MCPError('SSE服务未先声明消息端点', transport=True)

    def send(self, payload):
        if self.error: raise self.error
        response = self.client.post(self.endpoint, headers={'Content-Type': 'application/json', **self.headers}, json=payload)
        if response.status_code not in (200, 202, 204):
            message = '认证失败，请检查认证方式和令牌' if response.status_code in (401, 403) else f'SSE消息发送失败（HTTP {response.status_code}），请重新连接'
            raise MCPError(message, http_status=response.status_code, transport=True)

    def request(self, payload, callback, notify=False):
        deadline = monotonic() + self.timeout
        self.send(payload)
        if notify: return {}
        while True:
            kind, message = self.next(deadline)
            if kind == 'message':
                # 已超时的请求不会重试，后续由控制台释放整个连接。
                result = callback(message, payload['id'])
                if result is not None: return result

    def close(self):
        self.stopped.set()
        try:
            if self.response:
                # Windows 的 close 不一定唤醒另一线程中阻塞的 recv，先 shutdown。
                network = self.response.extensions.get('network_stream')
                connection = network.get_extra_info('socket') if network else None
                if connection:
                    try: connection.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
                self.response.close()
        except (httpx.HTTPError, OSError): pass
        if self.thread.is_alive(): self.thread.join(timeout=1)
