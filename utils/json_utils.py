"""JSON 解析辅助工具。

LLM 应返回纯 JSON；为兼容部分模型，只额外接受完整 Markdown JSON 代码块。
代码不会从自然语言中截取花括号内容，避免把不受控说明误判为合法响应。
"""

from __future__ import annotations

import json
import re
import traceback
from typing import Any


# 匹配 ```json ... ``` 或普通 ``` ... ``` 代码块，优先从代码块中提取 JSON。
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def loads_json_object(text: str) -> dict[str, Any]:
    """从模型原始文本中解析 JSON 对象。

    解析顺序:
        1. 尝试把完整文本作为 JSON 解析。
        2. 当完整响应只有一个 Markdown 代码块时解析代码块内容。

    返回:
        解析成功的 JSON 对象，必须是字典。

    异常:
        ValueError: 所有候选内容都无法解析为 JSON 对象时抛出。
    """

    stripped = text.strip()
    candidates = [stripped]
    block_match = JSON_BLOCK_RE.fullmatch(stripped)
    if block_match:
        candidates.append(block_match.group(1).strip())

    errors: list[str] = []
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(f"{exc}\n{traceback.format_exc()}")
            continue
        if isinstance(value, dict):
            return value
    raise ValueError(f"LLM response does not contain a JSON object: {'; '.join(errors[:3])}")


def ensure_list(value: Any) -> list[Any]:
    """把任意值规整为列表。

    LLM 或上游系统偶尔会把单个标签返回成对象或字符串。该函数保证后续逻辑
    总能按列表处理，同时不破坏本来就是列表的数据。
    """

    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
