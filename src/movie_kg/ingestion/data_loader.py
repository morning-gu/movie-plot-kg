import json
from pathlib import Path

import av
import av.error

from movie_kg.ingestion.timefmt import parse_ts
from movie_kg.models.scene import MovieData, SceneData, SceneMeta

_TOL = 2.0  # 片段时长与场景窗口的容差(秒,DESIGN §3.1)


class InputValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _probe_duration(video: Path) -> float:
    with av.open(str(video)) as container:
        return (container.duration or 0) / 1_000_000.0


def load_movie(movie_dir: str | Path) -> MovieData:
    movie_dir = Path(movie_dir)
    errors: list[str] = []
    movie_id = movie_dir.name
    info = json.loads((movie_dir / "movie.json").read_text(encoding="utf-8"))
    scene_files = sorted((movie_dir / "scenes").glob("scene_*.json"))
    scenes: list[SceneData] = []
    prev_end = -1.0
    seen_shots: set[str] = set()
    seen_frames: set[str] = set()
    for sf in scene_files:
        meta = SceneMeta.model_validate_json(sf.read_text(encoding="utf-8"))
        if meta.movie_id != movie_id:
            errors.append(f"{sf.name}: movie_id={meta.movie_id} 与目录 {movie_id} 不一致")
        video = movie_dir / "video" / f"{meta.scene_id}.mp4"
        if not video.exists():
            errors.append(f"{meta.scene_id}: 缺少视频 {video.name}")
        start, end = parse_ts(meta.start_ts), parse_ts(meta.end_ts)
        if start >= end:
            errors.append(f"{meta.scene_id}: 起止时间非法({meta.start_ts} >= {meta.end_ts})")
        if start < prev_end:
            errors.append(f"{meta.scene_id}: 场景窗口与上一场景重叠")
        prev_end = end
        roster = ({c.name for c in meta.characters}
                  | {a for c in meta.characters for a in c.aliases})
        for d in meta.dialogues:
            if d.char not in roster:
                errors.append(f"{meta.scene_id}: 台词角色 {d.char} 不在角色名单中")
            if not (start <= parse_ts(d.ts) <= end):
                errors.append(f"{meta.scene_id}: 台词时间越界({d.ts})")
        for sh in meta.shots:
            if sh.shot_id in seen_shots:
                errors.append(f"{meta.scene_id}: shot_id 重复({sh.shot_id})")
            seen_shots.add(sh.shot_id)
            s, e = parse_ts(sh.start_ts), parse_ts(sh.end_ts)
            if not (start <= s <= end and start <= e <= end):
                errors.append(
                    f"{meta.scene_id}: 镜头越界({sh.shot_id}: {sh.start_ts}-{sh.end_ts})")
            for k in sh.keyframes:
                if k.frame_id in seen_frames:
                    errors.append(f"{meta.scene_id}: frame_id 重复({k.frame_id})")
                seen_frames.add(k.frame_id)
                if not (start <= parse_ts(k.ts) <= end):
                    errors.append(f"{meta.scene_id}: 关键帧越界({k.frame_id}: {k.ts})")
        intervals = sorted((parse_ts(sh.start_ts), parse_ts(sh.end_ts), sh.shot_id)
                           for sh in meta.shots)
        for (_, e0, _), (s1, _, id1) in zip(intervals, intervals[1:]):
            if s1 < e0:
                errors.append(f"{meta.scene_id}: 镜头重叠({id1})")
        if video.exists():
            try:
                dur = _probe_duration(video)
            except av.error.FFmpegError:
                errors.append(f"{meta.scene_id}: 视频无法打开({video.name})")
            else:
                if abs(dur - (end - start)) > _TOL:
                    errors.append(
                        f"{meta.scene_id}: 片段时长 {dur:.1f}s 与场景窗口 "
                        f"{end - start:.1f}s 偏差超过 {_TOL:.0f}s")
        scenes.append(SceneData(meta=meta, video_path=video))
    if errors:
        raise InputValidationError(errors)
    return MovieData(movie_id=movie_id, title=info.get("title", movie_id), scenes=scenes)
