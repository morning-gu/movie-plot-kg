import logging

import pytest

from movie_kg.extraction.parse import align_name, levenshtein, parse_a1, parse_a2
from movie_kg.models.scene import CharacterMeta, SceneMeta
from movie_kg.models.extraction import ExtractedEvent, Participant

ROSTER = [CharacterMeta(name="张三", aliases=["老张"]), CharacterMeta(name="李四", aliases=[])]
META = SceneMeta(scene_id="scene_003", movie_id="m1", start_ts="00:00:00",
                 end_ts="00:02:00", characters=ROSTER, dialogues=[], location="x",
                 virtual_time="y")


def test_levenshtein():
    assert levenshtein("张三", "张叁") == 1
    assert levenshtein("abc", "abc") == 0


def test_align_name_variants():
    assert align_name("张三", ROSTER) == "张三"        # 精确
    assert align_name("老张", ROSTER) == "张三"        # 别名
    assert align_name("张叁", ROSTER) == "张三"        # 编辑距离 ≤2
    assert align_name("大老板张三", ROSTER) == "张三"   # 包含
    assert align_name("王五", ROSTER) == "未识别:王五"  # 对不上 -> 保留前缀


def _a1_raw():
    return {"events": [{
        "event_type": "conflict", "topic": "对峙", "description": "张三与李四对峙",
        "participating_characters": [
            {"name": "张三", "emotion": "愤怒", "role_description": "发起者", "items": ["枪"]},
            {"name": "王五", "emotion": None, "role_description": None, "items": []}],
        "subevents": [{
            "event_type": "action", "topic": "拔枪",
            "description": "张三拔枪", "participating_characters": []}]}]}


def test_parse_a1_assigns_ids_and_parents():
    events = parse_a1(_a1_raw(), META)
    assert [e.id for e in events] == ["evt_m1_003_01", "evt_m1_003_02"]
    assert events[0].order == 1 and not events[0].is_subevent
    assert events[1].is_subevent and events[1].parent_id == "evt_m1_003_01"
    assert events[1].order == 2
    assert events[0].participants[0].name == "张三"
    assert events[0].participants[1].name == "未识别:王五"


def test_parse_a1_rejects_bad_event_type():
    raw = _a1_raw()
    raw["events"][0]["event_type"] = "romance"
    with pytest.raises(Exception):
        parse_a1(raw, META)


def test_parse_a2_maps_evidence_index():
    events = parse_a1(_a1_raw(), META)
    raw = {"character_relations": [{
        "char_a": "张三", "char_b": "李四", "relation_type": "enemy",
        "is_new": True, "change_description": None, "evidence_event_index": 0}]}
    rels = parse_a2(raw, events, ROSTER)
    assert rels[0].start_event_id == "evt_m1_003_01"
    assert rels[0].char_a == "张三"


def test_parse_a2_rejects_index_out_of_range():
    events = parse_a1(_a1_raw(), META)
    raw = {"character_relations": [{
        "char_a": "张三", "char_b": "李四", "relation_type": "enemy",
        "is_new": True, "change_description": None, "evidence_event_index": 5}]}
    with pytest.raises(ValueError):
        parse_a2(raw, events, ROSTER)


def test_parse_a1_warns_and_skips_nested_subevents(caplog):
    raw = _a1_raw()
    raw["events"][0]["subevents"][0]["subevents"] = [{
        "event_type": "dialogue", "topic": "nested",
        "description": "嵌套子事件", "participating_characters": []}]
    with caplog.at_level(logging.WARNING, logger="movie_kg.extraction.parse"):
        events = parse_a1(raw, META)
    assert [e.id for e in events] == ["evt_m1_003_01", "evt_m1_003_02"]
    assert [e.topic for e in events] == ["对峙", "拔枪"]
    assert all(e.topic != "nested" for e in events)
    assert "scene_003" in caplog.text
    assert "拔枪" in caplog.text
