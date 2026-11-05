"""Additive SQL evaluation API; launches a local Python subprocess, not an LLM."""
from functools import lru_cache
import json
import os
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from tools.sql_evaluation.jobs import DATA, SQLJobManager
from conf.load_config import get_max_workers

router = APIRouter(prefix='/api/v1/sql-evaluation', tags=['sql-evaluation'])
DEFAULT_API_KEY = 'sk-o5y9bqE-RIG1myT_KXcv8WTpzTYG2BXkgrFO1ovzhw4'


class SQLCaseInput(BaseModel):
    database: str
    question: str = Field(min_length=1)
    schema_context: str = Field(alias='schema', min_length=1)


class SQLCase(BaseModel):
    model_config = {'extra': 'allow'}
    id: str = Field(min_length=1)
    database: str
    category: str = Field(min_length=1)
    input: SQLCaseInput
    standard_sql: str = Field(min_length=1)
    expected_results: dict[str, list[list[str | int | float | bool | None]]]

    @model_validator(mode='after')
    def validate_case(self):
        if self.database not in ('Oracle', 'PostgreSQL', 'SQL Server', 'MySQL'):
            raise ValueError('不支持的数据库类型')
        if self.input.database != self.database:
            raise ValueError('input.database 必须与 database 一致')
        if set(self.expected_results) != {'0', '1', '2'}:
            raise ValueError('expected_results 必须包含 0、1、2 三组预期结果')
        return self


class SQLRunRequest(BaseModel):
    url: str = 'http://192.168.31.60:7865'
    flow_id: str = '5d87aa5b-aeaf-4860-a7b6-9ccf93e2e04a'
    api_key: SecretStr = SecretStr('')
    database: str = ''
    limit: int = Field(default=100, ge=1, le=100)
    workers: int = Field(default_factory=get_max_workers, ge=1, le=8)
    timeout: int = Field(default=120, ge=5, le=300)
    cases: list[SQLCase] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def validate_cases(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError('测试用例 ID 不能重复')
        if self.database and not any(case.database == self.database for case in self.cases):
            raise ValueError('上传数据中没有所选数据库的测试用例')
        if len(json.dumps([case.model_dump() for case in self.cases], ensure_ascii=False).encode('utf-8')) > 5 * 1024 * 1024:
            raise ValueError('测试数据不能超过 5 MB')
        return self

    @field_validator('url')
    @classmethod
    def validate_url(cls, value):
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/'):
            raise ValueError('请填写不含路径及账号密码的HTTP(S)工作流服务地址。')
        return value.rstrip('/')

    @field_validator('flow_id')
    @classmethod
    def validate_flow_id(cls, value):
        return str(UUID(value))

    @field_validator('database')
    @classmethod
    def validate_database(cls, value):
        if value not in ('', 'Oracle', 'PostgreSQL', 'SQL Server', 'MySQL'):
            raise ValueError('不支持的数据库类型')
        return value


@lru_cache(maxsize=1)
def get_sql_manager():
    return SQLJobManager()


@router.get('/dataset')
def dataset():
    cases = json.loads((DATA / 'test_cases_100.json').read_text(encoding='utf-8'))
    return {'success': True, 'data': {'count': len(cases), 'cases': cases}}


@router.get('/config')
def evaluation_config():
    return {'success': True, 'data': {'max_workers': get_max_workers()}}


@router.get('/dataset/{format}')
def download_dataset(format: str):
    if format not in ('json', 'csv'):
        raise HTTPException(404, '文件不存在')
    path = DATA / ('test_cases_100.' + format)
    return FileResponse(path, filename=path.name)


@router.post('/jobs', status_code=202)
def start_job(body: SQLRunRequest, manager=Depends(get_sql_manager)):
    key = body.api_key.get_secret_value().strip() or os.environ.get('LANGFLOW_API_KEY', '').strip() or DEFAULT_API_KEY
    if not key:
        raise HTTPException(400, '请输入工作流API Key，或在本机配置LANGFLOW_API_KEY环境变量。')
    try:
        options = body.model_dump(exclude={'api_key'}, by_alias=True)
        return {'success': True, 'data': manager.start(options, key)}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/jobs')
def list_jobs(manager=Depends(get_sql_manager)):
    return {'success': True, 'data': manager.recent()}


@router.get('/jobs/{job_id}')
def job(job_id: str, manager=Depends(get_sql_manager)):
    try:
        return {'success': True, 'data': manager.get(job_id)}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/jobs/{job_id}/cancel')
def cancel_job(job_id: str, manager=Depends(get_sql_manager)):
    try:
        return {'success': True, 'data': manager.cancel(job_id)}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/jobs/{job_id}/files/{filename}')
def job_file(job_id: str, filename: str, manager=Depends(get_sql_manager)):
    try:
        return FileResponse(manager.artifact(job_id, filename), filename=filename)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
