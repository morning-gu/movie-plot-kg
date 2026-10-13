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
