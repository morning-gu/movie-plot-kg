import json

import pytest

from movie_kg.ingestion.data_loader import InputValidationError, load_movie
from tests.fixtures import make_movie_dir


def _edit_scene(movie_dir, sid, mutate):
    p = movie_dir / "scenes" / f"{sid}.json"
    m = json.loads(p.read_text(encoding="utf-8"))
    mutate(m)
    p.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")


def test_load_movie_happy_path(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    movie = load_movie(movie_dir)
    assert movie.movie_id == "m1" and movie.title == "测试影片"
    assert [s.meta.scene_id for s in movie.scenes] == ["scene_001", "scene_002"]
    s1 = movie.scenes[0]
    assert s1.meta.characters[0].name == "张三"
    assert s1.frames_dir == movie_dir / "frames"
    assert [k.frame_id for k in s1.meta.flat_keyframes()] == ["frame_001"]
    assert s1.meta.shots[0].shot_type == "中景"
    assert s1.meta.description == "张三与李四在仓库对峙。"
    s2 = movie.scenes[1]
    assert [k.frame_id for k in s2.meta.flat_keyframes()] == ["frame_101"]  # 顶层帧


def test_missing_frame_file_reports_error(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    (movie_dir / "frames" / "frame_001.jpg").unlink()
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("缺少帧文件" in msg for msg in e.value.errors)


def test_empty_frame_file_reports_error(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    (movie_dir / "frames" / "frame_101.jpg").write_bytes(b"")
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("帧文件为空" in msg for msg in e.value.errors)


def test_dialogue_char_not_in_roster(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    _edit_scene(movie_dir, "scene_001",
                lambda m: m["dialogues"][0].update(char="王五"))
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("王五" in msg for msg in e.value.errors)


def test_dialogue_ts_outside_window(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    _edit_scene(movie_dir, "scene_001",
                lambda m: m["dialogues"][0].update(ts="00:09:00"))
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("台词时间越界" in msg for msg in e.value.errors)


def test_keyframe_ts_outside_window(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    _edit_scene(movie_dir, "scene_001",
                lambda m: m["shots"][0]["keyframes"][0].update(ts="00:00:05"))
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("关键帧越界" in msg for msg in e.value.errors)


def test_toplevel_keyframe_ts_outside_window(tmp_path):
    # 顶层帧与 shot 内嵌帧同规则:越界必须报错(Review Focus #1 同源校验)
    movie_dir = make_movie_dir(tmp_path)
    _edit_scene(movie_dir, "scene_002",
                lambda m: m["keyframes"][0].update(ts="00:09:00"))
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("关键帧越界" in msg for msg in e.value.errors)


def test_shot_overlap_reports_error(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    _edit_scene(movie_dir, "scene_001",
                lambda m: m["shots"][1].update(start_ts="00:00:00"))
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("镜头重叠" in msg for msg in e.value.errors)


def test_duplicate_frame_id_across_scenes(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    _edit_scene(movie_dir, "scene_002", lambda m: m.update(shots=[
        {"shot_id": "shot_009", "start_ts": "00:00:02", "end_ts": "00:00:04",
         "keyframes": [{"frame_id": "frame_001", "ts": "00:00:03"}]}]))
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("frame_id 重复" in msg for msg in e.value.errors)


def test_toplevel_keyframe_duplicate_with_shot_internal(tmp_path):
    # Review Focus #1:顶层与 shot 内嵌双源拼接后,frame_id 查重必须覆盖顶层
    movie_dir = make_movie_dir(tmp_path)
    _edit_scene(movie_dir, "scene_001",
                lambda m: m.update(keyframes=[
                    {"frame_id": "frame_001", "ts": "00:00:01"}]))
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("frame_id 重复" in msg for msg in e.value.errors)
