import re
from typing import Literal

from pydantic import BaseModel

from movie_kg.graph.query_templates import execute_template
from movie_kg.qa.planner import RetrievalPlan, TargetEntity

INDEXES = [("event_vec", "Event"), ("character_vec", "Character"),
           ("virtualtime_vec", "VirtualTime"), ("location_vec", "Location")]

# temporal 窗口(spec v2.1):后续预算/停止信号/取回上限
WINDOW_BUDGET = 24
WINDOW_STOP_LOCATIONS = 2
WINDOW_WALK_LIMIT = 60


class Evidence(BaseModel):
    source: Literal["vector", "graph", "temporal_window"]
    node_id: str
    text: str
    score: float | None = None
    extra: dict = {}


def _node_text(node: dict) -> str:
    if "topic" in node:
        return f"[{node.get('event_type', '')}] {node.get('topic', '')}:{node.get('description', '')}"
    return f"{node.get('name', '')}:{node.get('description', '')}"


def vector_recall(session, llm, query: str, top_k: int = 10,
                  movie_id: str | None = None) -> list[Evidence]:
    vec = llm.embed([query])[0]
    out: list[Evidence] = []
    if movie_id is None:
        for index, label in INDEXES:
            for rec in session.run(
                    "CALL db.index.vector.queryNodes($index, $k, $vec) YIELD node, score",
                    index=index, k=top_k, vec=vec):
                node = dict(rec["node"])
                out.append(Evidence(source="vector", node_id=node.get("id", ""),
                                    text=_node_text(node), score=rec.get("score"),
                                    extra={"label": label}))
        return out
    # 片内精确扫描:不走全局 ANN(设计否决"先检索再过滤");成本 O(单片规模),
    # 与库内总片数无关。分数为 (1+cos)/2 重标度,与 ANN 裸 cosine 不可比。
    # 标签来自 INDEXES 常量而非用户输入,可安全内插。
    for _, label in INDEXES:
        cypher = (f"MATCH (n:{label} {{movie_id: $mid}}) "
                  "WHERE n.embedding IS NOT NULL "
                  "RETURN n, vector.similarity.cosine(n.embedding, $vec) AS score "
                  "ORDER BY score DESC LIMIT $k")
        for rec in session.run(cypher, mid=movie_id, k=top_k, vec=vec):
            node = dict(rec["n"])
            out.append(Evidence(source="vector", node_id=node.get("id", ""),
                                text=_node_text(node), score=rec.get("score"),
                                extra={"label": label}))
    return out


def bind_entities(session, targets: list[TargetEntity],
                  vector_hits: list[Evidence],
                  movie_id: str | None = None) -> dict[str, str]:
    """name_hint -> 节点 ID:精确(name/aliases)→ 词级包含 → 向量同类命中(名字须有词交集)。

    盲取首个同类向量命中会把 "John Mason" 绑到语义相近的 Goodspeed 上(试点实证),
    产出自环/错向证据;错绑比不绑更糟,各级都要求名字证据。
    movie_id 有值时前两级按片收窄(修跨片同名错绑:"未识别:群演"3 片全有);
    第三级吃的 vector_hits 在 scoped 模式下已是片内结果,无需再滤。"""
    bound: dict[str, str] = {}
    for t in targets:
        if movie_id is None:
            recs = list(session.run(
                f"MATCH (n:{t.type}) WHERE n.name = $h OR $h IN n.aliases "
                "RETURN n.id AS id LIMIT 1", h=t.name_hint))
        else:
            recs = list(session.run(
                f"MATCH (n:{t.type}) WHERE n.movie_id = $mid "
                "AND (n.name = $h OR $h IN n.aliases) "
                "RETURN n.id AS id LIMIT 1", h=t.name_hint, mid=movie_id))
        found = recs[0]["id"] if recs else None
        if found is None:
            # 词级包含:hint 的全部词都出现在候选名中(次序无关,
            # "John Mason" -> "John Patrick Mason");唯一命中才绑
            words = {w for w in t.name_hint.lower().replace(".", " ").split() if w}
            if movie_id is None:
                cands = session.run(
                    f"MATCH (n:{t.type}) RETURN n.id AS id, n.name AS name")
            else:
                cands = session.run(
                    f"MATCH (n:{t.type}) WHERE n.movie_id = $mid "
                    "RETURN n.id AS id, n.name AS name", mid=movie_id)
            # Event 节点无 name 属性(None),空名一律跳过
            hits = [c for c in cands if words and c["name"] and
                    words <= {w for w in c["name"].lower().replace(".", " ").split() if w}]
            if len(hits) == 1:
                found = hits[0]["id"]
        if found is None:
            hint_words = set(t.name_hint.lower().split())
            for hit in vector_hits:
                if hit.extra.get("label") == t.type:
                    name = hit.text.split(":", 1)[0].lower()
                    if hint_words & set(name.split()):
                        found = hit.node_id
                        break
        if found is not None:
            bound[t.name_hint] = found
    return bound


def temporal_window(session, hits: list[Evidence]) -> list[Evidence]:
    """ID 序时间窗(spec v2.1):锚点=胜组最高分命中,锚点残余+后续按片内全序。

    计票按 LOCATED_AT 多数,空名地点不计(Rock 有 301 事件的空名超级节点);
    地点仅作软停止信号;不走 PRECEDES 链(HK/Rock 各断 3 段)、不用 Scene
    节点(跨片污染)——事件 ID 内嵌(场景号,序号)即无断口全序,子事件
    免疫;窗口按锚点事件的 movie 前缀过滤,全局模式也不跨片。"""
    event_hits = [h for h in hits if h.extra.get("label") == "Event"]
    if not event_hits:
        return []
    loc_of = {r["id"]: (r.get("lid"), r.get("lname")) for r in session.run(
        "MATCH (e:Event) WHERE e.id IN $ids "
        "OPTIONAL MATCH (e)-[:LOCATED_AT]->(l:Location) "
        "RETURN e.id AS id, l.id AS lid, l.name AS lname",
        ids=[h.node_id for h in event_hits])}
    counts: dict[str, int] = {}
    for h in event_hits:
        lid, lname = loc_of.get(h.node_id, (None, None))
        if lid is not None and (lname or "").strip():
            counts[lid] = counts.get(lid, 0) + 1
    by_score = sorted(event_hits,
                      key=lambda h: h.score if h.score is not None else 0,
                      reverse=True)
    if counts:
        top = max(counts.values())
        tied = {lid for lid, n in counts.items() if n == top}
        winner = next(loc_of[h.node_id][0] for h in by_score
                      if loc_of.get(h.node_id, (None, None))[0] in tied)
        anchor = next(h for h in by_score
                      if loc_of.get(h.node_id, (None, None))[0] == winner)
    else:
        anchor = by_score[0]          # 全空名 → 退化 top-1(spec)
    m = re.match(r"evt_(tt\d+)_", anchor.node_id)
    if not m:
        return []
    parts = anchor.node_id.split("_")
    a_scn, a_ord = int(parts[2]), int(parts[3])
    a_lid = loc_of.get(anchor.node_id, (None, None))[0]
    rows = list(session.run(
        "MATCH (e:Event {movie_id: $mid}) "
        "WITH e, split(e.id, '_') AS p "
        "WITH e, toInteger(p[2]) AS scn, toInteger(p[3]) AS ord "
        "WHERE scn > $scn OR (scn = $scn AND ord > $ord) "
        "OPTIONAL MATCH (e)-[:LOCATED_AT]->(l:Location) "
        "RETURN e.id AS id, e.topic AS topic, e.description AS description, "
        "e.event_type AS event_type, scn, ord, l.id AS locid, l.name AS loc "
        "ORDER BY scn, ord LIMIT $lim",
        mid=m.group(1), scn=a_scn, ord=a_ord, lim=WINDOW_WALK_LIMIT))
    out: list[Evidence] = []
    new_locs: list[str] = []          # 有序去重:非空异组地点
    n_sub = 0
    for r in rows:
        if r["scn"] > a_scn:
            locid, locname = r.get("locid"), r.get("loc")
            if (locid and (locname or "").strip() and locid != a_lid
                    and locid not in new_locs):
                new_locs.append(locid)
                if len(new_locs) >= WINDOW_STOP_LOCATIONS:
                    break             # 第 2 个新地点:该行起全弃(设计原文)
            if n_sub >= WINDOW_BUDGET:
                break
            n_sub += 1
        out.append(Evidence(
            source="temporal_window", node_id=r["id"], score=None,
            text=(f"【时间序 {len(out) + 1}·场景{r['scn']}】"
                  f"[{r.get('event_type', '')}] {r.get('topic', '')}:"
                  f"{r.get('description', '')}"),
            extra={"label": "Event"}))
    return out


def retrieve(session, llm, plan: RetrievalPlan, top_k: int = 10) -> list[Evidence]:
    hits = vector_recall(session, llm, plan.rewritten_query, top_k,
                         movie_id=plan.movie_id)
    evidence: list[Evidence] = list(hits)
    bound = bind_entities(session, plan.target_entities, hits,
                          movie_id=plan.movie_id)

    def _resolve(v):
        # 参数值与 name_hint 不完全一致时(如缩写 "Mason"),按词级子集
        # 匹配已绑定实体;对不上则原样传递(模板查询自然落空并跳过)
        if not isinstance(v, str) or v in bound:
            return bound.get(v, v)
        vl = {w for w in v.lower().split() if w}
        for hint, nid in bound.items():
            if vl and vl <= {w for w in hint.lower().split() if w}:
                return nid
        return v

    for gq in plan.graph_queries[:2]:
        params = {k: _resolve(v) for k, v in gq.params.items()}
        try:
            # list(...) 立即消费: 真 driver 的 Result 是惰性的,迭代错误必须
            # 落在本 try 内,否则 DB 运行时错误会绕过授权的 catch 中断问答
            rows = list(execute_template(session, gq.template_id, params))
        except Exception:
            continue  # 模板参数不合法(如绑定失败)时跳过,不中断问答
        for row in rows:
            for value in row.values():
                # 三种形状:普通 dict(Mock/测试)、Neo4j Node、Neo4j Path(键名任意)。
                # 真 driver 返回 Node/Path 对象,isinstance(dict) 永假——图证据曾因此
                # 全部被静默丢弃,full 模式退化为纯向量(试点实证)
                if isinstance(value, dict) and "id" in value:
                    evidence.append(Evidence(source="graph", node_id=value["id"],
                                             text=_node_text(value),
                                             extra={"template": gq.template_id}))
                elif hasattr(value, "labels"):  # Neo4j Node
                    d = dict(value)
                    if d.get("id"):
                        evidence.append(Evidence(source="graph", node_id=d["id"],
                                                 text=_node_text(d),
                                                 extra={"template": gq.template_id}))
                elif hasattr(value, "nodes"):  # Neo4j Path
                    for node in value.nodes:
                        d = dict(node)
                        if d.get("id"):
                            evidence.append(Evidence(
                                source="graph", node_id=d["id"], text=_node_text(d),
                                extra={"template": gq.template_id}))
    # temporal 窗口(spec v2.1):intent 硬门,非 temporal 路径逐字节不变;
    # 窗口行覆盖同 node_id 旧行(时间序标记不被去重吃掉)后追加尾部
    if plan.intent == "temporal":
        window_rows = temporal_window(session, hits)
        if window_rows:
            win_ids = {w.node_id for w in window_rows}
            evidence = [e for e in evidence if e.node_id not in win_ids] + window_rows

    seen: set[str] = set()
    deduped: list[Evidence] = []
    for e in evidence:
        if e.node_id and e.node_id not in seen:
            seen.add(e.node_id)
            deduped.append(e)
    return deduped
