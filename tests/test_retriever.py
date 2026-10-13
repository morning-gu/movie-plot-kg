from movie_kg.qa.planner import GraphQueryPlan, RetrievalPlan, TargetEntity
from movie_kg.qa.retriever import bind_entities, retrieve, vector_recall
from tests.fakes import MockSession, ScriptedLLM

VEC_HITS = [
    {"node": {"id": "evt_m1_001_01", "topic": "对峙", "description": "d",
              "event_type": "conflict"}, "score": 0.9},
]


def test_vector_recall_queries_all_four_indexes():
    session = MockSession(results=[VEC_HITS] * 4)
    hits = vector_recall(session, ScriptedLLM(), "张三发怒")
    assert len(hits) == 4
    indexes = [p["index"] for q, p in session.queries
               if "queryNodes" in q]
    assert set(indexes) == {"event_vec", "character_vec", "virtualtime_vec",
                            "location_vec"}


def test_bind_entities_exact_then_alias_then_fallback():
    # 1) 精确 name 命中
    session = MockSession(results=[[{"id": "char_m1_aaa"}]])
    bound = bind_entities(session, [TargetEntity(type="Character", name_hint="张三")],
                          vector_hits=[])
    assert bound["张三"] == "char_m1_aaa"
    # 2) aliases 命中(name 与 aliases 在同一次查询中匹配)
    session = MockSession(results=[[{"id": "char_m1_bbb"}]])
    bound = bind_entities(session, [TargetEntity(type="Character", name_hint="老张")],
                          vector_hits=[])
    assert bound["老张"] == "char_m1_bbb"
    # 3) 回落: 向量命中中同类型最近者
    from movie_kg.qa.retriever import Evidence
    hits = [Evidence(source="vector", node_id="char_m1_ccc", text="x", score=0.8,
                     extra={"label": "Character"})]
    session = MockSession(results=[[]])  # 图中无精确匹配
    bound = bind_entities(session, [TargetEntity(type="Character", name_hint="老板")],
                          vector_hits=hits)
    assert bound["老板"] == "char_m1_ccc"


def test_retrieve_executes_bound_templates_and_dedupes():
    plan = RetrievalPlan(
        intent="relational", rewritten_query="张三和李四的关系",
        target_entities=[TargetEntity(type="Character", name_hint="张三"),
                         TargetEntity(type="Character", name_hint="李四")],
        graph_queries=[GraphQueryPlan(
            template_id="relation_between_chars",
            params={"a": "张三", "b": "李四"})])
    # 队列: 4 次向量召回 + 2 次绑定查询 + 1 次模板执行
    session = MockSession(results=[
        VEC_HITS, [], [], [],                       # 向量召回(4 索引)
        [{"id": "char_m1_zhang"}], [{"id": "char_m1_li"}],  # 绑定
        [{"a": {"id": "char_m1_zhang", "name": "张三"},
          "r": [{"relation_type": "enemy"}],
          "b": {"id": "char_m1_li", "name": "李四"}}],      # 模板结果
    ])
    evidence = retrieve(session, ScriptedLLM(), plan)
    template_calls = [(q, p) for q, p in session.queries if "RELATED_TO" in q]
    assert template_calls, "必须执行图模板"
    assert template_calls[-1][1]["a"] == "char_m1_zhang"  # name_hint 已替换为 ID
    # 去重: 向量命中 evt_m1_001_01 + 图结果两个角色 = 3 条证据
    assert len(evidence) == 3


def test_retrieve_vector_only_when_no_graph_queries():
    plan = RetrievalPlan(intent="factual", rewritten_query="q",
                         target_entities=[], graph_queries=[])
    session = MockSession(results=[VEC_HITS, [], [], []])
    evidence = retrieve(session, ScriptedLLM(), plan)
    assert len(evidence) == 1 and evidence[0].source == "vector"
