from pathlib import Path

import av
from PIL import Image


def make_video(path: Path, seconds: int = 2, fps: int = 12) -> None:
    """生成逐帧变色的合成视频,供帧采样与端到端测试使用。"""
    container = av.open(str(path), "w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
    for i in range(seconds * fps):
        img = Image.new("RGB", (64, 48), ((i * 7) % 256, (i * 13) % 256, (i * 29) % 256))
        for packet in stream.encode(av.VideoFrame.from_image(img)):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def make_movie_dir(tmp_path: Path, n_scenes: int = 2) -> Path:
    """构造完整的单部电影输入目录(视频/元数据),返回目录路径。"""
    import json

    movie_dir = tmp_path / "data" / "movies" / "m1"
    for sub in ("video", "scenes", "checkpoints"):
        (movie_dir / sub).mkdir(parents=True, exist_ok=True)
    (movie_dir / "movie.json").write_text(
        json.dumps({"movie_id": "m1", "title": "测试影片"}), encoding="utf-8")
    windows = [("00:00:00", "00:00:02"), ("00:00:02", "00:00:04"),
               ("00:00:04", "00:00:06")]
    base = {
        "characters": [{"name": "张三", "aliases": ["老张"]},
                       {"name": "李四", "aliases": []}],
        "location": "码头仓库", "virtual_time": "第一天傍晚",
        "description": "张三与李四在仓库对峙。",
        "props": ["枪"],
    }
    for i in range(n_scenes):
        seq = i + 1
        sid = f"scene_{seq:03d}"
        start, end = windows[i]
        dlg_ts = f"00:00:{2 * i + 1:02d}"  # 每场景窗口中点附近
        dialogues = [
            {"char": "张三", "text": "你到底想怎样?", "ts": dlg_ts,
             "emotion": "愤怒", "action": "拍桌"},
            {"char": "李四", "text": "我只是来谈生意的。", "ts": dlg_ts},
        ]
        m = dict(base, dialogues=dialogues, scene_id=sid, movie_id="m1",
                 start_ts=start, end_ts=end,
                 location="码头仓库" if i == 0 else "警局办公室",
                 virtual_time="第一天傍晚" if i == 0 else "第二天清晨")
        if i == 0:  # 仅 scene_001 带镜头/标注帧;scene_002 测无标注兜底
            m["shots"] = [
                {"shot_id": "shot_001", "start_ts": "00:00:00", "end_ts": "00:00:01",
                 "shot_type": "中景", "description": "两人隔桌对坐",
                 "keyframes": [
                     {"frame_id": "frame_001", "ts": "00:00:01", "frame_type": "特写",
                      "description": "张三的手按在枪上"}]},
                {"shot_id": "shot_002", "start_ts": "00:00:01", "end_ts": "00:00:02",
                 "shot_type": "全景", "description": "仓库全貌",
                 "keyframes": []},
            ]
        (movie_dir / "scenes" / f"{sid}.json").write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8")
        make_video(movie_dir / "video" / f"{sid}.mp4", seconds=2)
    return movie_dir
