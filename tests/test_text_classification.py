from __future__ import annotations

import logging
from typing import Any

import pytest

import tools.text_classification as text_classification
from tools.text_classification import classify_text
from utils.exceptions import LLMResponseError


class FakeDatabase:
    def tags_query(self) -> dict[str, str]:
        return {"5": "新闻", "6": "公告", "19": "保留标签"}


def test_classify_text_uses_database_tags_and_prompt() -> None:
    captured: dict[str, Any] = {}

    def fake_llm(prompt: str, parameters: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        captured.update(
            {"prompt": prompt, "parameters": parameters, "system_prompt": kwargs["system_prompt"]}
        )
        return {"tags": {"6": "公告"}}

    result = classify_text(
        "政府发布新的公告。",
        {"model": "fake-model"},
        db_client=FakeDatabase(),  # type: ignore[arg-type]
        llm=fake_llm,
    )

    assert result == {"tags": {"6": "公告"}, "labels": {"5": "新闻", "6": "公告"}}
    assert '"5": "新闻"' in captured["prompt"]
    assert '"6": "公告"' in captured["prompt"]
    assert '"19"' not in captured["prompt"]
    assert "政府发布新的公告" in captured["prompt"]
    assert captured["parameters"] == {"model": "fake-model"}


def test_classify_text_rejects_unknown_tag() -> None:
    def fake_llm(prompt: str, parameters: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return {"tags": {"99": "未知"}}

    with pytest.raises(LLMResponseError):
        classify_text(
            "待分类文本",
            {"model": "fake-model"},
            db_client=FakeDatabase(),  # type: ignore[arg-type]
            llm=fake_llm,
        )
