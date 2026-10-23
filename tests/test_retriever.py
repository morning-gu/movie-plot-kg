from movie_kg.qa.planner import GraphQueryPlan, RetrievalPlan, TargetEntity
from movie_kg.qa.retriever import (Evidence, bind_entities, retrieve,
                                   temporal_window, vector_recall)
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
    # 默认召回数 = 10(检索扩容实验实证 +5pp accuracy,2026-10-21)
    assert all(p["k"] == 10 for q, p in session.queries if "queryNodes" in q)


def test_vector_recall_scoped_scans_movie_subgraph():
    # 片内精确扫描(设计:真·片内检索,否决超采过滤):每标签一次,
    # 带 $mid/$vec/$k,绝不走全局 ANN;某标签空结果不崩
    hit = {"n": {"id": "evt_tt0105236_001_01", "topic": "对峙",
                 "description": "d", "event_type": "conflict"}, "score": 0.9}
    session = MockSession(results=[[hit], [], [], []])   # Event 命中,其余标签空
    hits = vector_recall(session, ScriptedLLM(), "仓库对峙", movie_id="tt0105236")
    assert len(hits) == 1 and hits[0].node_id == "evt_tt0105236_001_01"
    assert hits[0].extra["label"] == "Event" and hits[0].score == 0.9
    assert not any("queryNodes" in q for q, _ in session.queries), \
        "scoped 不得走全局 ANN"
    scans = [(q, p) for q, p in session.queries if "cosine" in q]
    assert len(scans) == 4
    assert all(p == {"mid": "tt0105236", "k": 10, "vec": [0.0] * 8}
               for _, p in scans)
    labels = {l for q, _ in scans
              for l in ("Event", "Character", "VirtualTime", "Location")
              if f"(n:{l} " in q}
    assert labels == {"Event", "Character", "VirtualTime", "Location"}


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


def test_retrieve_threads_movie_id_from_plan():
    # plan.movie_id 穿到召回与绑定;scoped 全空时保持空(宁空不污染),
    # 绝不回落全局 ANN(设计反目标)
    plan = RetrievalPlan(intent="factual", rewritten_query="q",
                         target_entities=[TargetEntity(
                             type="Character", name_hint="张三")],
                         graph_queries=[], movie_id="tt0105236")
    session = MockSession(results=[[], [], [], [], []])   # 4 扫描 + 绑定全空
    evidence = retrieve(session, ScriptedLLM(), plan)
    assert evidence == []
    assert not any("queryNodes" in q for q, _ in session.queries)
    assert all(p.get("mid") == "tt0105236" for _, p in session.queries)


def test_bind_entities_tolerates_null_name_candidates():
    # Event 实体无 name 属性(返回 None):词级匹配不得崩溃,自然不绑定
    session = MockSession(results=[
        [],                                            # 精确无命中
        [{"id": "evt_m1_001_01", "name": None}]])      # 候选 name 为 None
    bound = bind_entities(session,
                          [TargetEntity(type="Event", name_hint="决裂")],
                          vector_hits=[])
    assert bound == {}


def test_bind_entities_scoped_tiers_carry_movie_id():
    # 精确层与词级扫描层都带 movie_id 条件(修现存跨片同名错绑:
    # "未识别:群演"3 片全有、"Agent" 在 Hackers+Rock)
    session = MockSession(results=[[], []])   # 精确无 + 词级无 → 不绑
    bound = bind_entities(session,
                          [TargetEntity(type="Character", name_hint="张三")],
                          vector_hits=[], movie_id="tt0105236")
    assert bound == {}
    t1, t2 = session.queries[0], session.queries[1]
    assert "n.movie_id = $mid" in t1[0] and t1[1]["mid"] == "tt0105236"
    assert "n.movie_id = $mid" in t2[0] and t2[1]["mid"] == "tt0105236"


def test_bind_entities_global_queries_unchanged():
    # 全局模式逐字节不变:查询串与参数都不得出现 movie_id/mid
    session = MockSession(results=[[], []])
    bind_entities(session,
                  [TargetEntity(type="Character", name_hint="张三")],
                  vector_hits=[])
    for q, p in session.queries:
        assert "movie_id" not in q and "mid" not in p


# ============ temporal_window(ID 序时间窗,spec v2.1)============

def _ev(nid, score=0.9, label="Event"):
    return Evidence(source="vector", node_id=nid, text=f"t:{nid}", score=score,
                    extra={"label": label})


def _wrow(nid, scn, locid, loc, topic="t"):
    return {"id": nid, "topic": topic, "description": "d", "event_type": "action",
            "scn": scn, "ord": nid[-2:], "locid": locid, "loc": loc}


VOTE = [  # 查询 A 的脚本化结果:4 票餐厅(非空,locid 与 _wrow 一致)+ 2 票空名(不计)
    {"id": "evt_tt0105236_002_03", "lid": "loc_aaa", "lname": "餐厅"},
    {"id": "evt_tt0105236_002_05", "lid": "loc_aaa", "lname": "餐厅"},
    {"id": "evt_tt0105236_003_02", "lid": "loc_aaa", "lname": "餐厅"},
    {"id": "evt_tt0105236_003_07", "lid": "loc_aaa", "lname": "餐厅"},
    {"id": "evt_tt0105236_020_01", "lid": "loc_tt0105236_d41d8cd9", "lname": ""},
    {"id": "evt_tt0105236_021_01", "lid": None, "lname": None},
]


def test_temporal_window_vote_anchor_and_stop():
    hits = [_ev("evt_tt0105236_002_03", 0.9), _ev("evt_tt0105236_002_05", 0.8),
            _ev("evt_tt0105236_003_02", 0.7), _ev("evt_tt0105236_003_07", 0.6),
            _ev("evt_tt0105236_020_01", 0.5), _ev("evt_tt0105236_021_01", 0.4),
            _ev("char_tt0105236_x", 0.95, label="Character")]  # 非 Event 不计
    win = [  # 锚点=胜组最高分 002_03;残余 002_04/05;场景3 同地点不算新;
        # 004 新地点①收;005 新地点②停在其前(镜像真实 rd_t1:场景2-3 共享地点)
        _wrow("evt_tt0105236_002_04", 2, "loc_aaa", "餐厅"),
        _wrow("evt_tt0105236_002_05", 2, "loc_aaa", "餐厅"),
        _wrow("evt_tt0105236_003_01", 3, "loc_aaa", "餐厅"),
        _wrow("evt_tt0105236_003_02", 3, "loc_aaa", "餐厅"),
        _wrow("evt_tt0105236_004_01", 4, "loc_bbb", "仓库"),
        _wrow("evt_tt0105236_004_02", 4, "loc_bbb", "仓库"),
        _wrow("evt_tt0105236_005_01", 5, "loc_ccc", "闪回"),
        _wrow("evt_tt0105236_005_02", 5, "loc_ccc", "闪回"),
    ]
    session = MockSession(results=[VOTE, win])
    rows = temporal_window(session, hits)
    assert [r.node_id for r in rows] == [
        "evt_tt0105236_002_04", "evt_tt0105236_002_05",
        "evt_tt0105236_003_01", "evt_tt0105236_003_02",
        "evt_tt0105236_004_01", "evt_tt0105236_004_02"]
    assert all(r.source == "temporal_window" for r in rows)
    assert rows[0].text.startswith("【时间序 1·场景2】")
    assert rows[5].text.startswith("【时间序 6·场景4】")
    wq, wp = session.queries[1]          # 窗口查询参数:锚点位置 + 本片 + 上限
    assert wp == {"mid": "tt0105236", "scn": 2, "ord": 3, "lim": 60}


def test_temporal_window_budget_cap():
    # 锚点 002_01;残余 1(002_02)+ 后续同地点 29 行(永不触发停止)→
    # 后续按 WINDOW_BUDGET=24 截断 → 合计 25 行
    vote = [{"id": "evt_tt0105236_002_01", "lid": "loc_aaa", "lname": "餐厅"}]
    win = [_wrow("evt_tt0105236_002_02", 2, "loc_aaa", "餐厅")] + \
          [_wrow(f"evt_tt0105236_003_{i:02d}", 3, "loc_aaa", "餐厅")
           for i in range(1, 30)]
    session = MockSession(results=[vote, win])
    rows = temporal_window(session, [_ev("evt_tt0105236_002_01")])
    assert len(rows) == 25
    assert rows[24].node_id == "evt_tt0105236_003_24"


def test_temporal_window_all_empty_locations_falls_back_to_top_hit():
    vote = [{"id": "evt_tt0105236_002_03", "lid": "loc_d", "lname": ""},
            {"id": "evt_tt0105236_020_01", "lid": None, "lname": None}]
    win = [_wrow("evt_tt0105236_002_05", 2, "loc_x", "仓库")]
    session = MockSession(results=[vote, win])
    rows = temporal_window(session, [_ev("evt_tt0105236_002_03", 0.9),
                                     _ev("evt_tt0105236_020_01", 0.4)])
    assert [r.node_id for r in rows] == ["evt_tt0105236_002_05"]


def test_temporal_window_tie_breaks_by_top_scored_hit():
    # 平票 1:1 → 最高分命中(002_01,0.9)所在组胜,锚点即它
    vote = [{"id": "evt_tt0105236_002_01", "lid": "loc_a", "lname": "餐厅"},
            {"id": "evt_tt0105236_020_01", "lid": "loc_b", "lname": "仓库"}]
    win = [_wrow("evt_tt0105236_002_02", 2, "loc_a", "餐厅")]
    session = MockSession(results=[vote, win])
    temporal_window(session, [_ev("evt_tt0105236_002_01", 0.9),
                              _ev("evt_tt0105236_020_01", 0.4)])
    assert session.queries[1][1]["scn"] == 2     # 锚点在场景 2,非 20


def test_temporal_window_movie_from_anchor_id():
    # 全局模式锚点是别片事件 → 窗口按锚点本片开(Review Focus 2)
    vote = [{"id": "evt_tt0117500_008_04", "lid": "loc_r", "lname": "墓地"}]
    win = [_wrow("evt_tt0117500_009_01", 9, "loc_r2", "字幕")]
    session = MockSession(results=[vote, win])
    rows = temporal_window(session, [_ev("evt_tt0117500_008_04")])
    assert session.queries[1][1]["mid"] == "tt0117500"
    assert [r.node_id for r in rows] == ["evt_tt0117500_009_01"]


def test_temporal_window_empty_rows_returns_empty():
    vote = [{"id": "evt_tt0105236_002_03", "lid": "loc_a", "lname": "餐厅"}]
    session = MockSession(results=[vote, []])    # 锚点在全片末尾
    assert temporal_window(session, [_ev("evt_tt0105236_002_03")]) == []


def test_temporal_window_no_event_hits_returns_empty():
    session = MockSession(results=[])
    assert temporal_window(session, []) == []
    assert session.queries == []                  # 无 Event 命中不发任何查询


# ============ retrieve() temporal 集成(spec:intent 硬门 + 窗口行覆盖)============

def test_retrieve_temporal_plan_adds_window_and_supersedes():
    plan = RetrievalPlan(intent="temporal", rewritten_query="q",
                         target_entities=[], graph_queries=[])
    vec = [  # 事件索引一次返回 3 命中:2 票餐厅 + 1 票仓库(输票)
        {"node": {"id": "evt_tt0105236_003_02", "topic": "讨论", "description": "d",
                  "event_type": "dialogue"}, "score": 0.9},
        {"node": {"id": "evt_tt0105236_003_07", "topic": "告别", "description": "d",
                  "event_type": "dialogue"}, "score": 0.8},
        {"node": {"id": "evt_tt0105236_004_01", "topic": "对峙", "description": "d",
                  "event_type": "conflict"}, "score": 0.7},
    ]
    vote = [
        {"id": "evt_tt0105236_003_02", "lid": "loc_aaa", "lname": "餐厅"},
        {"id": "evt_tt0105236_003_07", "lid": "loc_aaa", "lname": "餐厅"},
        {"id": "evt_tt0105236_004_01", "lid": "loc_bbb", "lname": "仓库"},
    ]
    win = [  # 锚点=胜组最高分 003_02;残余 003_08/09;004_01 新地点①收
        # (004_01 同时是向量命中 → 覆盖语义);005 新地点②停
        _wrow("evt_tt0105236_003_08", 3, "loc_aaa", "餐厅"),
        _wrow("evt_tt0105236_003_09", 3, "loc_aaa", "餐厅"),
        _wrow("evt_tt0105236_004_01", 4, "loc_bbb", "仓库"),
        _wrow("evt_tt0105236_005_01", 5, "loc_ccc", "闪回"),
    ]
    session = MockSession(results=[vec, [], [], [], vote, win])
    evidence = retrieve(session, ScriptedLLM(), plan)
    assert any(q.startswith("MATCH (e:Event) WHERE e.id IN $ids")
               for q, _ in session.queries)
    marked = [e for e in evidence if e.node_id == "evt_tt0105236_004_01"]
    assert len(marked) == 1                     # 覆盖:只此一行
    assert marked[0].source == "temporal_window"
    assert marked[0].text.startswith("【时间序 3·场景4】")


def test_retrieve_non_temporal_never_issues_window_queries():
    plan = RetrievalPlan(intent="factual", rewritten_query="q",
                         target_entities=[], graph_queries=[])
    session = MockSession(results=[VEC_HITS, [], [], []])
    retrieve(session, ScriptedLLM(), plan)
    assert not any("LOCATED_AT" in q for q, _ in session.queries)
    # 逐字节钉:查询数与既有非 temporal 路径一致(4 召回,无绑定/模板/窗口)
    assert len(session.queries) == 4
