DESCRIBE_QUERY = (
    "MATCH (n) WHERE (n:Event OR n:Character OR n:VirtualTime OR n:Location) "
    "AND n.movie_id = $movie AND n.embedding IS NULL RETURN n")


def describe_event(topic: str, description: str, event_type: str) -> str:
    return f"[{event_type}] {topic}:{description}"


def describe_entity(name: str, description: str, kind: str) -> str:
    return f"{kind} {name}:{description}"


def _node_text(node: dict) -> str:
    if "topic" in node:
        return describe_event(node.get("topic", ""), node.get("description", ""),
                              node.get("event_type", ""))
    kind = "虚拟时间" if "timeline_index" in node else "实体"
    return describe_entity(node.get("name", ""), node.get("description", ""), kind)


def embed_all(session, llm, movie_id: str, batch_size: int = 32) -> int:
    """C2: 为缺 embedding 的实体生成向量并写回,返回处理数。"""
    nodes = [rec["node"] for rec in session.run(DESCRIBE_QUERY, movie=movie_id)]
    for i in range(0, len(nodes), batch_size):
        batch = nodes[i:i + batch_size]
        vectors = llm.embed([_node_text(dict(n)) for n in batch])
        for node, vec in zip(batch, vectors):
            session.run("MATCH (n {id: $id}) SET n.embedding = $embedding",
                        id=node["id"], embedding=vec)
    return len(nodes)
