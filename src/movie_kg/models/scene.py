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


class KeyframeMeta(BaseModel):
    frame_id: str
    ts: str
    frame_type: str | None = None
    description: str | None = None


class ShotMeta(BaseModel):
    shot_id: str
    start_ts: str
    end_ts: str
    shot_type: str | None = None
    description: str | None = None
    keyframes: list[KeyframeMeta] = []


class SceneMeta(BaseModel):
    scene_id: str
    movie_id: str
    start_ts: str
    end_ts: str
    characters: list[CharacterMeta]
    dialogues: list[Dialogue]
    location: str
    virtual_time: str
    description: str = ""
    props: list[str] = []
    shots: list[ShotMeta] = []
    keyframes: list[KeyframeMeta] = []  # v0.4.0: 顶层(无镜头归属)帧,MovieNet 模式

    def flat_keyframes(self) -> list[KeyframeMeta]:
        """shot 内嵌帧按序展开,再拼接顶层帧(采样与证据校验的基准列表)。"""
        return [k for sh in self.shots for k in sh.keyframes] + list(self.keyframes)


class SceneData(BaseModel):
    meta: SceneMeta
    frames_dir: Path


class MovieData(BaseModel):
    movie_id: str
    title: str
    scenes: list[SceneData]
