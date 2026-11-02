from types import SimpleNamespace

import pytest

import tools.llm_client as llm_client
from tools.llm_client import call_llm
from utils.exceptions import LLMResponseError, LLMServiceError


class FakeCompletions:
    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.kwargs = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        content = self.contents.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def fake_client(contents: list[str]):
    completions = FakeCompletions(contents)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def test_call_llm_parses_json(monkeypatch) -> None:
    client, completions = fake_client(['```json\n{"ok":true}\n```'])
    monkeypatch.setattr(llm_client, "OpenAI", lambda **kwargs: client)

    result = call_llm(
        "user",
        {"model": "fake"},
        system_prompt="system",
        config={"text_llm_url": "http://unused/v1", "text_llm_name": "fake"},
    )

    assert result == {"ok": True}
    assert completions.kwargs[0]["response_format"] == {"type": "json_object"}
    assert completions.kwargs[0]["messages"][0]["content"] == "system"


def test_call_llm_rejects_non_json_response(monkeypatch) -> None:
    client, _ = fake_client(["not json"])
    monkeypatch.setattr(llm_client, "OpenAI", lambda **kwargs: client)

    with pytest.raises(LLMResponseError):
        call_llm(
            "user",
            {"model": "fake"},
            config={"text_llm_url": "http://unused/v1", "text_llm_name": "fake"},
        )


def test_legacy_parameters_do_not_override_model_config(monkeypatch) -> None:
    client, completions = fake_client(['{"label":"新闻"}'])
    monkeypatch.setattr(llm_client, "OpenAI", lambda **kwargs: client)

    result = call_llm(
        "请判断文本类别",
        {"model": "fake", "temperature": 0.1, "max_tokens": 128},
        config={"text_llm_url": "http://unused/v1", "text_llm_name": "configured-model", "temperature": .3, "max_tokens": 256},
    )

    assert result == {"label": "新闻"}
    assert completions.kwargs[0]["model"] == 'configured-model'
    assert completions.kwargs[0]["temperature"] == .3
    assert completions.kwargs[0]["max_tokens"] == 256




def test_connection_exception_chain_is_preserved_for_evaluation_fallback(monkeypatch):
    import httpx
    from openai import APIConnectionError
    error = APIConnectionError(request=httpx.Request('POST', 'http://unused/v1/chat/completions'))
    def disconnected(**kwargs):
        raise error
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=disconnected)))
    monkeypatch.setattr(llm_client, 'OpenAI', lambda **kwargs: client)
    with pytest.raises(LLMServiceError) as raised:
        call_llm('user', config={'text_llm_url': 'http://unused/v1', 'text_llm_name': 'fake'})
    assert raised.value.__cause__ is error
