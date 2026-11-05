"""请求/任务上下文及统一日志脱敏，不负责初始化日志输出。"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy
import logging
import re
from threading import RLock

request_id = ContextVar('request_id', default='-')
job_id = ContextVar('job_id', default='-')
_secrets = set()
_secret_lock = RLock()


@contextmanager
def log_context(*, request=None, job=None):
    tokens = []
    try:
        for variable, value in ((request_id, request), (job_id, job)):
            if value is not None:
                tokens.append((variable, variable.set(str(value))))
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def register_secret(value):
    if isinstance(value, str) and value:
        with _secret_lock:
            _secrets.add(value)


def register_config_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {'key', 'api_key', 'password', 'token', 'secret'}:
                register_secret(item)
            else:
                register_config_secrets(item)


def redact(value):
    text = str(value)
    with _secret_lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for secret in secrets:
        text = text.replace(secret, '<已脱敏>')
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '<已脱敏>', text)
    text = re.sub(r'(?i)(bearer\s+)[^\s\"\']+', r'\1<已脱敏>', text)
    text = re.sub(r'(?i)((?:api[_-]?key|x-api-key|password|token|secret)[\"\']?\s*[:=]\s*[\"\']?)[^\s,;\"\'}]+', r'\1<已脱敏>', text)
    text = re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1<已脱敏>@', text)
    # Third-party HTTP loggers may include the full URL even when our middleware
    # only logs paths. Omit query values consistently for every handler.
    text = re.sub(r'(https?://[^\s?\"\']+)\?[^\s\"\']+', r'\1?<参数已省略>', text)
    return text


class ContextFormatter(logging.Formatter):
    def format(self, record):
        # Do not mutate records shared with other handlers (including test capture).
        record = copy(record)
        record.request_id = getattr(record, 'request_id', request_id.get())
        record.job_id = getattr(record, 'job_id', job_id.get())
        return redact(super().format(record))


def metrics_summary(value):
    """仅记录指标数字，不记录原文、标注或预测对象内容。"""
    if not isinstance(value, dict):
        return {}
    return {key: item if isinstance(item, (int, float, bool)) else metrics_summary(item)
            for key, item in value.items() if isinstance(item, (int, float, bool, dict))}
