import pytest
from pydantic import BaseModel

from movie_kg.config import ModelsConfig
from movie_kg.llm import LLMClient, LLMError
from tests.fakes import FakeOpenAI

CFG = ModelsConfig(base_url="https://x", api_key="sk", vision_model="v",
                   text_model="t", embedding_model="e", embedding_dims=8)


class Out(BaseModel):
    answer: str


def test_valid_json_parsed():
    llm = LLMClient(CFG, client=FakeOpenAI(['{"answer": "ok"}']))
    assert llm.chat_json(model="t", messages=[], schema=Out).answer == "ok"


def test_retry_then_success():
    llm = LLMClient(CFG, client=FakeOpenAI(["不是json", '{"answer": "ok"}']))
    assert llm.chat_json(model="t", messages=[], schema=Out).answer == "ok"
    assert len(llm._client.calls) == 2


def test_exhausted_retries_raise():
    llm = LLMClient(CFG, client=FakeOpenAI(["bad", "bad2", "bad3"]))
    with pytest.raises(LLMError):
        llm.chat_json(model="t", messages=[], schema=Out, max_retries=3)


def test_usage_accumulates():
    llm = LLMClient(CFG, client=FakeOpenAI(['{"answer": "1"}', '{"answer": "2"}']))
    llm.chat_json(model="t", messages=[], schema=Out)
    llm.chat_json(model="t", messages=[], schema=Out)
    assert llm.usage == {"prompt_tokens": 20, "completion_tokens": 10}


def test_embed_order_and_count():
    llm = LLMClient(CFG, client=FakeOpenAI([]))
    vecs = llm.embed(["a", "b", "c"])
    assert len(vecs) == 3 and len(vecs[0]) == 2


def test_timeout_converted_to_llmerror_after_retries():
    # 网络挂起(APITimeoutError)必须转为场景级 LLMError,而不是掀翻构建
    import openai
    from types import SimpleNamespace

    calls = []

    def create(**kwargs):
        calls.append(1)
        raise openai.APITimeoutError(request=None)

    client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create)))
    llm = LLMClient(CFG, client=client)
    with pytest.raises(LLMError) as e:
        llm.chat_json(model="t", messages=[], schema=Out, max_retries=3)
    assert len(calls) == 3
    assert "LLM 调用失败" in str(e.value)


def test_server_overload_converted_to_llmerror_after_retries():
    # 5xx 过载(SiliconFlow 实测 503 System is too busy)同样按瞬态重试
    import openai
    from types import SimpleNamespace

    calls = []
    resp = SimpleNamespace(request=SimpleNamespace(method="POST", url="u"),
                           status_code=503, headers={})

    def create(**kwargs):
        calls.append(1)
        raise openai.InternalServerError("busy", response=resp, body=None)

    client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create)))
    llm = LLMClient(CFG, client=client)
    with pytest.raises(LLMError):
        llm.chat_json(model="t", messages=[], schema=Out, max_retries=3)
    assert len(calls) == 3
