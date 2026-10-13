import json

from movie_kg.extraction.parse import parse_a2
from movie_kg.models.extraction import A2Output, SceneEvent
from movie_kg.models.scene import CharacterMeta
from movie_kg.prompts import load_prompt, render


def _events_text(events: list[SceneEvent]) -> str:
    mains = [e for e in events if not e.is_subevent]
    return "\n".join(
        f"[{i}] {e.topic}: {e.description}("
        + ",".join(p.name for p in e.participants) + ")"
        for i, e in enumerate(mains))


def extract_scene_relations(llm, events: list[SceneEvent],
                            roster: list[CharacterMeta],
                            prior_relations: list[dict]) -> list:
    """A2: 事件摘要 + 已有关系表 -> 本场景关系(新/变更)。"""
    priors = "\n".join(
        f"{p['char_a']} - {p['relation_type']} - {p['char_b']}" for p in prior_relations
    ) or "(暂无)"
    system = render(load_prompt("relation_update"),
                    PRIORS=priors, EVENTS=_events_text(events))
    cfg = getattr(llm, "cfg", None)
    model = cfg.vision_model if cfg is not None else "vision"
    raw = llm.chat_json(model=model, messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": "请输出本场景角色关系 JSON。"}],
        schema=A2Output)
    mains = [e for e in events if not e.is_subevent]
    return parse_a2(json.loads(raw.model_dump_json()), mains, roster)
