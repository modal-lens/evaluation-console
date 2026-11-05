"""项目配置加载模块。

本模块负责从 `conf/config.yml` 读取评测服务、分类标签数据库和 LLM 配置。
项目约定所有可变参数都进入配置文件，
业务代码不直接写死连接地址、端口、表名或模型参数。
"""

from __future__ import annotations

import os
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "conf" / "config.yml"
ENV_PREFIX = "NANJING__"

ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "POSTGRES_HOST": ("PostgreSQL", "url"),
    "POSTGRES_PORT": ("PostgreSQL", "port"),
    "POSTGRES_USER": ("PostgreSQL", "username"),
    "POSTGRES_PASSWORD": ("PostgreSQL", "password"),
    "POSTGRES_DB": ("PostgreSQL", "db_name"),
    "LLM_API_KEY": ("llm_config", "key"),
    "LLM_BASE_URL": ("llm_config", "text_llm_url"),
    "LLM_MODEL": ("llm_config", "text_llm_name"),
}


class ConfigError(RuntimeError):
    """配置文件缺失、格式错误或根节点类型不正确时抛出的异常。"""


def _parse_env_value(value: str) -> Any:
    """使用 YAML 标量规则解析环境变量值。"""

    try:
        parsed = yaml.safe_load(value)
    except yaml.YAMLError as exc:
        logging.getLogger(__name__).warning('环境变量配置解析失败，保留为字符串：异常类型=%s', type(exc).__name__)
        return value
    return value if parsed is None and value.strip() else parsed


def _set_nested(config: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    """把环境变量值写入嵌套配置。"""

    current = config
    for key in path[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[path[-1]] = value


def _apply_environment(config: dict[str, Any]) -> dict[str, Any]:
    """应用通用和常用别名环境变量覆盖。"""

    for name, raw_value in os.environ.items():
        if name.startswith(ENV_PREFIX):
            parts = [part for part in name[len(ENV_PREFIX):].split("__") if part]
            path = tuple(
                "PostgreSQL" if index == 0 and part.upper() == "POSTGRESQL" else part.lower()
                for index, part in enumerate(parts)
            )
            if path:
                _set_nested(config, path, _parse_env_value(raw_value))

    for name, path in ENV_ALIASES.items():
        if name in os.environ:
            _set_nested(config, path, _parse_env_value(os.environ[name]))
    return config


@lru_cache(maxsize=1)
def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """读取 YAML 配置并缓存。

    参数:
        config_path: 可选的配置文件路径。为空时读取 `conf/config.yml`。

    返回:
        解析后的配置字典。缓存后同一进程内重复调用不会反复读取磁盘。

    异常:
        ConfigError: 配置文件不存在，或 YAML 根节点不是字典。
    """

    configured_path = os.getenv("APP_CONFIG_PATH")
    path = Path(config_path or configured_path or DEFAULT_CONFIG_PATH)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as file_obj:
        data = yaml.safe_load(file_obj) or {}

    if not isinstance(data, dict):
        raise ConfigError(f"Config root must be a mapping: {path}")
    return _apply_environment(data)


def get_section(name: str) -> dict[str, Any]:
    """获取指定配置段。

    如果配置段本身是字典，直接返回；如果是字符串、数字等标量值，则包装成
    `{"value": value}`，这样调用方始终能按字典方式读取，减少类型分支。
    """

    value = load_config().get(name, {})
    if isinstance(value, dict):
        return value
    return {"value": value}


def clear_config_cache() -> None:
    """清除配置缓存，供测试或显式热加载使用。"""

    load_config.cache_clear()


def get_max_workers() -> int:
    """所有评测共用的默认预测并发数；沿用配置缓存和环境变量覆盖。"""
    value = load_config().get('max_workers', 4)
    if type(value) is not int or not 1 <= value <= 8:
        raise ConfigError('conf/config.yml 中 max_workers 必须是 1～8 的整数')
    return value
