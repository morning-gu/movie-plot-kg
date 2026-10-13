import pytest

from movie_kg.extraction.event_extractor import extract_scene_events
from movie_kg.ingestion.frame_sampler import Frame
from movie_kg.llm import LLMError
from movie_kg.models.scene import MovieData
from tests.fixtures import make_movie_dir
from movie_kg.ingestion.data_loader import load_movie

A1 = {"events": [{
    "event_type": "conflict", "topic": "对峙", "description": "张三与李四在仓库对峙",
    "participating_characters": [
        {"name": "张三", "emotion": "愤怒", "role_description": "发起者", "items": []}],
    "subevents": []}]}


def _movie(tmp_path) -> MovieData:
    return load_movie(make_movie_dir(tmp_path))


def test_extract_parses_and_aligns(tmp_path):
    from tests.fakes import ScriptedLLM
    llm = ScriptedLLM(A1)
    movie = _movie(tmp_path)
    frames = [Frame(ts=0.5, jpeg=b"\xff\xd8fake")]
    events = extract_scene_events(llm, frames, movie.scenes[0])
    assert events[0].id == "evt_m1_001_01"
    assert events[0].participants[0].name == "张三"
    model, messages = llm.calls[0]
    assert model  # 使用 vision 模型(由 llm 替身记录)
    user_content = messages[-1]["content"]
    assert any(p.get("type") == "image_url" for p in user_content)
    system = messages[0]["content"]
    assert "00:00:01 张三(愤怒/拍桌): 你到底想怎样?" in system
    assert "我只是来谈生意的" in system


def test_extract_propagates_llm_error(tmp_path):
    from tests.fakes import ScriptedLLM
    llm = ScriptedLLM(LLMError("bad json"))
    movie = _movie(tmp_path)
    with pytest.raises(LLMError):
        extract_scene_events(llm, [Frame(ts=0.0, jpeg=b"x")], movie.scenes[0])
