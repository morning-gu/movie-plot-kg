from tests.fixtures import make_video
from movie_kg.ingestion.frame_sampler import sample_frames


def test_uniform_sampling_caps_at_16(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=4)
    frames = sample_frames(video, keyframes=[])
    assert len(frames) == 16
    assert all(f.jpeg[:2] == b"\xff\xd8" for f in frames)  # JPEG magic
    assert frames == sorted(frames, key=lambda f: f.ts)


def test_keyframe_priority_included(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=4)
    frames = sample_frames(video, keyframes=["00:00:01"])
    assert 15 <= len(frames) <= 16  # 关键帧落在均匀网格上时允许少 1 帧
    assert any(abs(f.ts - 1.0) <= 0.5 for f in frames)
    assert len({f.jpeg for f in frames}) > 1  # 采样点应解码出不同画面,而非同一帧


def test_small_budget(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=4)
    frames = sample_frames(video, keyframes=["00:00:01"], max_frames=4)
    assert 3 <= len(frames) <= 4
