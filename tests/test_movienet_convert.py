import io
import json
import tarfile

import pytest

from movie_kg.ingestion.data_loader import load_movie
from movie_kg.ingestion.movienet.convert import (
    ConvertError, calibrate_fps, convert, keyframe_entries, scene_dialogues,
    scene_windows)

SRT = """1
00:00:00,500 --> 00:00:01,200
You can't handle the truth.

2
00:00:01,500 --> 00:00:01,900
I was just here to talk.

3
00:00:02,000 --> 00:00:02,400
Boundary line.

4
00:00:03,500 --> 00:00:03,900
The money is gone.

5
00:00:03,700 --> 00:00:04,100
This line is nowhere in the script.
"""

SCRIPT = """EXT. OFFICE - DAY

                    MATTY
          You can't handle the truth.

                    NED
          I was just here to talk.

                    NED
          Boundary line.

                    NED
          The money is gone.
"""


def _ann(story_shots=((0, 1),), story_dur=((0.0, 2.0),)):
    return {
        "imdb_id": "tt0000001",
        "cast": [
            {"id": "x1", "pid": "nm1", "shot_idx": 0, "img_idx": 0},
            {"id": "x2", "pid": "nm2", "shot_idx": 1, "img_idx": 0},
        ],
        "scene": [
            {"id": "s0", "shot": [0, 1], "frame": [0, 48],
             "place_tag": ["office", "room"], "action_tag": None},
            {"id": "s1", "shot": [2, 3], "frame": [48, 96],
             "place_tag": None, "action_tag": None},
        ],
        "story": [
            {"id": "st0", "shot": list(s), "frame": [0, 48], "duration": list(d),
             "consistency": 0.9, "description": "Opening scene in the office.",
             "subtitle": []}
            for s, d in zip(story_shots, story_dur)
        ],
        "cinematic_style": None,
    }


META = {"imdb_id": "tt0000001", "title": "Mini (2020)",
        "cast": [{"id": "nm1", "name": "Actor One", "character": "Matty Walker"},
                 {"id": "nm2", "name": "Actor Two", "character": "Ned Racine"}]}


def _make_src(tmp_path, ann=None):
    src = tmp_path / "src"
    for sub in ("annotation", "subtitle", "script", "meta"):
        (src / sub).mkdir(parents=True, exist_ok=True)
    (src / "annotation" / "tt0000001.json").write_text(
        json.dumps(ann or _ann()), encoding="utf-8")
    (src / "meta" / "tt0000001.json").write_text(
        json.dumps(META), encoding="utf-8")
    (src / "subtitle" / "tt0000001.srt").write_text(SRT, encoding="utf-8")
    (src / "script" / "tt0000001.script").write_text(SCRIPT, encoding="utf-8")
    (src / "keyframes").mkdir(exist_ok=True)
    with tarfile.open(src / "keyframes" / "tt0000001.tar", "w") as tf:
        for shot in range(4):
            for img in range(3):
                data = b"\xff\xd8\xff\xd9"  # 最小 JPEG(存在且非空即可)
                ti = tarfile.TarInfo(f"tt0000001/shot_{shot:04d}_img_{img}.jpg")
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
    return src


def test_calibrate_fps_median_and_tolerance():
    fps = calibrate_fps(_ann()["story"])
    assert abs(fps - 24.0) < 1e-9  # (48-0)/(2.0-0.0)
    bad = _ann(story_shots=((0, 1), (2, 3)), story_dur=((0.0, 2.0), (2.0, 4.4)))
    with pytest.raises(ConvertError):
        calibrate_fps(bad["story"])   # 24 与 ~21.8 离散度超 ±0.5%


def test_scene_windows_merges_subsecond_scene_backward():
    # 亚秒场景([84,90]@24fps → [3.5,3.75],整秒截断后 start==end,
    # loader 会报「起止时间非法」)并入前一场景,整部影片不再被拒
    ann = _ann()
    ann["scene"] = [
        {"id": "s0", "shot": [0, 1], "frame": [0, 84], "place_tag": None},
        {"id": "s1", "shot": [2, 2], "frame": [84, 90], "place_tag": ["alley"]},
        {"id": "s2", "shot": [3, 4], "frame": [90, 192], "place_tag": ["office"]},
    ]
    scenes, merged = scene_windows(ann, 24.0)
    assert merged == 1
    assert [sc["seq"] for sc in scenes] == [1, 2]   # 合并后重编号
    m = scenes[0]
    assert m["start"] == 0.0 and m["end"] == 3.75   # 终点延展到被并场景
    assert m["shot_span"] == (0, 2)                 # shot 上界延展
    assert m["place_tag"] == ["alley"]              # prev 无 place → 用被并者的
    n = scenes[1]
    assert n["start"] == 3.75 and n["end"] == 8.0
    assert n["shot_span"] == (3, 4) and n["place_tag"] == ["office"]


def test_scene_windows_merges_leading_subsecond_scene_forward():
    # 开头连续亚秒场景:逐个累积挂起,向前并入其后首个正常场景
    ann = _ann()
    ann["scene"] = [
        {"id": "s0", "shot": [0, 0], "frame": [0, 6], "place_tag": ["logo"]},
        {"id": "s1", "shot": [1, 1], "frame": [6, 12], "place_tag": None},
        {"id": "s2", "shot": [2, 3], "frame": [12, 96], "place_tag": None},
    ]
    scenes, merged = scene_windows(ann, 24.0)
    assert merged == 2
    assert [sc["seq"] for sc in scenes] == [1]
    sc = scenes[0]
    assert sc["start"] == 0.0 and sc["end"] == 4.0  # 起点/下界取挂起场景
    assert sc["shot_span"] == (0, 3)                # 下界 0(挂起),上界 3(本景)
    assert sc["place_tag"] == ["logo"]              # 本景无 place → 用挂起者的


def test_dialogue_cue_at_scene_boundary():
    # Review Focus #3:cue 起点恰在边界 → 归后一场景,不重复计入
    scenes, _ = scene_windows(_ann(), 24.0)
    per, stats = scene_dialogues(SRT, SCRIPT, scenes, ["Matty Walker", "Ned Racine"])
    assert [len(d) for d in per] == [2, 2]
    assert per[1][0].text == "Boundary line."      # 2.0s 的 cue 在 scene_002
    assert stats["no_script_match"] == 1 and stats["outside_scenes"] == 0


def test_keyframe_ts_clamped_into_window():
    # Review Focus #4:story 镜头插值越过场景窗口 → clamp 进窗口
    ann = _ann(story_shots=((0, 3),), story_dur=((0.0, 4.0),))
    ann["scene"][0]["frame"] = [0, 84]    # scene_001 窗口 [0, 3.5]
    ann["scene"][1]["frame"] = [84, 96]   # scene_002 窗口 [3.5, 4]
    scenes, _ = scene_windows(ann, 24.0)
    kfs = keyframe_entries(ann, scenes)
    # story 覆盖 shots[0,3] dur[0,4]: 均匀插值 t(k)=k+0.5
    assert kfs[0][3].ts == "00:00:01"     # shot1: t=1.5 ∈ [0,3.5] 未越界
    assert kfs[1][0].ts == "00:00:03"     # shot2: 插值 2.5 < 3.5 → clamp 到 3.5
    assert kfs[1][3].ts == "00:00:03"     # shot3: 插值 3.5 恰在窗口起点


def test_convert_end_to_end(tmp_path):
    src = _make_src(tmp_path)
    out = tmp_path / "data" / "movies" / "tt0000001"
    convert("tt0000001", src, out)
    movie = load_movie(out)               # stage 9 自校验通过才返回
    assert movie.movie_id == "tt0000001" and movie.title == "Mini (2020)"
    s1, s2 = movie.scenes
    assert [c.name for c in s1.meta.characters] == ["Matty Walker", "Ned Racine"]
    assert [d.char for d in s1.meta.dialogues] == ["Matty Walker", "Ned Racine"]
    assert s1.meta.location == "office / room"
    assert s1.meta.description == "Opening scene in the office."
    assert len(s1.meta.flat_keyframes()) == 6      # 2 shots × 3 imgs
    assert s1.meta.virtual_time == "" and s1.meta.shots == []
    # scene_002: cast 名单空,说话人并入(Ned Racine)
    assert [c.name for c in s2.meta.characters] == ["Ned Racine"]
    assert [d.text for d in s2.meta.dialogues] == ["Boundary line.", "The money is gone."]
    assert s2.meta.location == "" and s2.meta.description == ""
    assert len(list((out / "frames").glob("*.jpg"))) == 12
    report = (out / "convert_report.md").read_text(encoding="utf-8")
    assert "fps 标定: 24.0000" in report
    assert "剧本未匹配 1" in report
    assert "loader 校验: 通过" in report
    assert "亚秒场景合并: 0" in report              # e2e 场景均多秒,无合并
    assert "角色数: 2" in report
    assert "每场景中位 6, min 6, max 6" in report


def test_convert_missing_srt_raises_convert_error(tmp_path):
    # 源缺失 → ConvertError(带 fetch 提示),而非裸 FileNotFoundError
    src = _make_src(tmp_path)
    (src / "subtitle" / "tt0000001.srt").unlink()
    with pytest.raises(ConvertError, match="fetch --movie tt0000001"):
        convert("tt0000001", src, tmp_path / "out")


def test_convert_rejects_when_validation_fails(tmp_path):
    src = _make_src(tmp_path)   # 关键帧 tar 只有 shot 0-3,但场景引用 shot 0-3 之外
    ann = _ann()
    ann["scene"][1]["shot"] = [2, 9]
    (src / "annotation" / "tt0000001.json").write_text(
        json.dumps(ann), encoding="utf-8")
    out = tmp_path / "out"
    with pytest.raises(ConvertError):
        convert("tt0000001", src, out)


def test_boundary_shot_assigned_to_first_scene_only():
    # MovieNet scene.shot 为闭区间:相邻场景共享边界镜头,frame_id 只出一次
    ann = _ann()
    ann["scene"][0]["shot"] = [0, 1]
    ann["scene"][1]["shot"] = [1, 2]   # shot 1 共享
    scenes, _ = scene_windows(ann, 24.0)
    kfs = keyframe_entries(ann, scenes)
    ids1 = {k.frame_id for k in kfs[0]}
    ids2 = {k.frame_id for k in kfs[1]}
    assert "s0001i0" in ids1 and "s0001i0" not in ids2
    assert "s0002i0" in ids2
    assert len(ids1) == 6 and len(ids2) == 3
