from movie_kg.ingestion.frame_sampler import sample_frames
from movie_kg.models.scene import KeyframeMeta
from tests.fixtures import make_video


def kf(fid, ts, desc="d"):
    return KeyframeMeta(frame_id=fid, ts=ts, description=desc)


def test_uniform_sampling_caps_at_16(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=4)
    frames = sample_frames(video, [], scene_start=0.0)
    assert len(frames) == 16
    assert all(f.jpeg[:2] == b"\xff\xd8" for f in frames)  # JPEG magic
    assert frames == sorted(frames, key=lambda f: f.ts)
    assert all(f.keyframe is None for f in frames)          # 补足帧无标注


def test_absolute_ts_converted_via_scene_start(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=4)
    frames = sample_frames(video, [kf("f1", "00:10:01")],
                           scene_start=600.0, max_frames=4)
    ann = [f for f in frames if f.keyframe is not None]
    assert ann and abs(ann[0].ts - 601.0) < 0.5   # 绝对时间保留,偏移正确换算
    assert all(f.ts >= 600.0 for f in frames)


def test_even_truncation_keeps_both_ends(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=20)
    kfs = [kf(f"f{i:02d}", f"00:00:{i:02d}") for i in range(1, 21)]  # 20 个标注帧
    frames = sample_frames(video, kfs, scene_start=0.0, max_frames=6)
    ann = [f for f in frames if f.keyframe is not None]
    assert len(frames) == 6 and len(ann) == 6   # 截断后不再均匀补足
    assert ann[0].keyframe.frame_id == "f01"    # 首帧保留
    assert ann[-1].keyframe.frame_id == "f20"   # 尾帧保留(而非取前 6)


def test_fill_frames_yield_to_annotated(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=4)
    # max_frames=4 时补足网格 step=1.0s,与标注帧 00:00:01 重合 → 标注帧胜出
    frames = sample_frames(video, [kf("f1", "00:00:01")], scene_start=0.0,
                           max_frames=4)
    ann = [f for f in frames if f.keyframe is not None]
    assert ann and ann[0].keyframe.frame_id == "f1"
    rounded = [round(f.ts, 1) for f in frames]
    assert len(set(rounded)) == len(rounded)     # 无同点重复帧


def test_small_budget(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=4)
    frames = sample_frames(video, [kf("f1", "00:00:01")], scene_start=0.0,
                           max_frames=4)
    assert 3 <= len(frames) <= 4
