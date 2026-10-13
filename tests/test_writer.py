from movie_kg.extraction.global_passes import AliasPlan, MergeGroup
from movie_kg.graph.writer import GraphWriter, char_id
from movie_kg.models.extraction import Participant, ResolvedRelation, SceneEvent
from movie_kg.models.scene import SceneMeta
from tests.fakes import MockSession

META = SceneMeta(scene_id="scene_001", movie_id="m1", start_ts="00:00:00",
                 end_ts="00:02:00",
                 characters=[{"name": "张三", "aliases": []}],
                 dialogues=[], location="码头仓库", virtual_time="第一天傍晚")
EVENTS = [
    SceneEvent(id="evt_m1_001_01", event_type="conflict", topic="对峙",
               description="张三对峙", scene_id="scene_001", movie_id="m1",
               order=1, participants=[
                   Participant(name="张三", emotion="愤怒",
                               role_description="发起者", items=["枪"])]),
    SceneEvent(id="evt_m1_001_02", event_type="action", topic="拔枪",
               description="张三拔枪", scene_id="scene_001", movie_id="m1",
               order=2, is_subevent=True, parent_id="evt_m1_001_01"),
]


def _writer(session, plan=None):
    w = GraphWriter(session, alias_plan=plan)
    w.ensure_movie("m1", "测试影片")
    w.ensure_scene(META)
    return w


def test_write_extraction_merges_event_with_global_order():
    s = MockSession()
    _writer(s).write_extraction(_ext(), META)
    q = "\n".join(query for query, _ in s.queries)
    assert "MERGE (e:Event {id: $id})" in q
    event_params = [p for query, p in s.queries
                    if "MERGE (e:Event" in query][0]
    assert event_params["id"] == "evt_m1_001_01"
    assert event_params["global_order"] == 1001  # seq*1000 + order
    assert event_params["movie_id"] == "m1"
    assert "PARTICIPATES_IN" in q and "SCENE_OF" in q
    assert "OCCURS_AT" in q and "LOCATED_AT" in q and "BELONGS_TO" in q
    assert "SUBEVENT_OF" in q


def _ext():
    from movie_kg.models.extraction import SceneExtraction
    return SceneExtraction(scene_id="scene_001", movie_id="m1", status="ok",
                           events=EVENTS)


def test_alias_plan_resolves_names():
    s = MockSession()
    plan = AliasPlan(characters=[MergeGroup(canonical="张三", aliases=["张老板"],
                                             description="码头头目")])
    _writer(s, plan).write_extraction(_ext(), META)
    cid = char_id("m1", "张三")
    char_params = [p for query, p in s.queries if "MERGE (c:Character" in query]
    assert char_params and any(p.get("id") == cid for p in char_params)


def test_precedes_chain_with_boundary_gap():
    s = MockSession()
    meta2 = META.model_copy(update={"scene_id": "scene_002", "start_ts": "00:02:00",
                                    "end_ts": "00:04:00", "virtual_time": "第二天清晨"})
    from movie_kg.models.extraction import SceneExtraction
    ext1 = _ext()
    ext2 = SceneExtraction(scene_id="scene_002", movie_id="m1", status="ok", events=[
        SceneEvent(id="evt_m1_002_01", event_type="dialogue", topic="问话",
                   description="问话", scene_id="scene_002", movie_id="m1", order=1)])
    _writer(s).write_precedes_chain([ext1, ext2], [META, meta2])
    precedes = [(q, p) for q, p in s.queries if "PRECEDES" in q]
    ids = [p for q, p in precedes if p.get("gap") is not None]
    # 场景内 gap=None;边界边 gap=第二天清晨
    boundary = [p for q, p in precedes if p.get("gap") == "第二天清晨"]
    assert boundary and boundary[0]["a"] == "evt_m1_001_01"
    assert boundary[0]["b"] == "evt_m1_002_01"


def test_relation_change_invalidates_old_edge():
    s = MockSession()
    w = _writer(s)
    rel_new = ResolvedRelation(char_a="张三", char_b="李四", relation_type="friend",
                               is_new=True, start_event_id="evt_m1_001_01")
    w.write_relations([rel_new], "m1")
    assert any("MERGE (a)-[rel:RELATED_TO" in q for q, _ in s.queries)
    s2_queries_before = len(s.queries)
    rel_change = ResolvedRelation(char_a="张三", char_b="李四", relation_type="enemy",
                                  is_new=False, change_description="决裂",
                                  start_event_id="evt_m1_001_02")
    w.write_relations([rel_change], "m1")
    after = s.queries[s2_queries_before:]
    invalidate = [q for q, _ in after if "valid = false" in q or "valid=false" in q]
    assert invalidate, "变更关系必须先失效旧边"
    assert any("end_event_id" in q for q, _ in after)


def test_apply_vt_order_sets_index_and_description():
    s = MockSession()
    from movie_kg.extraction.global_passes import OrderedVt
    _writer(s).apply_vt_order([OrderedVt(name="第一天傍晚", timeline_index=0,
                                         description="开始之夜")])
    q, p = s.queries[-1]
    assert "timeline_index" in q and "description" in q
    assert p["idx"] == 0 and p["desc"] == "开始之夜"


def test_causal_links_written():
    s = MockSession()
    from movie_kg.extraction.global_passes import CausalLink
    _writer(s).write_causal_links(
        [CausalLink(cause_id="evt_m1_001_01", effect_id="evt_m1_001_02", reason="r")])
    assert any("CAUSES" in q for q, _ in s.queries)
