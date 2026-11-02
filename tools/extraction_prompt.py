# Prompt construction for metric extraction; no HTTP or database lookup.
from __future__ import annotations
import json
from typing import Any
from tools.prompts import system_prompt

def build_prompt(
    *,
    entity_categories: list[str],
    entity_attributes: dict[str, Any],
    relation_types: list[str] | None = None,
    text: str = "",
) -> str:
    """把已解析好的实体名称、属性名称和关系类型拼成提示词字符串。

    这里不访问数据库，只接收已经转换好的中文名称，方便单元测试或其它模块复用。
    """

    # 关系类型为空时，不把空数组直接交给模型，而是明确告诉模型可以自由生成关系名。
    relation_rule = (
        json.dumps(relation_types, ensure_ascii=False)
        if relation_types
        else "不指定关系类型；请根据原文中的明确语义生成简短、准确的中文关系名称"
    )

    # system_prompt 中保留了三个占位符，这里统一注入实际抽取配置。
    system_content = system_prompt.replace(
        "{{ENTITY_TYPE}}", json.dumps(entity_categories, ensure_ascii=False)
    ).replace(
        "{{ENTITY_ATTRIBUTE_SCHEMA}}", json.dumps(entity_attributes, ensure_ascii=False)
    ).replace(
        "{{RELATION_TYPES}}", relation_rule
    )

    # 当前接口只返回系统提示词字符串，不附加模型配置和用户正文。
    return system_content.strip()
