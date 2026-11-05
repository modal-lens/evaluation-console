"""手动 MCP 工具客户端：Streamable HTTP JSON/SSE，兼容会话式及无状态协议。"""
import base64
import codecs
import json
import logging
import re
from time import monotonic
from uuid import uuid4

import httpx

from utils.log_context import redact, register_secret

logger = logging.getLogger(__name__)
LEGACY_VERSIONS = {'2025-03-26', '2025-06-18', '2025-11-25'}
MODERN_VERSION = '2026-07-28'
MAX_RESPONSE = 2 * 1024 * 1024


class MCPError(RuntimeError):
    def __init__(self, message, *, status=502, http_status=None, rpc_code=None, transport=False):
        super().__init__(message)
        self.status, self.http_status, self.rpc_code, self.transport = status, http_status, rpc_code, transport


def header_value(value):
    value = str(value)
    if value == value.strip() and all(32 <= ord(c) <= 126 for c in value) and not (value.startswith('=?base64?') and value.endswith('?=')):
        return value
    return '=?base64?' + base64.b64encode(value.encode('utf-8')).decode('ascii') + '?='


def parameter_headers(schema):
    """仅允许沿 properties 静态可达的基本类型参数映射到专用 HTTP 头。"""
    found, names = [], set()
    def visit(node, path=(), reachable=True):
        if isinstance(node, list):
            for item in node: visit(item, path, False)
        elif isinstance(node, dict):
            if 'x-mcp-header' in node:
                name = node['x-mcp-header']
                if not path or not reachable or not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", name) or name.lower() in names or node.get('type') not in ('string', 'integer', 'boolean'):
                    raise ValueError('工具参数 HTTP 头注解无效')
                names.add(name.lower())
                found.append((path, name, node['type']))
            for key, value in node.items():
                if key == 'properties' and isinstance(value, dict):
                    for prop, child in value.items(): visit(child, (*path, prop), reachable)
                elif key not in ('default', 'const', 'enum', 'examples') and isinstance(value, (dict, list)):
                    visit(value, path, False)
    visit(schema)
    return found


class MCPClient:
    def __init__(self, url, *, secret='', auth_type='none', timeout=60, protocol='auto', transport_type='streamable_http', event=lambda message: None, transport=None):
        self.url, self.timeout, self.protocol, self.event = url, timeout, protocol, event
        self.version, self.session, self.server_info = '2025-11-25', None, {}
        self.client = httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False, transport=transport)
        self.auth = {'Authorization': 'Bearer ' + secret} if auth_type == 'bearer' else {'X-API-Key': secret} if auth_type == 'api_key' else {}
        register_secret(secret)
        self.sse = None
        self.transport_type = transport_type
        if transport_type == 'sse':
            from tools.mcp_sse import SSEChannel
            self.sse = SSEChannel(self.client, url, self.auth, timeout)

    def _headers(self, method=None, params=None, tool=None):
        headers = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json', **self.auth}
        if self.session: headers['MCP-Session-Id'] = self.session
        if method != 'initialize': headers['MCP-Protocol-Version'] = self.version
        if self.version == MODERN_VERSION:
            headers['Mcp-Method'] = method
            if method == 'tools/call':
                headers['Mcp-Name'] = header_value(params['name'])
                for path, name, kind in parameter_headers(tool.get('inputSchema', {})):
                    value = params.get('arguments', {})
                    for part in path:
                        if not isinstance(value, dict) or part not in value: break
                        value = value[part]
                    else:
                        valid = isinstance(value, str) if kind == 'string' else type(value) is bool if kind == 'boolean' else type(value) is int and abs(value) <= 2**53 - 1
                        if not valid: raise MCPError('工具参数类型与 HTTP 头定义不一致：' + '.'.join(path), status=400)
                        headers['Mcp-Param-' + name] = header_value(str(value).lower() if kind == 'boolean' else value)
        return headers

    def _message(self, value, identifier):
        if not isinstance(value, dict) or value.get('jsonrpc') != '2.0':
            raise MCPError('服务返回的数据不是有效的 MCP JSON-RPC 消息')
        if 'method' in value:
            if 'id' in value:
                if self.version == MODERN_VERSION:
                    raise MCPError('服务在无状态响应流中返回了不支持的反向请求')
                response = {'jsonrpc': '2.0', 'id': value['id']}
                response.update(result={} if value['method'] == 'ping' else None)
                if value['method'] != 'ping':
                    response.pop('result')
                    response['error'] = {'code': -32601, 'message': 'Client capability not supported'}
                if self.sse: self.sse.send(response)
                else: self.client.post(self.url, headers=self._headers(), json=response).raise_for_status()
            elif value['method'] == 'notifications/progress':
                params = value.get('params', {})
                self.event(f"工具执行进度：{params.get('progress', '—')} / {params.get('total', '—')}")
            elif value['method'] == 'notifications/message':
                self.event('收到服务日志通知')
            return None
        if value.get('id') != identifier:
            raise MCPError('服务响应编号与当前请求不一致')
        if isinstance(value.get('error'), dict):
            error = value['error']
            raise MCPError(f"服务返回 RPC 错误（{error.get('code')}）：{redact(error.get('message', '未知错误'))[:500]}", status=400, rpc_code=error.get('code'))
        if not isinstance(value.get('result'), dict): raise MCPError('服务响应缺少 result 对象')
        return value['result']

    def rpc(self, method, params=None, *, notify=False, tool=None):
        params = dict(params or {})
        if not notify and method != 'initialize': params['_meta'] = {'progressToken': uuid4().hex}
        if self.version == MODERN_VERSION:
            params['_meta'] = {'io.modelcontextprotocol/protocolVersion': self.version,
                               'io.modelcontextprotocol/clientInfo': {'name': 'evaluation-console', 'version': '1.0.0'},
                               'io.modelcontextprotocol/clientCapabilities': {}, 'progressToken': uuid4().hex}
        identifier = uuid4().hex
        payload = {'jsonrpc': '2.0', 'method': method, 'params': params}
        if not notify: payload['id'] = identifier
        started = monotonic()
        logger.info('MCP请求开始：方法=%s，协议=%s', method, self.version)
        try:
            if self.sse: return self.sse.request(payload, self._message, notify)
            with self.client.stream('POST', self.url, headers=self._headers(method, params, tool), json=payload) as response:
                if response.status_code >= 300:
                    message = '认证失败，请检查认证方式和令牌' if response.status_code in (401, 403) else '会话已失效，请重新连接' if response.status_code == 404 and self.session else f'服务返回 HTTP {response.status_code}，请检查 MCP 端点和协议版本'
                    raise MCPError(message, http_status=response.status_code, transport=True)
                session = response.headers.get('MCP-Session-Id')
                if session and method == 'initialize':
                    if len(session) > 2048 or not all(33 <= ord(c) <= 126 for c in session): raise MCPError('服务返回的会话标识无效')
                    self.session = session
                    register_secret(session)
                if notify:
                    if response.status_code not in (200, 202, 204): raise MCPError('服务未接受初始化通知')
                    return {}
                content_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
                if content_type not in ('application/json', 'text/event-stream'):
                    raise MCPError('端点未返回 JSON 或 SSE，请确认填写的是 Streamable HTTP 的 MCP 地址')
                buffer, size = '', 0
                decoder = codecs.getincrementaldecoder('utf-8')()
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE: raise MCPError('服务响应超过 2 MB，请缩小查询范围')
                    if monotonic() - started > self.timeout: raise MCPError('MCP请求超过配置的总超时时间', transport=True)
                    buffer += decoder.decode(chunk)
                    if content_type == 'text/event-stream':
                        buffer = buffer.replace('\r\n', '\n')
                        while '\n\n' in buffer:
                            frame, buffer = buffer.split('\n\n', 1)
                            data = '\n'.join(line[5:].lstrip(' ') for line in frame.split('\n') if line.startswith('data:'))
                            if data:
                                result = self._message(json.loads(data), identifier)
                                if result is not None: return result
                if content_type == 'application/json': return self._message(json.loads(buffer + decoder.decode(b'', final=True)), identifier)
                raise MCPError('SSE响应结束，但未收到当前请求的最终结果')
        except MCPError:
            raise
        except httpx.TimeoutException as exc:
            raise MCPError('连接或调用超时，请检查服务状态或调整超时秒数', transport=True) from exc
        except httpx.HTTPError as exc:
            raise MCPError('无法连接 MCP 服务，请检查地址、网络和证书', transport=True) from exc
        except (ValueError, UnicodeError) as exc:
            raise MCPError('服务返回的 JSON 或字符编码无效') from exc
        finally:
            logger.info('MCP请求结束：方法=%s，耗时=%.3f秒', method, monotonic() - started)

    def connect(self):
        if self.sse:
            if self.protocol == MODERN_VERSION: raise MCPError('旧版SSE不支持2026无状态协议，请选择自动协商', status=400)
            self.event('正在建立SSE接收连接…')
            self.sse.open()
        if self.protocol == MODERN_VERSION:
            self.version = MODERN_VERSION
            return self.list_tools()
        try:
            result = self.rpc('initialize', {'protocolVersion': self.version, 'capabilities': {}, 'clientInfo': {'name': 'evaluation-console', 'version': '1.0.0'}})
        except MCPError as exc:
            if self.sse or self.protocol != 'auto' or not (exc.http_status in (400, 404, 405, 406) or exc.rpc_code in (-32600, -32601, -32602)):
                raise
            self.session = None
            self.version = MODERN_VERSION
            self.event('服务未接受会话式初始化，尝试无状态协议')
            return self.list_tools()
        version = result.get('protocolVersion')
        if version not in (LEGACY_VERSIONS | ({'2024-11-05'} if self.sse else set())): raise MCPError('服务协商了当前控制台不支持的协议版本')
        self.version, self.server_info = version, result.get('serverInfo', {})
        self.rpc('notifications/initialized', notify=True)
        return self.list_tools()

    def list_tools(self):
        tools, cursor, seen, names, total_size = [], None, set(), set(), 0
        for _ in range(20):
            result = self.rpc('tools/list', {'cursor': cursor} if cursor else {})
            if not isinstance(result.get('tools'), list): raise MCPError('服务的工具列表格式无效')
            if self.version == MODERN_VERSION:
                self.server_info = result.get('_meta', {}).get('io.modelcontextprotocol/serverInfo', self.server_info)
            for tool in result['tools']:
                if not isinstance(tool, dict) or not isinstance(tool.get('name'), str) or not tool['name'] or not isinstance(tool.get('inputSchema'), dict):
                    raise MCPError('服务返回了缺少名称或参数结构的工具定义')
                if tool['name'] in names: raise MCPError('服务返回了重复工具名称')
                if self.version == MODERN_VERSION:
                    try: parameter_headers(tool['inputSchema'])
                    except ValueError:
                        self.event('已跳过 HTTP 头注解无效的工具：' + tool['name'])
                        continue
                names.add(tool['name']); tools.append(tool)
                total_size += len(json.dumps(tool, ensure_ascii=False).encode('utf-8'))
                if total_size > MAX_RESPONSE: raise MCPError('工具定义总量超过2MB，请缩小服务公开的工具范围')
            if len(tools) > 1000: raise MCPError('服务工具超过1000个，请缩小工具列表')
            cursor = result.get('nextCursor')
            if not cursor: return tools
            if not isinstance(cursor, str) or cursor in seen: raise MCPError('工具列表分页游标无效或重复')
            seen.add(cursor)
        raise MCPError('工具列表分页超过20页')

    def call(self, tool, arguments):
        return self.rpc('tools/call', {'name': tool['name'], 'arguments': arguments}, tool=tool)

    def close(self):
        try:
            if self.sse: self.sse.close()
            elif self.session:
                response = self.client.delete(self.url, headers=self._headers(), timeout=min(self.timeout, 3))
                if response.status_code not in (200, 202, 204, 404, 405):
                    logger.warning('MCP会话关闭时返回HTTP状态码=%d', response.status_code)
        except httpx.HTTPError:
            logger.warning('MCP远端会话关闭未确认，本地连接已释放')
        finally:
            self.session = None
            self.client.close()
