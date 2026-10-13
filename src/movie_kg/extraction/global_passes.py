from pydantic import BaseModel

from movie_kg.models.extraction import SceneExtraction
from movie_kg.models.scene import MovieData
from movie_kg.prompts import load_prompt, render


class VtInfo(BaseModel):
    name: str
    scene_ids: list[int]


class OrderedVtRaw(BaseModel):
    name: str
    description: str = ""


class B1Output(BaseModel):
    ordered: list[OrderedVtRaw]


class OrderedVt(BaseModel):
    name: str
    timeline_index: int
    description: str


class NameContext(BaseModel):
    name: str
    aliases: list[str] = []
    context: str = ""


class MergeGroup(BaseModel):
    canonical: str
    aliases: list[str] = []
    description: str = ""


class AliasPlan(BaseModel):
    characters: list[MergeGroup] = []
    locations: list[MergeGroup] = []

    def resolve(self, kind: str, name: str) -> str:
        field = {"character": "characters", "location": "locations"}[kind]
        for g in getattr(self, field):
            if name == g.canonical or name in g.aliases:
                return g.canonical
        return name


class MergeGroupRaw(BaseModel):
    canonical: str
    aliases: list[str] = []
    description: str = ""


class B2Output(BaseModel):
    characters: list[MergeGroupRaw] = []
    locations: list[MergeGroupRaw] = []


class EventPair(BaseModel):
    cause_id: str
    cause_topic: str
    effect_id: str
    effect_topic: str


class CausalLinkRaw(BaseModel):
    cause_id: str
    effect_id: str
    reason: str


class B3Output(BaseModel):
    causal_links: list[CausalLinkRaw]


class CausalLink(BaseModel):
    cause_id: str
    effect_id: str
    reason: str


def _text_model(llm) -> str:
    cfg = getattr(llm, "cfg", None)
    return cfg.text_model if cfg is not None else "text"


def collect_vts(movie: MovieData) -> list[VtInfo]:
    seen: dict[str, VtInfo] = {}
    for scene in movie.scenes:
        seq = int(scene.meta.scene_id.rsplit("_", 1)[1])
        name = scene.meta.virtual_time
        seen.setdefault(name, VtInfo(name=name, scene_ids=[])).scene_ids.append(seq)
    return list(seen.values())


def collect_name_contexts(movie: MovieData,
                          extractions: list[SceneExtraction]
                          ) -> tuple[list[NameContext], list[NameContext]]:
    chars: dict[str, NameContext] = {}
    for scene in movie.scenes:
        for c in scene.meta.characters:
            ctx = chars.setdefault(c.name, NameContext(name=c.name,
                                                       aliases=list(c.aliases)))
            for a in c.aliases:
                if a not in ctx.aliases:
                    ctx.aliases.append(a)
    for ext in extractions:
        for ev in ext.events:
            for p in ev.participants:
                if p.name.startswith("未识别:"):
                    raw = p.name.split(":", 1)[1]
                    chars.setdefault(raw, NameContext(name=raw))
    locs = {s.meta.location: NameContext(name=s.meta.location,
                                         context=f"出现于 {s.meta.scene_id}")
            for s in movie.scenes}
    for ctx in chars.values():
        if not ctx.context:
            ctx.context = f"参与 {len(movie.scenes)} 个场景的剧情"
    return list(chars.values()), list(locs.values())


def pass_b1_order_vts(llm, vts: list[VtInfo]) -> list[OrderedVt]:
    vts_text = "\n".join(f"- {v.name}(出现于场景 {v.scene_ids})" for v in vts)
    system = render(load_prompt("vt_ordering"), VTS=vts_text)
    raw = llm.chat_json(model=_text_model(llm),
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": "请输出排序 JSON。"}],
                        schema=B1Output)
    ordered_names = [o.name for o in raw.ordered]
    known = {v.name for v in vts}
    ordered_names += [v.name for v in vts if v.name not in ordered_names]
    desc = {o.name: o.description for o in raw.ordered}
    out = []
    for i, name in enumerate(ordered_names):
        if name in known:
            out.append(OrderedVt(name=name, timeline_index=i,
                                 description=desc.get(name, "") or name))
    return out


def pass_b2_merge_aliases(llm, characters: list[NameContext],
                          locations: list[NameContext]) -> AliasPlan:
    def fmt(items):
        return "\n".join(f"- {i.name}(别名: {', '.join(i.aliases) or '无'}; {i.context})"
                         for i in items) or "(无)"
    system = render(load_prompt("alias_merge"),
                    CHARACTERS=fmt(characters), LOCATIONS=fmt(locations))
    raw = llm.chat_json(model=_text_model(llm),
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": "请输出归并 JSON。"}],
                        schema=B2Output)
    plan = AliasPlan(characters=[MergeGroup(**g.model_dump()) for g in raw.characters],
                     locations=[MergeGroup(**g.model_dump()) for g in raw.locations])
    for nc in characters:
        if nc.name not in {g.canonical for g in plan.characters} and not any(
                nc.name in g.aliases for g in plan.characters):
            plan.characters.append(MergeGroup(canonical=nc.name,
                                              aliases=nc.aliases, description=""))
    for nc in locations:
        if nc.name not in {g.canonical for g in plan.locations} and not any(
                nc.name in g.aliases for g in plan.locations):
            plan.locations.append(MergeGroup(canonical=nc.name, description=""))
    return plan


def _mains(ext: SceneExtraction) -> list:
    return [e for e in ext.events if not e.is_subevent]


def build_causal_pairs(movie: MovieData,
                       extractions: list[SceneExtraction]) -> list[EventPair]:
    by_scene = {ext.scene_id: _mains(ext) for ext in extractions}
    pairs: list[EventPair] = []
    scene_ids = [s.meta.scene_id for s in movie.scenes]
    for sid in scene_ids:
        mains = by_scene.get(sid, [])
        for a, b in zip(mains, mains[1:]):
            pairs.append(EventPair(cause_id=a.id, cause_topic=a.topic,
                                   effect_id=b.id, effect_topic=b.topic))
    for prev_sid, next_sid in zip(scene_ids, scene_ids[1:]):
        prev_mains, next_mains = by_scene.get(prev_sid, []), by_scene.get(next_sid, [])
        if prev_mains and next_mains:
            a, b = prev_mains[-1], next_mains[0]
            pairs.append(EventPair(cause_id=a.id, cause_topic=a.topic,
                                   effect_id=b.id, effect_topic=b.topic))
    return pairs


def pass_b3_causal(llm, pairs: list[EventPair]) -> list[CausalLink]:
    pairs_text = "\n".join(
        f"- {p.cause_id}({p.cause_topic}) -> {p.effect_id}({p.effect_topic})"
        for p in pairs)
    system = render(load_prompt("causal_detection"), PAIRS=pairs_text)
    raw = llm.chat_json(model=_text_model(llm),
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": "请输出因果 JSON。"}],
                        schema=B3Output)
    valid = {(p.cause_id, p.effect_id) for p in pairs}
    return [CausalLink(cause_id=l.cause_id, effect_id=l.effect_id, reason=l.reason)
            for l in raw.causal_links if (l.cause_id, l.effect_id) in valid]
