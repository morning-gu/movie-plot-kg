from movie_kg.extraction.checkpoints import CheckpointStore
from movie_kg.extraction.global_passes import (
    AliasPlan, build_causal_pairs, collect_name_contexts, collect_vts,
    pass_b1_order_vts, pass_b2_merge_aliases, pass_b3_causal)
from movie_kg.extraction.pipeline import run_phase_a
from movie_kg.models.extraction import SceneExtraction
from tests.fakes import ScriptedLLM
from tests.fixtures import make_movie_dir
from movie_kg.ingestion.data_loader import load_movie

A1 = {"events": [
    {"event_type": "conflict", "topic": "对峙", "description": "张三李四对峙",
     "participating_characters": [{"name": "张三", "emotion": None,
                                   "role_description": None, "items": []}],
     "subevents": []},
    {"event_type": "action", "topic": "拔枪", "description": "张三拔枪",
     "participating_characters": [], "subevents": []}]}
A2 = {"character_relations": []}


def _setup(tmp_path):
    d = make_movie_dir(tmp_path)
    movie = load_movie(d)
    store = CheckpointStore(d)
    run_phase_a(ScriptedLLM(A1, A2, A1, A2), movie, store)
    exts = [store.load(s.meta.scene_id) for s in movie.scenes]
    return movie, exts


def test_collect_vts_dedupes(tmp_path):
    movie, _ = _setup(tmp_path)
    vts = collect_vts(movie)
    assert [v.name for v in vts] == ["第一天傍晚", "第二天清晨"]
    assert vts[0].scene_ids == [1]


def test_b1_orders_and_backfills_missing(tmp_path):
    movie, _ = _setup(tmp_path)
    vts = collect_vts(movie)
    # LLM 只排了第二个,第一个应按原序追加到末尾
    ordered = pass_b1_order_vts(ScriptedLLM(
        {"ordered": [{"name": "第二天清晨", "description": "d2"}]}), vts)
    assert ordered[0].name == "第二天清晨" and ordered[0].timeline_index == 0
    assert ordered[1].name == "第一天傍晚" and ordered[1].timeline_index == 1
    assert ordered[1].description != ""


def test_b2_plan_resolve(tmp_path):
    chars, locs = collect_name_contexts(*_setup(tmp_path))
    plan: AliasPlan = pass_b2_merge_aliases(ScriptedLLM(
        {"characters": [{"canonical": "张三", "aliases": ["老张", "张老板"],
                         "description": "码头头目"}],
         "locations": []}), chars, locs)
    assert plan.resolve("character", "张老板") == "张三"
    assert plan.resolve("character", "李四") == "李四"      # 未命中保持原名
    assert plan.resolve("location", "警局办公室") == "警局办公室"


def test_b2_batches_large_character_lists():
    # meta.cast 并入后角色可达 65 人,单次输出的归并 JSON 超 max_tokens 截断
    # (Hackers 试点实证:949 行截断):分批调用拼接,未归并者透传
    from movie_kg.extraction.global_passes import NameContext
    chars = [NameContext(name=f"角色{i:02d}") for i in range(30)]
    locs = [NameContext(name="码头")]
    llm = ScriptedLLM(
        {"characters": [{"canonical": "角色00", "aliases": ["零号"],
                         "description": "d"}], "locations": []},
        {"characters": [{"canonical": "角色29", "aliases": ["廿九"],
                         "description": "d"}], "locations": []},
    )
    plan = pass_b2_merge_aliases(llm, chars, locs, batch_size=15)
    assert plan.resolve("character", "零号") == "角色00"
    assert plan.resolve("character", "廿九") == "角色29"
    assert plan.resolve("character", "角色15") == "角色15"   # 未归并者透传
    assert len(llm.calls) == 2
    assert "角色00" in llm.calls[0][1][0]["content"]
    assert "角色29" in llm.calls[1][1][0]["content"]


def test_build_causal_pairs_intra_and_boundary(tmp_path):
    movie, exts = _setup(tmp_path)
    pairs = build_causal_pairs(movie, exts)
    ids = [(p.cause_id, p.effect_id) for p in pairs]
    # scene_001 内: 01->02;边界: scene_001 末主事件 -> scene_002 首主事件
    assert ("evt_m1_001_01", "evt_m1_001_02") in ids
    assert ("evt_m1_001_02", "evt_m1_002_01") in ids
    assert len(ids) == 3  # 2 个场景内对 + 1 个边界对


def test_b3_drops_unknown_ids(tmp_path):
    movie, exts = _setup(tmp_path)
    pairs = build_causal_pairs(movie, exts)
    links = pass_b3_causal(ScriptedLLM(
        {"causal_links": [
            {"cause_id": "evt_m1_001_01", "effect_id": "evt_m1_001_02", "reason": "r"},
            {"cause_id": "evt_m1_999_01", "effect_id": "evt_m1_001_02", "reason": "x"}]}),
        pairs)
    assert len(links) == 1 and links[0].reason == "r"


def test_b3_batches_large_pair_lists():
    # 全片事件对超出单次 max_tokens:分批调用拼接结果(试点实证修复)
    from movie_kg.extraction.global_passes import EventPair
    pairs = [EventPair(cause_id=f"e{i}", cause_topic="a", effect_id=f"e{i+1}",
                       effect_topic="b") for i in range(81)]
    llm = ScriptedLLM(
        {"causal_links": [{"cause_id": "e0", "effect_id": "e1", "reason": "r1"}]},
        {"causal_links": [{"cause_id": "e80", "effect_id": "e81", "reason": "r2"}]},
    )
    links = pass_b3_causal(llm, pairs, batch_size=80)
    assert [l.reason for l in links] == ["r1", "r2"]
    assert len(llm.calls) == 2
    assert "e0" in llm.calls[0][1][0]["content"]      # 第一批含 e0
    assert "e80" in llm.calls[1][1][0]["content"]     # 第二批含 e80
