from typing import Literal

from pydantic import BaseModel


class Participant(BaseModel):
    name: str
    emotion: str | None = None
    role_description: str | None = None
    items: list[str] = []


class ExtractedEvent(BaseModel):
    event_type: Literal["dialogue", "conflict", "action", "turning_point", "discovery"]
    topic: str
    description: str
    evidence_frame_ids: list[str] = []
    participating_characters: list[Participant] = []
    subevents: list["ExtractedEvent"] = []


class A1Output(BaseModel):
    events: list[ExtractedEvent]


class ExtractedRelation(BaseModel):
    char_a: str
    char_b: str
    relation_type: Literal["friend", "enemy", "lover", "family", "mentor", "other"]
    is_new: bool
    change_description: str | None = None
    evidence_event_index: int


class A2Output(BaseModel):
    character_relations: list[ExtractedRelation]


class SceneEvent(BaseModel):
    id: str
    event_type: str
    topic: str
    description: str
    scene_id: str
    movie_id: str
    order: int
    is_subevent: bool = False
    parent_id: str | None = None
    evidence_frame_ids: list[str] = []
    participants: list[Participant] = []


class ResolvedRelation(BaseModel):
    char_a: str
    char_b: str
    relation_type: str
    is_new: bool
    change_description: str | None = None
    start_event_id: str


class SceneExtraction(BaseModel):
    scene_id: str
    movie_id: str
    status: Literal["ok", "failed"]
    events: list[SceneEvent] = []
    relations: list[ResolvedRelation] = []
