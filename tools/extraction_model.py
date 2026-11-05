# Model adapter for metric extraction; no standalone business routes.
from __future__ import annotations
from typing import Any
import httpx
from openai import OpenAI
from conf.load_config import get_section
from utils.database import postgres_client
from utils.log_context import register_secret

def _as_int(value: Any, default: int = 0) -> int:
    """把模型参数中的数字字段安全转成 int。"""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _as_float(value: Any, default: float = 0.0) -> float:
    """把模型参数中的数字字段安全转成 float。"""

    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _enabled_value(params: dict[str, Any], enabled_key: str, value_key: str, default: Any) -> Any:
    """根据 ``xxxEnabled`` 判断是否使用模板中的模型参数。"""

    if params.get(enabled_key, True):
        return params.get(value_key, default)
    return default

def _resolve_model_config(model_params: dict[str, Any]) -> tuple[str | None, str | None, str]:
    """根据 ``modelId`` 查询模型服务地址，并用 ``modelName`` 作为模型名兜底。"""

    config = get_section("llm_config")
    model_config: dict[str, Any] = {}
    model_id = model_params.get("modelId")
    if model_id is not None:
        model_config = postgres_client.llm_model_config_query(config_id=model_id) or {}

    base_url = model_config.get("base_url") or config.get("text_llm_url")
    model = model_config.get("model_code") or model_params.get("modelName") or config.get("text_llm_name")
    api_key = model_config.get("api_key") or config.get("key") or "EMPTY"
    register_secret(api_key)
    return base_url, model, api_key

def extract_entities_with_model(*, input_text: str, model_params: dict[str, Any],
                                sampling_params: dict[str, Any] | None = None) -> dict[str, Any]:
    """按 model_params 调用；sampling_params 仅供内部评测显式覆盖，HTTP 原调用不传。"""

    from tools.entity_extraction import extract_text

    config = get_section("llm_config")
    sampling = sampling_params or {}
    base_url, model, api_key = _resolve_model_config(model_params)
    openai_client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=httpx.Client(trust_env=False),
    )
    max_tokens = _enabled_value(model_params, "maxTokensEnabled", "maxTokens", config.get("max_tokens", 8192))

    return extract_text(
        input_text,
        openai_client=openai_client,
        entity_attribute_schema={},
        entity_type=[],
        relation_types=None,
        invalid_relation_evidence_keywords=None,
        invalid_established_sources=None,
        model=model,
        temperature=sampling.get('temperature', _as_float(
            _enabled_value(model_params, "temperatureEnabled", "temperature", config.get("temperature", 0.1)),
            0.1,
        )),
        top_p=sampling.get('top_p', _as_float(
            _enabled_value(model_params, "topPEnabled", "topP", config.get("top_p", 0.3)),
            0.3,
        )),
        max_tokens=min(_as_int(max_tokens, 8192), 8192),
        frequency_penalty=sampling.get('frequency_penalty', _as_float(
            _enabled_value(model_params, "frequencyPenaltyEnabled", "frequencyPenalty",
                           config.get("frequency_penalty", 0.0)),
            0.0,
        )),
        presence_penalty=sampling.get('presence_penalty', _as_float(
            _enabled_value(model_params, "presencePenaltyEnabled", "presencePenalty",
                           config.get("presence_penalty", 0.0)),
            0.0,
        )),
        request_extra_body={"chat_template_kwargs": {"enable_thinking": False},
                            **({'seed': sampling['seed']} if 'seed' in sampling else {})},
        response_format={"type": "json_object"},
        max_len=_as_int(model_params.get("blockSize"), 1000),
        max_workers=sampling.get('max_workers', _as_int(config.get("max_workers"), 8)),
        system_prompt_override=model_params.get("generatedPrompt", ""),
    )
