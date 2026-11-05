"""OpenAI 兼容大模型客户端封装。"""

from __future__ import annotations

import json
import sys
import traceback
import logging
from time import perf_counter
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from conf.load_config import get_section
from utils.exceptions import LLMResponseError, LLMServiceError
from utils.json_utils import loads_json_object
from utils.log_context import register_secret

logger = logging.getLogger(__name__)


DEFAULT_SYSTEM_PROMPT = "你是一个严谨的文本处理助手，只返回合法 JSON 对象。"
DEFAULT_MAX_OUTPUT_TOKENS = 4096


def _safe_max_tokens(value: Any) -> int:
    """限制输出 token，避免把模型上下文全部占满。"""

    try:
        max_tokens = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_OUTPUT_TOKENS
    if max_tokens <= 0:
        return DEFAULT_MAX_OUTPUT_TOKENS
    return min(max_tokens, DEFAULT_MAX_OUTPUT_TOKENS)


class LLMClient:
    """实例化时读取配置，后续调用不再传模型参数。"""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or get_section("llm_config")

        self.model = str(self.config.get("text_llm_name") or "")
        self.api_key = str(self.config.get("key") or "EMPTY")
        register_secret(self.api_key)
        self.base_url = str(self.config.get("text_llm_url") or "")
        self.timeout = float(self.config.get("timeout_seconds", 120))
        self.max_retries = int(self.config.get("max_retries", 0))
        self.trust_env = bool(self.config.get("trust_env", False))

        self.request_params = {
            "seed": self.config.get("seed"),
            "temperature": self.config.get("temperature"),
            "top_p": self.config.get("top_p"),
            "presence_penalty": self.config.get("presence_penalty"),
            "frequency_penalty": self.config.get("frequency_penalty"),
            "max_tokens": _safe_max_tokens(self.config.get("max_tokens")),
        }
        self.request_params = {
            key: value for key, value in self.request_params.items() if value is not None
        }

        self.response_format = {"type": "json_object"}
        self.extra_body = None
        if not bool(self.config.get("enable_thinking", False)):
            self.extra_body = {"chat_template_kwargs": {"enable_thinking": False}}

        if not self.model:
            raise LLMServiceError("llm_config 缺少 text_llm_name")
        if not self.base_url:
            raise LLMServiceError("llm_config 缺少 text_llm_url")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
            http_client=httpx.Client(trust_env=self.trust_env),
        )

    def build_request(self, prompt: str, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> dict[str, Any]:
        """使用实例化时固定的参数构建模型请求。"""

        request: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "response_format": self.response_format,
            **self.request_params,
        }
        if self.extra_body is not None:
            request["extra_body"] = self.extra_body
        return request

    def chat_json(self, prompt: str, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> dict[str, Any]:
        """调用模型并解析 JSON 对象结果。"""

        request = self.build_request(prompt, system_prompt)
        started = perf_counter()
        logger.info('大模型请求开始：模型=%s，输入字符数=%d，最大输出token=%s，超时秒数=%s，重试上限=%d', self.model, len(prompt), self.request_params.get('max_tokens'), self.timeout, self.max_retries)
        try:
            response = self.client.chat.completions.create(**request)
            content = response.choices[0].message.content or ""
        except Exception as exc:
            logger.error('大模型请求失败：模型=%s，异常类型=%s，耗时=%.3f秒', self.model, type(exc).__name__, perf_counter() - started)
            raise LLMServiceError(
                "大模型调用失败 "
                f"base_url={self.base_url} model={self.model}\n"
                f"{traceback.format_exc()}"
            ) from exc

        try:
            result = loads_json_object(content)
            logger.info('大模型请求完成：模型=%s，返回字符数=%d，JSON字段数=%d，耗时=%.3f秒', self.model, len(content), len(result), perf_counter() - started)
            return result
        except ValueError as exc:
            logger.warning('大模型输出解析失败：模型=%s，返回字符数=%d，耗时=%.3f秒', self.model, len(content), perf_counter() - started)
            raise LLMResponseError(
                f"大模型返回内容不是合法 JSON 对象\n{traceback.format_exc()}"
            ) from exc


def call_llm(
    prompt: str,
    parameters: dict[str, Any] | None = None,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """兼容旧函数入口；parameters 不再覆盖模型参数。"""

    del parameters
    return LLMClient(config=config).chat_json(prompt, system_prompt=system_prompt)
