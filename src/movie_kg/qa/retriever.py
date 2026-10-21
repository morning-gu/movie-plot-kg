from typing import Literal

from pydantic import BaseModel

from movie_kg.graph.query_templates import execute_template
from movie_kg.prompts import load_prompt, render
from movie_kg.qa.planner import RetrievalPlan, TargetEntity

INDEXES = [("event_vec", "Event"), ("character_vec", "Character"),
           ("virtualtime_vec", "VirtualTime"), ("location_vec", "Location")]


class Evidence(BaseModel):
    source: Literal["vector", "graph"]
    node_id: str
    text: str
    score: float | None = None
    extra: dict = {}


class EventPick(BaseModel):
    node_id: str | None = None


def _node_text(node: dict) -> str:
    if "topic" in node:
        return f"[{node.get('event_type', '')}] {node.get('topic', '')}:{node.get('description', '')}"
    return f"{node.get('name', '')}:{node.get('description', '')}"


def vector_recall(session, llm, query: str, top_k: int = 10) -> list[Evidence]:
    vec = llm.embed([query])[0]
    out: list[Evidence] = []
    for index, label in INDEXES:
        for rec in session.run(
                "CALL db.index.vector.queryNodes($index, $k, $vec) YIELD node, score",
                index=index, k=top_k, vec=vec):
            node = dict(rec["node"])
            out.append(Evidence(source="vector", node_id=node.get("id", ""),
                                text=_node_text(node), score=rec.get("score"),
                                extra={"label": label}))
    return out


def _movie_of(node_id: str) -> str:
    """从实体 ID 前缀取影片号(evt_tt0113243_120_01 → tt0113243)。"""
    parts = node_id.split("_")
    return parts[1] if len(parts) > 2 else ""


def bind_entities(session, targets: list[TargetEntity],
                  vector_hits: list[Evidence], llm=None) -> dict[str, str]:
    """name_hint -> 节点 ID:精确(name/aliases)→ 词级包含 → 向量同类命中。

    盲取首个同类向量命中会把 "John Mason" 绑到语义相近的 Goodspeed 上
    (试点实证),产出自环/错向证据;错绑比不绑更糟,各级都要求名字证据。
    Event 目标例外(试点实证 7/12 temporal 题 hit=0):Event 节点无
    name/aliases 且中文主题无分词,三级全落空——改为 LLM 锚点选择:
    top-5 同影片候选(有 Character 绑定时按其影片过滤,防跨片污染;
    分数 >=0.55 预筛)交给文本模型按 hint 判定,可判 null
    (Phase A 回归实证:分数阈值绑定的错锚点图证据比不绑定更糟)。"""
    bound: dict[str, str] = {}
    event_targets: list[TargetEntity] = []
    for t in targets:
        if t.type == "Event":
            event_targets.append(t)
            continue
        recs = list(session.run(
            f"MATCH (n:{t.type}) WHERE n.name = $h OR $h IN n.aliases "
            "RETURN n.id AS id LIMIT 1", h=t.name_hint))
        found = recs[0]["id"] if recs else None
        if found is None:
            # 词级包含:hint 的全部词都出现在候选名中(次序无关,
            # "John Mason" -> "John Patrick Mason");唯一命中才绑
            words = {w for w in t.name_hint.lower().replace(".", " ").split() if w}
            cands = session.run(
                f"MATCH (n:{t.type}) RETURN n.id AS id, n.name AS name")
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
    # Event 专层:LLM 锚点选择(top-5 同影片候选,可判 null)
    if event_targets and llm is not None:
        cfg = getattr(llm, "cfg", None)
        model = cfg.text_model if cfg is not None else "text"
        bound_movies = {_movie_of(nid) for nid in bound.values()}
        for t in event_targets:
            cands = [h for h in vector_hits
                     if h.extra.get("label") == "Event"
                     and h.score is not None and h.score >= 0.55
                     and (not bound_movies
                          or _movie_of(h.node_id) in bound_movies)][:5]
            if not cands:
                continue
            cand_lines = "\n".join(f"{h.node_id} | {h.text[:80]}"
                                   for h in cands)
            system = render(load_prompt("event_binding"),
                            HINT=t.name_hint, CANDIDATES=cand_lines)
            pick = llm.chat_json(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": "请输出选择 JSON。"}],
                schema=EventPick)
            if pick.node_id in {h.node_id for h in cands}:
                bound[t.name_hint] = pick.node_id
    return bound


def retrieve(session, llm, plan: RetrievalPlan, top_k: int = 10) -> list[Evidence]:
    hits = vector_recall(session, llm, plan.rewritten_query, top_k)
    evidence: list[Evidence] = list(hits)
    bound = bind_entities(session, plan.target_entities, hits, llm=llm)

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
    seen: set[str] = set()
    deduped: list[Evidence] = []
    for e in evidence:
        if e.node_id and e.node_id not in seen:
            seen.add(e.node_id)
            deduped.append(e)
    # 回答端呈现序:图证据(模板精心遍历的产物)置顶——LLM 对 prompt
    # 开头的证据权重更高;向量证据按分数降序、全局截 top-15 抗稀释。
    # hit_rate 为集合口径,不受呈现序影响。
    graph_ev = [e for e in deduped if e.source == "graph"]
    vec_ev = sorted((e for e in deduped if e.source == "vector"),
                    key=lambda e: -(e.score or 0.0))[:15]
    return graph_ev + vec_ev
