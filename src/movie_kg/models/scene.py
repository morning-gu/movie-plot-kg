from pathlib import Path

from pydantic import BaseModel


class CharacterMeta(BaseModel):
    name: str
    aliases: list[str] = []


class Dialogue(BaseModel):
    char: str
    text: str
    ts: str
    emotion: str | None = None
    action: str | None = None


class SceneMeta(BaseModel):
    scene_id: str
    movie_id: str
    start_ts: str
    end_ts: str
    characters: list[CharacterMeta]
    dialogues: list[Dialogue]
    location: str
    virtual_time: str
    props: list[str] = []
    keyframes: list[str] = []


class SrtEntry(BaseModel):
    start: str
    end: str
    text: str


class SceneData(BaseModel):
    meta: SceneMeta
    srt_text: str
    video_path: Path

    model_config = {"arbitrary_types_allowed": True}


class MovieData(BaseModel):
    movie_id: str
    title: str
    scenes: list[SceneData]
