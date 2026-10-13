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
