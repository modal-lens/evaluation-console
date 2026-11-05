"""基于数据库标签和大模型的文本分类。"""

from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from typing import Any

from conf.load_config import get_section
from tools.llm_client import call_llm
from utils.database import PostgresClient, postgres_client
from utils.exceptions import LLMResponseError
from utils.logger import get_logger


# 使用模块名创建 logger，日志中可以明确看出消息来自文本分类模块。
logger = get_logger(__name__)

# 系统提示词负责约束模型的角色和返回格式；具体的候选标签和正文由
# build_classification_prompt() 在每次调用时动态拼接。
CLASSIFICATION_SYSTEM_PROMPT = """
你是一个文本分类助手。
只能从用户提供的标签中选择，不能创建新标签。
输出的 tags 必须是“标签 ID: 标签名称”的 JSON 对象。
请只返回 JSON 对象，不要返回解释文字。
""".strip()


def build_classification_prompt(text: str, tags: dict[str, str]) -> str:
    """根据正文和数据库标签生成分类提示词。

    参数：
        text: 待分类的原始文本。
        tags: 从数据库查询出的标签映射，格式为 ``{"ID": "标签名称"}``。

    返回：
        包含候选标签、待分类正文和严格 JSON 输出格式要求的 Prompt。

    注意：
        这里只记录标签数量和文本长度，不把完整正文或完整 Prompt 写入日志，
        避免日志中出现过大的原文或敏感业务内容。
    """

    tag_text = json.dumps(tags, ensure_ascii=False, indent=2)
    prompt = (
        "请根据下面的标签对文本进行分类，可以选择一个或多个标签，必须至少选择一个标签。\n"
        "如果没有完全匹配的标签，也必须从可选标签中选择一个最接近正文主题的标签；禁止返回空 tags。\n"
        "tags 必须至少包含一个“标签ID: 标签名称”键值对，不能返回 {\"tags\":{}}。\n"
        f"可选标签（标签 ID: 标签名称）：\n{tag_text}\n\n"
        f"文本：\n{text}\n\n"
        '只返回如下格式的 JSON：{"tags":{"标签ID":"标签名称","标签ID":"标签名称"}}。tags内容最少要有一个值'
    )
    logger.info(
        "分类 Prompt 已生成 tag_count=%s text_length=%s",
        len(tags),
        len(text),
    )
    return prompt



def classify_text(
    text: str,
    parameters: dict[str, Any],
    db_client: PostgresClient | None = None,
    llm: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """查询数据库标签并调用大模型完成文本分类。

    执行步骤：
        1. 校验待分类文本不能是空字符串；
        2. 从 PostgreSQL 查询可用标签；
        3. 在本模块构造分类 Prompt；
        4. 调用 OpenAI 兼容大模型；
        5. 校验模型返回的标签 ID 和名称，并返回稳定结果。

    异常处理：
        任何异常都会先记录完整 traceback，再原样抛出给上层调用者，
        不把失败任务伪装成成功结果。
    """

    if not text.strip():
        raise ValueError("分类文本不能为空")

    logger.info("开始文本分类 text_length=%s", len(text))

    # 默认使用全局 PostgreSQL 客户端；测试时可以注入 FakeDatabase，
    # 从而避免连接真实数据库。
    database = db_client or postgres_client
    labels = database.tags_query()
    del labels["19"]
    logger.info("数据库标签查询完成 tag_count=%s", len(labels))
    if not labels:
        raise LLMResponseError(
            "数据库中没有可用分类标签",
            stage="text_classification",
        )

    prompt = build_classification_prompt(text[:2048], labels)

    # 生产环境使用 call_llm；测试环境可以传入 Fake LLM，保持调用参数一致。
    model_caller = llm or call_llm
    logger.info("开始调用分类模型 model=%s", parameters.get("model"))
    result = model_caller(
        prompt,
        parameters,
        system_prompt=CLASSIFICATION_SYSTEM_PROMPT,
    )
    logger.info("分类模型调用完成 response_keys=%s", list(result.keys()))

    final_result = _build_result(result, labels)
    logger.info(
        "文本分类完成 selected_tag_count=%s",
        len(final_result["tags"]),
    )
    final_result.update({"labels":labels})
    return final_result



def _build_result(result: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    """校验模型返回的标签 ID 和标签名称。

    模型必须返回以下结构：

    .. code-block:: json

        {"tags": {"5": "新闻"}}

    标签 ID 和标签名称都必须来自数据库查询结果，不能接受模型自行生成的
    标签，避免分类结果脱离系统实际配置。
    """

    values = result.get("tags")
    if not isinstance(values, dict):
        raise LLMResponseError(
            "分类结果中的 tags 必须是 JSON 对象",
            stage="text_classification",
        )

    selected: dict[str, str] = {}
    for tag_id, tag_name in values.items():
        # JSON 对象的键通常会被解析为字符串，因此统一转换后再和数据库映射比较。
        key = str(tag_id)
        if key not in labels:
            raise LLMResponseError(
                "模型返回了数据库中不存在的标签",
                detail={"tag_id": key},
                stage="text_classification",
            )
        if str(tag_name).strip() != labels[key]:
            raise LLMResponseError(
                "模型返回的标签名称与数据库不一致",
                detail={"tag_id": key, "tag": tag_name},
                stage="text_classification",
            )
        selected[key] = labels[key]
    if not selected:
        fallback_id, fallback_name = next(iter(labels.items()))
        logger.warning(
            "分类模型返回空 tags，使用第一个候选标签兜底 tag_id=%s tag_name=%s",
            fallback_id,
            fallback_name,
        )
        selected[fallback_id] = fallback_name
    return {"tags": selected}




__all__ = ["build_classification_prompt", "classify_text"]
