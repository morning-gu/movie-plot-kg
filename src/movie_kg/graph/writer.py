import hashlib

from movie_kg.extraction.global_passes import AliasPlan, CausalLink, OrderedVt
from movie_kg.models.extraction import ResolvedRelation, SceneExtraction
from movie_kg.models.scene import SceneMeta


def _md5(name: str) -> str:
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:8]


def char_id(movie_id: str, name: str) -> str:
    return f"char_{movie_id}_{_md5(name)}"


def vt_id(movie_id: str, name: str) -> str:
    return f"vt_{movie_id}_{_md5(name)}"


def loc_id(movie_id: str, name: str) -> str:
    return f"loc_{movie_id}_{_md5(name)}"


class GraphWriter:
    """C1: 按 checkpoint 顺序幂等写图(MERGE on id)。"""

    def __init__(self, session, alias_plan: AliasPlan | None = None):
        self.session = session
        self.plan = alias_plan

    def _canon(self, kind: str, name: str) -> str:
        return self.plan.resolve(kind, name) if self.plan else name

    def ensure_movie(self, movie_id: str, title: str) -> None:
        self.session.run(
            "MERGE (m:Movie {id: $id}) SET m.title = $title", id=movie_id, title=title)

    def ensure_scene(self, meta: SceneMeta) -> None:
        seq = int(meta.scene_id.rsplit("_", 1)[1])
        self.session.run(
            "MERGE (s:Scene {id: $id}) SET s.seq = $seq, s.start_ts = $start, "
            "s.end_ts = $end, s.annotated_location = $loc, s.annotated_time = $vt, "
            "s.movie_id = $movie",
            id=meta.scene_id, seq=seq, start=meta.start_ts, end=meta.end_ts,
            loc=meta.location, vt=meta.virtual_time, movie=meta.movie_id)
        self.session.run(
            "MATCH (s:Scene {id: $sid}), (m:Movie {id: $mid}) "
            "MERGE (s)-[:BELONGS_TO]->(m)", sid=meta.scene_id, mid=meta.movie_id)

    def write_extraction(self, ext: SceneExtraction, meta: SceneMeta) -> None:
        seq = int(meta.scene_id.rsplit("_", 1)[1])
        vt = vt_id(meta.movie_id, meta.virtual_time)
        loc = loc_id(meta.movie_id, self._canon("location", meta.location))
        self.session.run(
            "MERGE (v:VirtualTime {id: $id}) ON CREATE SET v.name = $name, "
            "v.description = '', v.movie_id = $movie", id=vt, name=meta.virtual_time,
            movie=meta.movie_id)
        self.session.run(
            "MERGE (l:Location {id: $id}) ON CREATE SET l.name = $name, "
            "l.aliases = [], l.description = '', l.movie_id = $movie", id=loc,
            name=self._canon("location", meta.location), movie=meta.movie_id)
        for ev in ext.events:
            go = seq * 1000 + ev.order
            self.session.run(
                "MERGE (e:Event {id: $id}) SET e.event_type = $type, e.topic = $topic, "
                "e.description = $desc, e.scene_id = $sid, e.order = $order, "
                "e.global_order = $global_order, e.is_subevent = $sub, e.movie_id = $movie_id",
                id=ev.id, type=ev.event_type, topic=ev.topic, desc=ev.description,
                sid=ev.scene_id, order=ev.order, global_order=go, sub=ev.is_subevent,
                movie_id=ev.movie_id)
            self.session.run(
                "MATCH (e:Event {id: $eid}), (s:Scene {id: $sid}) "
                "MERGE (e)-[:SCENE_OF]->(s)", eid=ev.id, sid=ev.scene_id)
            self.session.run(
                "MATCH (e:Event {id: $eid}), (m:Movie {id: $mid}) "
                "MERGE (e)-[:BELONGS_TO]->(m)", eid=ev.id, mid=ev.movie_id)
            self.session.run(
                "MATCH (e:Event {id: $eid}), (v:VirtualTime {id: $vid}) "
                "MERGE (e)-[:OCCURS_AT]->(v)", eid=ev.id, vid=vt)
            self.session.run(
                "MATCH (e:Event {id: $eid}), (l:Location {id: $lid}) "
                "MERGE (e)-[:LOCATED_AT]->(l)", eid=ev.id, lid=loc)
            if ev.parent_id:
                self.session.run(
                    "MATCH (s:Event {id: $sid}), (p:Event {id: $pid}) "
                    "MERGE (s)-[:SUBEVENT_OF]->(p)", sid=ev.id, pid=ev.parent_id)
            for p in ev.participants:
                cname = self._canon("character", p.name.split(":", 1)[-1]
                                    if p.name.startswith("未识别:") else p.name)
                cid = char_id(meta.movie_id, cname)
                self.session.run(
                    "MERGE (c:Character {id: $id}) ON CREATE SET c.name = $name, "
                    "c.aliases = [], c.description = '', c.movie_id = $movie",
                    id=cid, name=cname, movie=meta.movie_id)
                self.session.run(
                    "MATCH (c:Character {id: $cid}), (m:Movie {id: $mid}) "
                    "MERGE (c)-[:BELONGS_TO]->(m)", cid=cid, mid=meta.movie_id)
                self.session.run(
                    "MATCH (c:Character {id: $cid}), (e:Event {id: $eid}) "
                    "MERGE (c)-[r:PARTICIPATES_IN]->(e) "
                    "SET r.emotion = $emo, r.role_description = $role, r.items = $items",
                    cid=cid, eid=ev.id, emo=p.emotion, role=p.role_description,
                    items=p.items)

    def write_precedes_chain(self, extractions: list[SceneExtraction],
                             metas: list[SceneMeta]) -> None:
        by_scene = {ext.scene_id: [e for e in ext.events if not e.is_subevent]
                    for ext in extractions}
        vt_by_scene = {m.scene_id: m.virtual_time for m in metas}
        ordered_ids = [m.scene_id for m in metas]
        edges: list[tuple[str, str, str | None]] = []
        for sid in ordered_ids:
            mains = by_scene.get(sid, [])
            for a, b in zip(mains, mains[1:]):
                edges.append((a.id, b.id, None))
        for prev, nxt in zip(ordered_ids, ordered_ids[1:]):
            pm, nm = by_scene.get(prev, []), by_scene.get(nxt, [])
            if pm and nm:
                edges.append((pm[-1].id, nm[0].id, vt_by_scene.get(nxt)))
        for a, b, gap in edges:
            self.session.run(
                "MATCH (a:Event {id: $a}), (b:Event {id: $b}) "
                "MERGE (a)-[r:PRECEDES]->(b) SET r.gap = $gap", a=a, b=b, gap=gap)

    def write_relations(self, relations: list[ResolvedRelation], movie_id: str) -> None:
        for r in relations:
            a = char_id(movie_id, self._canon("character", r.char_a))
            b = char_id(movie_id, self._canon("character", r.char_b))
            if not r.is_new:
                self.session.run(
                    "MATCH (a:Character {id: $a})-[old:RELATED_TO]-(b:Character {id: $b}) "
                    "WHERE old.valid = true "
                    "SET old.valid = false, old.end_event_id = $eid",
                    a=a, b=b, eid=r.start_event_id)
            self.session.run(
                "MATCH (a:Character {id: $a}), (b:Character {id: $b}) "
                "MERGE (a)-[rel:RELATED_TO {start_event_id: $sid}]->(b) "
                "SET rel.relation_type = $rtype, rel.valid = true",
                a=a, b=b, sid=r.start_event_id, rtype=r.relation_type)

    def apply_vt_order(self, ordered: list[OrderedVt], movie_id: str) -> None:
        for o in ordered:
            self.session.run(
                "MATCH (vt:VirtualTime {id: $id}) SET vt.timeline_index = $idx, "
                "vt.description = $desc", id=vt_id(movie_id, o.name),
                idx=o.timeline_index, desc=o.description)

    def write_causal_links(self, links: list[CausalLink]) -> None:
        for l in links:
            self.session.run(
                "MATCH (c:Event {id: $cid}), (e:Event {id: $eid}) "
                "MERGE (c)-[r:CAUSES]->(e) SET r.reason = $reason",
                cid=l.cause_id, eid=l.effect_id, reason=l.reason)
