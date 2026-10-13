from movie_kg.extraction.event_extractor import extract_scene_events
from movie_kg.extraction.relation_extractor import extract_scene_relations
from movie_kg.ingestion.frame_sampler import Frame
from movie_kg.llm import LLMError
from tests.fakes import ScriptedLLM
from tests.fixtures import make_movie_dir
from movie_kg.ingestion.data_loader import load_movie

A1 = {"events": [{"event_type": "conflict", "topic": "对峙", "description": "张三与李四对峙",
                  "participating_characters": [
                      {"name": "张三", "emotion": "愤怒", "role_description": "发起者", "items": []},
                      {"name": "李四", "emotion": "紧张", "role_description": "应对", "items": []}],
                  "subevents": []}]}
A2 = {"character_relations": [{"char_a": "张三", "char_b": "李四", "relation_type": "enemy",
                               "is_new": True, "change_description": None,
                               "evidence_event_index": 0}]}


def test_relation_extraction_maps_ids(tmp_path):
    movie = load_movie(make_movie_dir(tmp_path))
    events = extract_scene_events(ScriptedLLM(A1), [Frame(0.0, b"x")], movie.scenes[0])
    rels = extract_scene_relations(
        ScriptedLLM(A2), events, movie.scenes[0].meta.characters,
        prior_relations=[])
    assert rels[0].start_event_id == "evt_m1_001_01"
    assert rels[0].relation_type == "enemy"
