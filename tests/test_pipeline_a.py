from movie_kg.extraction.checkpoints import CheckpointStore
from movie_kg.extraction.pipeline import run_phase_a
from movie_kg.llm import LLMError
from tests.fakes import ScriptedLLM
from tests.fixtures import make_movie_dir
from movie_kg.ingestion.data_loader import load_movie

A1 = {"events": [{"event_type": "action", "topic": "t", "description": "d",
                  "participating_characters": [{"name": "张三", "emotion": None,
                                                "role_description": None, "items": []}],
                  "subevents": []}]}
A2 = {"character_relations": []}


def test_phase_a_happy_path(tmp_path):
    d = make_movie_dir(tmp_path)
    movie = load_movie(d)
    store = CheckpointStore(d)
    llm = ScriptedLLM(A1, A2, A1, A2)  # scene1 A1,A2; scene2 A1,A2
    result = run_phase_a(llm, movie, store)
    assert result.completed == ["scene_001", "scene_002"]
    assert result.failed == [] and result.skipped == 0
    assert store.load("scene_001").status == "ok"


def test_phase_a_failure_does_not_block(tmp_path):
    d = make_movie_dir(tmp_path)
    movie = load_movie(d)
    store = CheckpointStore(d)
    llm = ScriptedLLM(LLMError("x"), A1, A2)  # scene1 失败;scene2 正常
    result = run_phase_a(llm, movie, store)
    assert result.failed == ["scene_001"]
    assert result.completed == ["scene_002"]
    assert store.load("scene_001").status == "failed"


def test_phase_a_skips_completed_on_rerun(tmp_path):
    d = make_movie_dir(tmp_path)
    movie = load_movie(d)
    store = CheckpointStore(d)
    run_phase_a(ScriptedLLM(A1, A2, A1, A2), movie, store)
    llm2 = ScriptedLLM()  # 不再提供任何响应:若被调用会 IndexError
    result = run_phase_a(llm2, movie, store)
    assert result.skipped == 2 and result.completed == []
    assert len(llm2.calls) == 0


def test_phase_a_scene_range(tmp_path):
    d = make_movie_dir(tmp_path, n_scenes=2)
    movie = load_movie(d)
    store = CheckpointStore(d)
    result = run_phase_a(ScriptedLLM(A1, A2), movie, store, scene_range=(1, 1))
    assert result.completed == ["scene_001"]


def test_phase_a_from_scratch(tmp_path):
    d = make_movie_dir(tmp_path)
    movie = load_movie(d)
    store = CheckpointStore(d)
    run_phase_a(ScriptedLLM(A1, A2, A1, A2), movie, store)
    result = run_phase_a(ScriptedLLM(A1, A2, A1, A2), movie, store, from_scratch=True)
    assert result.skipped == 0 and result.completed == ["scene_001", "scene_002"]
