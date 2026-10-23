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


def test_rd_t2_window_covers_gold(session):
    # 真图机制冒烟:直接喂真实场景8事件为命中(语义锚定是全量评测的事——
    # 常量 embed 无法承载语义断言)。钉:多数票→锚点=最高分→残余覆盖
    # gold(008_06-08)→不跨片→ID 序→前缀格式,全部在真数据上
    from movie_kg.qa.retriever import Evidence, temporal_window
    hits = [Evidence(source="vector", node_id=f"evt_tt0105236_008_{i:02d}",
                     text="t", score=0.9 - i * 0.01, extra={"label": "Event"})
            for i in range(1, 6)]          # 008_01..008_05,分数递减
    rows = temporal_window(session, hits)
    assert rows, "真图上窗口必须开"
    ids = [r.node_id for r in rows]
    assert {"evt_tt0105236_008_06", "evt_tt0105236_008_07",
            "evt_tt0105236_008_08"} <= set(ids)   # 残余覆盖 gold
    assert all(r.node_id.startswith("evt_tt0105236_") for r in rows)
    scns = [int(r.node_id.split("_")[2]) for r in rows]
    assert scns == sorted(scns)                                        # ID 序
    assert all("【时间序" in r.text for r in rows)


def test_rk_t1_subevent_anchor_still_opens_window(session):
    # 锚点可能落在链外子事件(Rock 008_02-05 PRECEDES 度 0)——
    # ID 序窗口对子事件免疫,必须照常开窗(可达性另论,机制不崩)
    from movie_kg.qa.planner import RetrievalPlan
    from movie_kg.qa.retriever import retrieve
    plan = RetrievalPlan(intent="temporal",
                         rewritten_query="Hummel 在妻子墓前的告别",
                         target_entities=[], graph_queries=[],
                         movie_id="tt0117500")
    evidence = retrieve(session, LLM1024(), plan)
    win = [e for e in evidence if e.source == "temporal_window"]
    assert win, "子事件锚点也必须开窗(ID 序不走链)"
    assert all(e.node_id.startswith("evt_tt0117500_") for e in win)
