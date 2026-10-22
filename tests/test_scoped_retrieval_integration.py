import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("NEO4J_URI"),
                       reason="需要 NEO4J_URI / NEO4J_PASSWORD"),
]


class LLM1024:
    """embed 须与真库节点嵌入同维(1024);ScriptedLLM 默认 8 维会炸 cosine。"""

    def embed(self, texts):
        return [[0.1] * 1024 for _ in texts]


@pytest.fixture
def session():
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ.get("NEO4J_USER", "neo4j"),
              os.environ.get("NEO4J_PASSWORD", "")))
    s = driver.session()
    yield s
    s.close()
    driver.close()


def test_scoped_retrieve_only_returns_target_movie(session):
    # 宁空不污染的正面钉子:证据 node_id 全部属于目标片,零跨片混入
    from movie_kg.qa.planner import RetrievalPlan
    from movie_kg.qa.retriever import retrieve
    plan = RetrievalPlan(
        intent="causal", rewritten_query="仓库里 Mr. White 的对峙与冲突",
        target_entities=[], graph_queries=[], movie_id="tt0105236")
    evidence = retrieve(session, LLM1024(), plan)
    assert evidence, "RD 185 事件/21 角色,片内扫描必有结果"
    prefixes = ("evt_tt0105236_", "char_tt0105236_",
                "loc_tt0105236_", "vt_tt0105236_")
    assert all(e.node_id.startswith(prefixes) for e in evidence)
    assert not any(("tt0113243" in e.node_id) or ("tt0117500" in e.node_id)
                   for e in evidence)


def test_scoped_bind_hits_target_movie_character(session):
    from movie_kg.qa.planner import TargetEntity
    from movie_kg.qa.retriever import bind_entities
    bound = bind_entities(
        session, [TargetEntity(type="Character", name_hint="Mr. White")],
        vector_hits=[], movie_id="tt0105236")
    assert bound.get("Mr. White", "").startswith("char_tt0105236_")
