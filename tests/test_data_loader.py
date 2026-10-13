import pytest

from movie_kg.ingestion.data_loader import InputValidationError, load_movie
from tests.fixtures import make_movie_dir


def test_load_movie_happy_path(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    movie = load_movie(movie_dir)
    assert movie.movie_id == "m1" and movie.title == "测试影片"
    assert [s.meta.scene_id for s in movie.scenes] == ["scene_001", "scene_002"]
    s1 = movie.scenes[0]
    assert s1.meta.characters[0].name == "张三"
    assert "你到底想怎样?" in s1.srt_text
    assert s1.video_path.name == "scene_001.mp4"


def test_missing_video_reports_error(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    (movie_dir / "video" / "scene_001.mp4").unlink()
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("scene_001.mp4" in msg for msg in e.value.errors)


def test_dialogue_char_not_in_roster(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    import json
    p = movie_dir / "scenes" / "scene_001.json"
    m = json.loads(p.read_text(encoding="utf-8"))
    m["dialogues"][0]["char"] = "王五"
    p.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("王五" in msg for msg in e.value.errors)


def test_srt_outside_scene_window(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    (movie_dir / "subtitles" / "scene_001.srt").write_text(
        "1\n00:10:00,000 --> 00:10:05,000\n越界台词\n", encoding="utf-8")
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("00:10:00" in msg or "越界" in msg for msg in e.value.errors)
