"""MCP控制台服务管理、工具发现与手动调用接口。"""
import atexit
from functools import lru_cache
import json
import os
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from tools.mcp_console import MCPConsole
from tools.mcp_client import MCPError

router = APIRouter(prefix='/api/v1/mcp-console', tags=['mcp-console'])


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2048)
    auth_type: Literal['none', 'bearer', 'api_key'] = 'none'
    secret: SecretStr = SecretStr('')
    timeout: int = Field(default=60, ge=5, le=300)
    protocol: Literal['auto', '2025-11-25', '2026-07-28'] = 'auto'
    transport_type: Literal['streamable_http', 'sse'] = 'streamable_http'

    @field_validator('url')
    @classmethod
    def validate_url(cls, value):
        parts = urlsplit(value)
        if parts.port is not None and not 1 <= parts.port <= 65535: raise ValueError('MCP服务端口必须在1～65535之间')
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('请填写HTTP(S) MCP端点，不能包含账号、密码、查询参数或片段；认证请使用令牌字段')
        return value

    @field_validator('secret')
    @classmethod
    def validate_secret(cls, value):
        if len(value.get_secret_value()) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in value.get_secret_value()):
            raise ValueError('认证令牌必须是可打印ASCII字符，且不能超过4096字符')
        return value


class ToolCall(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=512)
    arguments: dict = Field(default_factory=dict)

    @field_validator('arguments')
    @classmethod
    def validate_arguments(cls, value):
        if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > 128 * 1024: raise ValueError('工具参数不能超过128KB')
        return value


@lru_cache(maxsize=1)
def get_mcp_console():
    manager = MCPConsole(os.environ.get('MCP_CONSOLE_DB'))
    atexit.register(manager.close)
    return manager


def perform(function, *args):
    try: return {'success': True, 'data': function(*args)}
    except MCPError as exc: raise HTTPException(exc.status, str(exc)) from exc


@router.get('/services')
def services(manager=Depends(get_mcp_console)):
    return perform(manager.list)


@router.post('/services', status_code=201)
def create_service(body: ServerConfig, manager=Depends(get_mcp_console)):
    return perform(manager.save, body.model_dump(exclude={'secret'}), body.secret.get_secret_value())


@router.get('/services/{identifier}')
def service(identifier: str, manager=Depends(get_mcp_console)):
    return perform(manager.get, identifier)


@router.post('/services/{identifier}/edit')
def edit_service(identifier: str, body: ServerConfig, manager=Depends(get_mcp_console)):
    return perform(manager.save, body.model_dump(exclude={'secret'}), body.secret.get_secret_value(), identifier)


@router.post('/services/{identifier}/connect')
def connect_service(identifier: str, manager=Depends(get_mcp_console)):
    return perform(manager.connect, identifier)


@router.post('/services/{identifier}/disconnect')
def disconnect_service(identifier: str, manager=Depends(get_mcp_console)):
    return perform(manager.disconnect, identifier)


@router.post('/services/{identifier}/tools/refresh')
def refresh_tools(identifier: str, manager=Depends(get_mcp_console)):
    return perform(manager.refresh, identifier)


@router.post('/services/{identifier}/tools/call')
def call_tool(identifier: str, body: ToolCall, manager=Depends(get_mcp_console)):
    return perform(manager.call, identifier, body.name, body.arguments)


@router.post('/services/{identifier}/output/clear')
def clear_output(identifier: str, manager=Depends(get_mcp_console)):
    return perform(manager.clear, identifier)


@router.post('/services/{identifier}/delete')
def delete_service(identifier: str, manager=Depends(get_mcp_console)):
    return perform(manager.delete, identifier)
