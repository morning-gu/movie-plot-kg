from movie_kg.embeddings.embedder import describe_event, embed_all
from tests.fakes import MockSession, ScriptedLLM

NODES = [{"id": "evt_m1_001_01", "topic": "对峙", "description": "张三对峙",
          "event_type": "conflict", "labels": ["Event"]}]


def test_describe_event_concat():
    text = describe_event(topic="对峙", description="张三对峙", event_type="conflict")
    assert "对峙" in text and "张三对峙" in text and "conflict" in text


def test_embed_all_batches_and_sets():
    session = MockSession(results=[  # 第一次查询返回缺嵌入的节点
        [{"node": n} for n in NODES]])
    llm = ScriptedLLM()
    count = embed_all(session, llm, "m1")
    assert count == 1
    set_queries = [q for q, _ in session.queries if "embedding" in q and "SET" in q]
    assert set_queries, "必须写回 embedding 属性"
    params = [p for q, p in session.queries if "SET" in q and "embedding" in q][0]
    assert params["id"] == "evt_m1_001_01"
    assert isinstance(params["embedding"], list) and len(params["embedding"]) == 8
