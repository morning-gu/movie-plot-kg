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
    # 3) 词级包含: "John Mason" -> "John Patrick Mason"(全部词都在候选名中,唯一命中才绑)
    session = MockSession(results=[
        [],                                    # 精确/别名无命中
        [{"id": "char_m1_ddd", "name": "John Patrick Mason"}]])
    bound = bind_entities(session,
                          [TargetEntity(type="Character", name_hint="John Mason")],
                          vector_hits=[])
    assert bound["John Mason"] == "char_m1_ddd"
    # 4) 向量回落须有词交集——盲取首个同类命中会把语义相近者错绑
    #    (试点实证 "John Mason" 被绑到 Goodspeed),词不相交宁可不绑
    from movie_kg.qa.retriever import Evidence
    hits = [Evidence(source="vector", node_id="char_m1_ccc", text="张三:码头头目",
                     score=0.8, extra={"label": "Character"})]
    session = MockSession(results=[[], []])   # 精确无 + 候选无
    bound = bind_entities(session,
                          [TargetEntity(type="Character", name_hint="王五")],
                          vector_hits=hits)
    assert bound == {}
    # 5) 向量回落命中名与 hint 有词交集时可用
    session = MockSession(results=[[], []])
    bound = bind_entities(session,
                          [TargetEntity(type="Character", name_hint="张三")],
                          vector_hits=hits)
    assert bound["张三"] == "char_m1_ccc"


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


def test_retrieve_substitutes_id_key_via_binding():
    # causes_of 等模板要求 params 键为 id;绑定后 name_hint 必须替换为实体 ID
    plan = RetrievalPlan(
        intent="causal", rewritten_query="导致对峙的事件链",
        target_entities=[TargetEntity(type="Event", name_hint="对峙")],
        graph_queries=[GraphQueryPlan(
            template_id="causes_of", params={"id": "对峙", "depth": 3})])
    # 队列: 4 次向量召回 + 1 次绑定查询 + 1 次模板执行
    session = MockSession(results=[
        [], [], [], [],                       # 向量召回(4 索引)
        [{"id": "evt_m1_001_01"}],            # 绑定: 对峙 -> 事件 ID
        [{"path": {"id": "evt_m1_000_01", "topic": "争执", "description": "d",
                   "event_type": "conflict"}}],   # 模板结果(causes_of 返回 path)
    ])
    evidence = retrieve(session, ScriptedLLM(), plan)
    template_calls = [(q, p) for q, p in session.queries if "CAUSES" in q]
    assert template_calls, "必须执行 causes_of 模板"
    assert template_calls[-1][1]["id"] == "evt_m1_001_01"  # name_hint 已替换为 ID
    assert any(e.source == "graph" for e in evidence)


def test_retrieve_vector_only_when_no_graph_queries():
    plan = RetrievalPlan(intent="factual", rewritten_query="q",
                         target_entities=[], graph_queries=[])
    session = MockSession(results=[VEC_HITS, [], [], []])
    evidence = retrieve(session, ScriptedLLM(), plan)
    assert len(evidence) == 1 and evidence[0].source == "vector"


def test_retrieve_extracts_neo4j_node_and_path_shapes():
    # 真 driver 返回 Node/Path 对象而非 dict,且 Path 键名任意(如 "p")——
    # 旧代码 isinstance(value, dict) 全部漏掉,图证据被静默丢弃(试点实证)
    from types import SimpleNamespace

    class FakeNode(dict):
        labels = {"Character"}          # Neo4j Node 特征属性

    class FakePath:
        def __init__(self, nodes):
            self.nodes = nodes

    node_a = FakeNode({"id": "char_m1_aaa", "name": "张三", "description": "d"})
    node_b = FakeNode({"id": "char_m1_bbb", "name": "李四", "description": "d"})
    path = FakePath([FakeNode({"id": "evt_m1_001_01", "topic": "对峙",
                               "description": "d", "event_type": "conflict"})])
    session = MockSession(results=[[], [], [], []])          # 向量召回 4 索引
    session2 = MockSession(results=[[]])                     # bind 精确查询
    rows = [{"a": node_a, "r": ["rel"], "b": node_b}, {"p": path}]

    from movie_kg.qa import retriever as R
    orig = R.execute_template
    R.execute_template = lambda session, tid, params: iter(rows)
    try:
        plan = RetrievalPlan(intent="relational", rewritten_query="q",
                             target_entities=[], graph_queries=[
                                 GraphQueryPlan(template_id="relation_between_chars",
                                                params={})])
        llm = ScriptedLLM()
        evidence = R.retrieve(session2, llm, plan)
    finally:
        R.execute_template = orig
    graph_ids = [e.node_id for e in evidence if e.source == "graph"]
    assert "char_m1_aaa" in graph_ids and "char_m1_bbb" in graph_ids  # Node 形状
    assert "evt_m1_001_01" in graph_ids                              # Path 形状(键 p)


def test_retrieve_resolves_abbreviated_param_names():
    # LLM 的 params 值可能与 name_hint 不完全一致(缩写 "Mason"):
    # 词级子集解析到已绑定实体,而非裸名查库落空(试点实证)
    from movie_kg.qa.retriever import Evidence
    plan = RetrievalPlan(
        intent="relational", rewritten_query="q",
        target_entities=[TargetEntity(type="Character", name_hint="John Patrick Mason")],
        graph_queries=[GraphQueryPlan(template_id="relation_between_chars",
                                      params={"a": "Mason", "b": "x"})])
    session = MockSession(results=[[], [], [], []])   # 4 个向量召回查询空结果
    # 无任何绑定时参数原样传递,不崩
    from movie_kg.qa import retriever as R
    captured = {}
    orig = R.execute_template
    R.execute_template = lambda s, tid, params: (captured.update(params=params), iter([]))[1]
    try:
        R.retrieve(session, ScriptedLLM(), plan)
    finally:
        R.execute_template = orig
    assert captured["params"]["a"] == "Mason" and captured["params"]["b"] == "x"

    # 有绑定时缩写解析成功
    plan2 = plan.model_copy(deep=True)
    # 4 个向量查询空 + bind 精确查询命中 John Patrick Mason -> char_m1_mason
    session3 = MockSession(results=[[], [], [], [], [{"id": "char_m1_mason"}]])
    R2 = R
    captured.clear()
    orig = R2.execute_template
    R2.execute_template = lambda s, tid, params: (captured.update(params=params), iter([]))[1]
    try:
        # target name_hint "John Patrick Mason" 精确命中 char_m1_mason,
        # params 里写缩写 "Mason" -> 词级子集 -> 解析为 char_m1_mason
        R2.retrieve(session3, ScriptedLLM(), plan2)
    finally:
        R2.execute_template = orig
    assert captured["params"]["a"] == "char_m1_mason"
