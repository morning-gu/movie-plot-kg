import json
from pathlib import Path

from movie_kg.ingestion.timefmt import parse_ts
from movie_kg.models.scene import KeyframeMeta, MovieData, SceneData, SceneMeta


class InputValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _check_keyframe(k: KeyframeMeta, scene_id: str, start: float, end: float,
                    seen_frames: set[str], errors: list[str]) -> None:
    """顶层与 shot 内嵌关键帧共用的查重 + 窗口校验(DESIGN §3.2/§3.3)。"""
    if k.frame_id in seen_frames:
        errors.append(f"{scene_id}: frame_id 重复({k.frame_id})")
    seen_frames.add(k.frame_id)
    if not (start <= parse_ts(k.ts) <= end):
        errors.append(f"{scene_id}: 关键帧越界({k.frame_id}: {k.ts})")


def load_movie(movie_dir: str | Path) -> MovieData:
    movie_dir = Path(movie_dir)
    errors: list[str] = []
    movie_id = movie_dir.name
    info = json.loads((movie_dir / "movie.json").read_text(encoding="utf-8"))
    scene_files = sorted((movie_dir / "scenes").glob("scene_*.json"))
    frames_dir = movie_dir / "frames"
    scenes: list[SceneData] = []
    prev_end = -1.0
    seen_shots: set[str] = set()
    seen_frames: set[str] = set()
    for sf in scene_files:
        meta = SceneMeta.model_validate_json(sf.read_text(encoding="utf-8"))
        if meta.movie_id != movie_id:
            errors.append(f"{sf.name}: movie_id={meta.movie_id} 与目录 {movie_id} 不一致")
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
                _check_keyframe(k, meta.scene_id, start, end, seen_frames, errors)
        for k in meta.keyframes:  # v0.4.0 顶层帧,同规则
            _check_keyframe(k, meta.scene_id, start, end, seen_frames, errors)
        intervals = sorted((parse_ts(sh.start_ts), parse_ts(sh.end_ts), sh.shot_id)
                           for sh in meta.shots)
        for (_, e0, _), (s1, _, id1) in zip(intervals, intervals[1:]):
            if s1 < e0:
                errors.append(f"{meta.scene_id}: 镜头重叠({id1})")
        for k in meta.flat_keyframes():
            fp = frames_dir / f"{k.frame_id}.jpg"
            if not fp.exists():
                errors.append(f"{meta.scene_id}: 缺少帧文件({fp.name})")
            elif fp.stat().st_size == 0:
                errors.append(f"{meta.scene_id}: 帧文件为空({fp.name})")
        scenes.append(SceneData(meta=meta, frames_dir=frames_dir))
    if errors:
        raise InputValidationError(errors)
    return MovieData(movie_id=movie_id, title=info.get("title", movie_id), scenes=scenes)
