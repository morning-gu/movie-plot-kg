import json
from pathlib import Path

from movie_kg.ingestion.srt import parse_srt, parse_ts
from movie_kg.models.scene import MovieData, SceneData, SceneMeta


class InputValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def load_movie(movie_dir: str | Path) -> MovieData:
    movie_dir = Path(movie_dir)
    errors: list[str] = []
    movie_id = movie_dir.name
    info = json.loads((movie_dir / "movie.json").read_text(encoding="utf-8"))
    scene_files = sorted((movie_dir / "scenes").glob("scene_*.json"))
    scenes: list[SceneData] = []
    prev_end = -1.0
    for sf in scene_files:
        meta = SceneMeta.model_validate_json(sf.read_text(encoding="utf-8"))
        if meta.movie_id != movie_id:
            errors.append(f"{sf.name}: movie_id={meta.movie_id} 与目录 {movie_id} 不一致")
        video = movie_dir / "video" / f"{meta.scene_id}.mp4"
        srt_file = movie_dir / "subtitles" / f"{meta.scene_id}.srt"
        if not video.exists():
            errors.append(f"{meta.scene_id}: 缺少视频 {video.name}")
        if not srt_file.exists():
            errors.append(f"{meta.scene_id}: 缺少字幕 {srt_file.name}")
        start, end = parse_ts(meta.start_ts), parse_ts(meta.end_ts)
        if start >= end:
            errors.append(f"{meta.scene_id}: 起止时间非法({meta.start_ts} >= {meta.end_ts})")
        if start < prev_end:
            errors.append(f"{meta.scene_id}: 场景窗口与上一场景重叠")
        prev_end = end
        roster = {c.name for c in meta.characters} | {a for c in meta.characters for a in c.aliases}
        for d in meta.dialogues:
            if d.char not in roster:
                errors.append(f"{meta.scene_id}: 台词角色 {d.char} 不在角色名单中")
        srt_text = srt_file.read_text(encoding="utf-8") if srt_file.exists() else ""
        for entry in parse_srt(srt_text):
            if parse_ts(entry.start) < start or parse_ts(entry.end) > end:
                errors.append(f"{meta.scene_id}: 字幕越界({entry.start})")
        scenes.append(SceneData(meta=meta, srt_text=srt_text, video_path=video))
    if errors:
        raise InputValidationError(errors)
    return MovieData(movie_id=movie_id, title=info.get("title", movie_id), scenes=scenes)
