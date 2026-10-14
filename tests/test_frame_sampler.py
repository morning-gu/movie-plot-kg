from movie_kg.ingestion.frame_sampler import select_frames
from movie_kg.models.scene import KeyframeMeta
from tests.fixtures import make_frame


def kf(fid, ts, desc="d"):
    return KeyframeMeta(frame_id=fid, ts=ts, description=desc)


def _items(tmp_path, kfs):
    items = []
    for k in kfs:
        p = tmp_path / f"{k.frame_id}.jpg"
        make_frame(p)
        items.append((p, k))
    return items


def test_returns_all_when_under_budget(tmp_path):
    items = _items(tmp_path, [kf("f1", "00:00:01"), kf("f2", "00:00:02")])
    frames = select_frames(items, max_frames=16)
    assert len(frames) == 2                       # 无补帧:目录里有多少给多少
    assert all(f.keyframe is not None for f in frames)


def test_even_truncation_keeps_both_ends(tmp_path):
    kfs = [kf(f"f{i:02d}", f"00:00:{i:02d}") for i in range(1, 21)]  # 20 帧
    frames = select_frames(_items(tmp_path, kfs), max_frames=6)
    assert len(frames) == 6
    assert frames[0].keyframe.frame_id == "f01"   # 首帧保留
    assert frames[-1].keyframe.frame_id == "f20"  # 尾帧保留(而非取前 6)


def test_output_sorted_by_ts(tmp_path):
    kfs = [kf("f3", "00:00:30"), kf("f1", "00:00:10"), kf("f2", "00:00:20")]
    frames = select_frames(_items(tmp_path, kfs), max_frames=16)
    assert [f.keyframe.frame_id for f in frames] == ["f1", "f2", "f3"]


def test_frame_bytes_and_absolute_ts_loaded(tmp_path):
    items = _items(tmp_path, [kf("f1", "00:10:01")])
    frames = select_frames(items, max_frames=16)
    assert frames[0].jpeg[:2] == b"\xff\xd8"      # JPEG magic
    assert abs(frames[0].ts - 601.0) < 1e-9        # 影片绝对秒,无偏移换算
