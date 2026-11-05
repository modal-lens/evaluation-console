"""统一日志配置模块。

API 服务、启动器和 SQL 命令行通过这里创建 logger，保证日志格式、日志级别和输出
位置一致。日志级别来自配置文件，生产环境可以改成 INFO，排查问题时改成 DEBUG。
"""

from __future__ import annotations

import datetime
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from conf.load_config import load_config
from utils.log_context import ContextFormatter, register_config_secrets


_LOGGING_CONFIGURED = False


def _append_log_separator(log_path: str) -> None:
    if not os.path.exists(log_path) or os.path.getsize(log_path) == 0:
        return

    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    separator = f"{'*' * 50}{current_time}{'*' * 56}"
    with open(log_path, "a", encoding="UTF-8") as file_obj:
        file_obj.write(f"\n{separator}\n")


def setup_logging(service: str = '') -> None:
    """初始化全局日志配置。

    `force=True` 用于确保在测试、重复导入或 uvicorn 预先配置日志后，本项目
    仍能应用统一格式。所有日志输出到 stdout，方便 Docker 和进程管理器收集。
    """

    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    config = load_config()
    register_config_secrets(config)
    level_name = str(config.get("log_level") or config.get("log_util_config") or "INFO")
    log_level = getattr(logging, level_name.upper(), logging.INFO)
    formatter = ContextFormatter(
        "%(asctime)s %(levelname)s [进程=%(process)d 线程=%(threadName)s] "
        "[请求=%(request_id)s 任务=%(job_id)s] [%(name)s:%(lineno)d] %(message)s"
    )

    log_dir = str(Path(__file__).resolve().parents[1] / 'logs')
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    today = datetime.datetime.now().strftime("%Y-%m-%d")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)

    suffix = f'_{service}' if service else ''
    log_path = os.path.join(log_dir, f"{today}{suffix}.log")
    error_log_path = os.path.join(log_dir, f"{today}{suffix}_error.log")
    _append_log_separator(log_path)
    _append_log_separator(error_log_path)

    file_handler = RotatingFileHandler(
        log_path,
        mode="a",
        maxBytes=1024 * 1024 * 5,
        backupCount=20,
        encoding="UTF-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    error_handler = RotatingFileHandler(
        error_log_path,
        mode="a",
        maxBytes=1024 * 1024 * 5,
        backupCount=20,
        encoding="UTF-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    logging.basicConfig(
        level=log_level,
        handlers=[console_handler, file_handler, error_handler],
        force=True,
    )
    _LOGGING_CONFIGURED = True
    # Avoid third-party HTTP debug bodies; our own request summaries are sufficient.
    for name in ('httpx', 'httpcore', 'openai'):
        logging.getLogger(name).setLevel(logging.WARNING)
    # HTTP lifecycle logs above the application already include correlation IDs;
    # avoid duplicate access lines with unsanitized query strings.
    logging.getLogger('uvicorn.access').setLevel(logging.WARNING)
    logging.getLogger(__name__).info('日志初始化完成：级别=%s，运行日志=%s，错误日志=%s，单文件上限=5MB，备份数=20', level_name.upper(), log_path, error_log_path)


def get_logger(name: str) -> logging.Logger:
    """获取指定名称的 logger。

    参数通常传入 `__name__`，这样日志中可以看到具体来源模块，便于定位 API、
    任务、数据库或 LLM 调用链路中的问题。
    """

    setup_logging()
    return logging.getLogger(name)
