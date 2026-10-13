import pytest

from movie_kg.extraction.event_extractor import extract_scene_events
from movie_kg.ingestion.frame_sampler import Frame
from movie_kg.llm import LLMError
from movie_kg.models.scene import KeyframeMeta, MovieData
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


def test_prompt_contains_grounding_priors(tmp_path):
    from tests.fakes import ScriptedLLM
    llm = ScriptedLLM(A1)
    movie = _movie(tmp_path)
    frames = [Frame(ts=1.0, jpeg=b"\xff\xd8fake",
                     keyframe=KeyframeMeta(frame_id="frame_001", ts="00:00:01",
                                           frame_type="特写",
                                           description="按在枪上"))]
    extract_scene_events(llm, frames, movie.scenes[0])
    system = llm.calls[0][1][0]["content"]
    assert "场景描述: 张三与李四在仓库对峙。" in system
    assert "道具: 枪" in system
    assert "shot_001 [00:00:00–00:00:01] 中景: 两人隔桌对坐" in system
    assert "00:00:01 张三(愤怒/拍桌): 你到底想怎样?" in system
    assert "张三(老张)" in system                     # ROSTER 含别名渲染
    assert "码头仓库" in system and "第一天傍晚" in system  # LOCATION / TIME
    assert "<<" not in system                          # 全部占位符已替换
    user = llm.calls[0][1][-1]["content"]
    texts = [p["text"] for p in user if p.get("type") == "text"]
    assert texts[0] == "请分析以下帧序列与台词,输出 JSON。"
    assert "帧 frame_001 (00:00:01 特写): 按在枪上" in texts
    types = [p["type"] for p in user]
    assert types.index("text") < types.index("image_url")  # 图文交错


def test_fill_frame_marked(tmp_path):
    from tests.fakes import ScriptedLLM
    llm = ScriptedLLM(A1)
    movie = _movie(tmp_path)
    extract_scene_events(llm, [Frame(ts=0.5, jpeg=b"x")], movie.scenes[0])
    texts = [p["text"] for p in llm.calls[0][1][-1]["content"]
             if p.get("type") == "text"]
    assert "[补充帧 00:00:00]" in texts


def test_dialogue_single_field_cue_renders_without_none(tmp_path):
    from tests.fakes import ScriptedLLM
    from movie_kg.models.scene import Dialogue
    llm = ScriptedLLM(A1)
    movie = _movie(tmp_path)
    scene = movie.scenes[0]
    scene.meta.dialogues[0] = Dialogue(char="张三", text="……", ts="00:00:01",
                                       emotion="愤怒")
    extract_scene_events(llm, [Frame(ts=0.5, jpeg=b"x")], scene)
    system = llm.calls[0][1][0]["content"]
    assert "张三(愤怒): ……" in system
