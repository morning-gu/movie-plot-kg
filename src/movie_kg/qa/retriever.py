from typing import Literal

from pydantic import BaseModel

from movie_kg.graph.query_templates import execute_template
from movie_kg.qa.planner import RetrievalPlan, TargetEntity

INDEXES = [("event_vec", "Event"), ("character_vec", "Character"),
           ("virtualtime_vec", "VirtualTime"), ("location_vec", "Location")]


class Evidence(BaseModel):
    source: Literal["vector", "graph"]
    node_id: str
    text: str
    score: float | None = None
    extra: dict = {}


def _node_text(node: dict) -> str:
    if "topic" in node:
        return f"[{node.get('event_type', '')}] {node.get('topic', '')}:{node.get('description', '')}"
    return f"{node.get('name', '')}:{node.get('description', '')}"


def vector_recall(session, llm, query: str, top_k: int = 5) -> list[Evidence]:
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


def bind_entities(session, targets: list[TargetEntity],
                  vector_hits: list[Evidence]) -> dict[str, str]:
    """name_hint -> 节点 ID: name/aliases 精确匹配,失败回落向量最近同类命中。"""
    bound: dict[str, str] = {}
    for t in targets:
        found = None
        recs = session.run(
            f"MATCH (n:{t.type}) WHERE n.name = $h OR $h IN n.aliases "
            "RETURN n.id AS id LIMIT 1", h=t.name_hint)
        # type 经 TargetEntity Literal 白名单校验,拼接入 Cypher 安全
        for rec in recs:
            found = rec["id"]
            break
        if found is None:
            for hit in vector_hits:
                if hit.extra.get("label") == t.type:
                    found = hit.node_id
                    break
        if found is not None:
            bound[t.name_hint] = found
    return bound


def retrieve(session, llm, plan: RetrievalPlan, top_k: int = 5) -> list[Evidence]:
    hits = vector_recall(session, llm, plan.rewritten_query, top_k)
    evidence: list[Evidence] = list(hits)
    bound = bind_entities(session, plan.target_entities, hits)
    for gq in plan.graph_queries[:2]:
        params = {k: (bound.get(v, v) if isinstance(v, str) else v)
                  for k, v in gq.params.items()}
        try:
            rows = execute_template(session, gq.template_id, params)
        except Exception:
            continue  # 模板参数不合法(如绑定失败)时跳过,不中断问答
        for row in rows:
            for key, value in row.items():
                if isinstance(value, dict) and "id" in value:
                    evidence.append(Evidence(source="graph", node_id=value["id"],
                                             text=_node_text(value),
                                             extra={"template": gq.template_id}))
                elif key == "path" and value:
                    for node in value.nodes if hasattr(value, "nodes") else []:
                        evidence.append(Evidence(
                            source="graph", node_id=node["id"],
                            text=_node_text(dict(node)),
                            extra={"template": gq.template_id}))
    seen: set[str] = set()
    deduped: list[Evidence] = []
    for e in evidence:
        if e.node_id and e.node_id not in seen:
            seen.add(e.node_id)
            deduped.append(e)
    return deduped
