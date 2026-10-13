import logging

from movie_kg.models.extraction import (A1Output, A2Output, ExtractedEvent,
                                        Participant, ResolvedRelation,
                                        SceneEvent)
from movie_kg.models.scene import CharacterMeta, SceneMeta

logger = logging.getLogger(__name__)


def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def align_name(name: str, roster: list[CharacterMeta]) -> str:
    """名单精确/别名/模糊(编辑距离≤2 或包含)对齐;失败保留 '未识别:' 前缀。"""
    table = {c.name: c.name for c in roster}
    for c in roster:
        for alias in c.aliases:
            table[alias] = c.name
    if name in table:
        return table[name]

    def close(a: str, b: str) -> bool:
        d = levenshtein(a, b)
        return (d <= 1
                or (d <= 2 and min(len(a), len(b)) >= 3)
                or (len(a) > 1 and len(b) > 1 and (a in b or b in a)))

    for c in roster:
        if close(name, c.name) or any(close(name, a) for a in c.aliases):
            return c.name
    return f"未识别:{name}"


def parse_a1(raw: dict, meta: SceneMeta) -> list[SceneEvent]:
    """A1 原始输出 -> 扁平化 SceneEvent 列表(ID 确定性分配,子事件紧随父事件)。"""
    out = A1Output.model_validate(raw)
    seq = int(meta.scene_id.rsplit("_", 1)[1])
    events: list[SceneEvent] = []
    counter = 0

    def make(ev: ExtractedEvent, parent_id: str | None) -> SceneEvent:
        nonlocal counter
        counter += 1
        return SceneEvent(
            id=f"evt_{meta.movie_id}_{seq:03d}_{counter:02d}",
            event_type=ev.event_type, topic=ev.topic, description=ev.description,
            scene_id=meta.scene_id, movie_id=meta.movie_id, order=counter,
            is_subevent=parent_id is not None, parent_id=parent_id,
            participants=[Participant.model_validate(
                p.model_copy(update={"name": align_name(p.name, meta.characters)}))
                for p in ev.participating_characters])

    for ev in out.events:
        main = make(ev, None)
        events.append(main)
        for sub in ev.subevents:
            if sub.subevents:
                logger.warning(
                    "场景 %s: 子事件「%s」存在 %d 个嵌套子事件,超出单层限制,已跳过",
                    meta.scene_id, sub.topic, len(sub.subevents))
            events.append(make(sub, main.id))
    return events


def parse_a2(raw: dict, main_events: list[SceneEvent],
             roster: list[CharacterMeta]) -> list[ResolvedRelation]:
    """A2 原始输出 -> 关系列表;evidence_event_index(主事件 0 起)映射为 start_event_id。"""
    out = A2Output.model_validate(raw)
    relations: list[ResolvedRelation] = []
    for r in out.character_relations:
        if not (0 <= r.evidence_event_index < len(main_events)):
            raise ValueError(f"evidence_event_index 越界: {r.evidence_event_index}")
        relations.append(ResolvedRelation(
            char_a=align_name(r.char_a, roster), char_b=align_name(r.char_b, roster),
            relation_type=r.relation_type, is_new=r.is_new,
            change_description=r.change_description,
            start_event_id=main_events[r.evidence_event_index].id))
    return relations
