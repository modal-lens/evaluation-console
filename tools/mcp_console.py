"""MCP服务配置持久化与进程内连接/输出管理；认证令牌不落盘。"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import sqlite3
from threading import Lock, RLock
from time import monotonic
from uuid import uuid4

from tools.mcp_client import MCPClient, MCPError
from utils.log_context import log_context, redact, register_secret

logger = logging.getLogger(__name__)


def safe_output(value):
    if isinstance(value, str): return redact(value)
    if isinstance(value, list): return [safe_output(v) for v in value]
    if isinstance(value, dict):
        return {key: '<已脱敏>' if key.lower() in ('api_key', 'password', 'authorization', 'token', 'secret', 'x-api-key') else safe_output(item) for key, item in value.items()}
    return value


@dataclass
class Connection:
    operation: Lock = field(default_factory=Lock)
    status: str = 'disconnected'
    secret: str = ''
    client: MCPClient | None = None
    tools: list = field(default_factory=list)
    events: list = field(default_factory=list)
    result: dict | None = None
    error: str = ''


class MCPConsole:
    def __init__(self, path=None, *, client_factory=MCPClient):
        self.path = Path(path or Path(__file__).resolve().parents[1] / 'logs/mcp_services.sqlite')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock, self.connections = RLock(), {}
        self.client_factory = client_factory
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS mcp_services (id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, auth_type TEXT NOT NULL, timeout INTEGER NOT NULL, protocol TEXT NOT NULL, created_at TEXT NOT NULL)')
            if 'transport_type' not in {row['name'] for row in db.execute('PRAGMA table_info(mcp_services)')}:
                db.execute("ALTER TABLE mcp_services ADD COLUMN transport_type TEXT NOT NULL DEFAULT 'streamable_http'")

    @contextmanager
    def _db(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                with db: yield db
            finally: db.close()

    def _config(self, identifier):
        if not re.fullmatch(r'[a-f0-9]{32}', identifier): raise MCPError('MCP服务不存在', status=404)
        with self._db() as db:
            row = db.execute('SELECT * FROM mcp_services WHERE id=?', (identifier,)).fetchone()
        if row is None: raise MCPError('MCP服务不存在或已删除', status=404)
        return dict(row)

    def _connection(self, identifier):
        with self.lock:
            return self.connections.setdefault(identifier, Connection())

    def event(self, identifier, message, level='info'):
        with self.lock:
            connection = self._connection(identifier)
            connection.events.append({'time': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'level': level, 'message': redact(message)[:2000]})
            connection.events[:] = connection.events[-200:]
        logger.log(logging.ERROR if level == 'error' else logging.INFO, 'MCP服务状态更新：服务ID=%s，级别=%s', identifier, level)

    def get(self, identifier, *, detailed=True):
        config, connection = self._config(identifier), self._connection(identifier)
        with self.lock:
            value = {**config, 'status': connection.status, 'tools_count': len(connection.tools), 'has_secret': bool(connection.secret),
                     'needs_credentials': config['auth_type'] != 'none' and not connection.secret,
                     'last_error': connection.error, 'protocol_version': connection.client.version if connection.client else None}
            if detailed:
                value.update(tools=connection.tools.copy(), output=connection.events.copy(), result=connection.result,
                             server_info=connection.client.server_info if connection.client else {})
            return value

    def list(self):
        with self.lock:
            with self._db() as db:
                identifiers = [row['id'] for row in db.execute('SELECT id FROM mcp_services ORDER BY created_at,id')]
            return [self.get(identifier, detailed=False) for identifier in identifiers]

    @contextmanager
    def operation(self, identifier):
        config, connection = self._config(identifier), self._connection(identifier)
        if not connection.operation.acquire(blocking=False): raise MCPError('该服务正在连接或调用，请等待当前操作完成', status=409)
        try:
            with log_context(job='mcp:' + identifier):
                yield config, connection
        finally: connection.operation.release()

    def save(self, config, secret='', identifier=None):
        config = {**config, 'transport_type': config.get('transport_type', 'streamable_http')}
        if config['transport_type'] == 'sse' and config['protocol'] == '2026-07-28':
            raise MCPError('旧版SSE请使用自动协商或2025会话式协议', status=400)
        if identifier is None:
            with self.lock:
                if len(self.list()) >= 50: raise MCPError('最多保存50个MCP服务，请先删除不再使用的服务', status=400)
                identifier = uuid4().hex
                if config['auth_type'] != 'none' and not secret: raise MCPError('请填写所选认证方式需要的令牌', status=400)
                with self._db() as db:
                    db.execute('INSERT INTO mcp_services (id,name,url,auth_type,timeout,protocol,created_at,transport_type) VALUES (?,?,?,?,?,?,?,?)', (identifier, config['name'], config['url'], config['auth_type'], config['timeout'], config['protocol'], datetime.now(timezone.utc).isoformat(), config['transport_type']))
                self._connection(identifier).secret = secret if config['auth_type'] != 'none' else ''
        else:
            with self.operation(identifier) as (old, connection):
                effective = secret or (connection.secret if old['auth_type'] == config['auth_type'] else '')
                if config['auth_type'] != 'none' and not effective: raise MCPError('请重新填写认证令牌', status=400)
                self._release(connection)
                with self._db() as db:
                    db.execute('UPDATE mcp_services SET name=?,url=?,auth_type=?,timeout=?,protocol=?,transport_type=? WHERE id=?', (config['name'], config['url'], config['auth_type'], config['timeout'], config['protocol'], config['transport_type'], identifier))
                connection.secret = effective if config['auth_type'] != 'none' else ''
        register_secret(self._connection(identifier).secret)
        self.event(identifier, '服务配置已保存，请点击“连接”建立连接')
        return self.get(identifier)

    def _release(self, connection):
        try:
            if connection.client: connection.client.close()
        finally:
            with self.lock:
                connection.client = None
                connection.status = 'disconnected'
                connection.tools = []

    def connect(self, identifier):
        with self.operation(identifier) as (config, connection):
            if connection.status == 'connected': return self.get(identifier)
            if config['auth_type'] != 'none' and not connection.secret: raise MCPError('后端重启后认证令牌已清除，请编辑服务并重新填写', status=409)
            self._release(connection)
            with self.lock:
                connection.status, connection.error = 'connecting', ''
            self.event(identifier, '正在连接服务…')
            try:
                client = self.client_factory(config['url'], auth_type=config['auth_type'], secret=connection.secret, timeout=config['timeout'], protocol=config['protocol'], transport_type=config['transport_type'], event=lambda message: self.event(identifier, message))
                connection.client = client
                tools = client.connect()
                with self.lock:
                    connection.tools, connection.status = tools, 'connected'
                self.event(identifier, '服务连接成功')
                self.event(identifier, f'已发现 {len(tools)} 个工具')
            except MCPError as exc:
                self._release(connection)
                connection.status, connection.error = 'error', redact(str(exc))
                self.event(identifier, connection.error, 'error')
                raise
            except Exception as exc:
                self._release(connection)
                connection.status, connection.error = 'error', '连接失败，请检查MCP服务及后端日志'
                self.event(identifier, connection.error, 'error')
                logger.exception('MCP连接异常：异常类型=%s', type(exc).__name__)
                raise MCPError(connection.error) from exc
        return self.get(identifier)

    def refresh(self, identifier):
        with self.operation(identifier) as (_, connection):
            if connection.status != 'connected' or not connection.client: raise MCPError('请先连接服务', status=409)
            try:
                tools = connection.client.list_tools()
                with self.lock: connection.tools, connection.error = tools, ''
                self.event(identifier, f'工具列表已刷新，共 {len(tools)} 个工具')
            except MCPError as exc:
                self._failed_operation(identifier, connection, exc)
                raise
        return self.get(identifier)

    def _failed_operation(self, identifier, connection, error):
        if error.transport:
            self._release(connection)
            connection.status = 'error'
        connection.error = redact(str(error))
        self.event(identifier, connection.error, 'error')

    def call(self, identifier, name, arguments):
        with self.operation(identifier) as (_, connection):
            if connection.status != 'connected' or not connection.client: raise MCPError('请先连接服务', status=409)
            tool = next((tool for tool in connection.tools if tool['name'] == name), None)
            if tool is None: raise MCPError('工具不存在，请刷新工具列表后重试', status=400)
            self.event(identifier, '正在调用工具：' + name)
            started = monotonic()
            try:
                result = connection.client.call(tool, arguments)
                with self.lock:
                    connection.result = safe_output(result)
                    connection.error = ''
                failed = result.get('isError') is True
                message = '工具执行失败，详情见输出结果' if failed else '服务要求额外交互，详情见输出结果' if result.get('resultType') == 'input_required' else '调用完成'
                self.event(identifier, f'{message}（耗时 {monotonic() - started:.2f} 秒）', 'error' if failed else 'info')
            except MCPError as exc:
                self._failed_operation(identifier, connection, exc)
                raise
            except Exception as exc:
                error = MCPError('工具调用异常，请查看后端日志；该调用不会自动重试')
                self._failed_operation(identifier, connection, error)
                logger.exception('MCP工具调用异常：工具=%s，异常类型=%s', name, type(exc).__name__)
                raise error from exc
        return self.get(identifier)

    def disconnect(self, identifier):
        with self.operation(identifier) as (_, connection):
            self._release(connection)
            connection.error = ''
            self.event(identifier, '服务已断开，本地连接已释放')
        return self.get(identifier)

    def clear(self, identifier):
        with self.operation(identifier) as (_, connection):
            with self.lock:
                connection.events, connection.result, connection.error = [], None, ''
        return self.get(identifier)

    def delete(self, identifier):
        with self.operation(identifier) as (_, connection):
            self._release(connection)
            with self._db() as db: db.execute('DELETE FROM mcp_services WHERE id=?', (identifier,))
            with self.lock: self.connections.pop(identifier, None)
        logger.info('MCP服务已删除：服务ID=%s', identifier)
        return {'id': identifier, 'deleted': True}

    def close(self):
        with self.lock: connections = list(self.connections.values())
        for connection in connections:
            if connection.operation.acquire(blocking=False):
                try: self._release(connection)
                finally: connection.operation.release()
