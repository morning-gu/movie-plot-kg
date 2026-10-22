from typing import Literal

from pydantic import BaseModel

from movie_kg.graph.query_templates import TEMPLATES
from movie_kg.prompts import load_prompt, render


class TargetEntity(BaseModel):
    type: Literal["Character", "Event", "VirtualTime", "Location"]
    name_hint: str


class GraphQueryPlan(BaseModel):
    template_id: str
    params: dict


class RawTargetEntity(BaseModel):
    # LLM 偶发给出词表外类型(实测 "Song"),先收下,由 plan_retrieval 过滤——
    # 直接 Literal 会让整次问答崩在重试耗尽(试点实证)
    type: str
    name_hint: str


class PlannerOutput(BaseModel):
    intent: str  # 宽松收下;下游统计用题目自带 intent,不依赖此值
    rewritten_query: str
    target_entities: list[RawTargetEntity] = []
    graph_queries: list[GraphQueryPlan] = []


class RetrievalPlan(BaseModel):
    intent: str
    rewritten_query: str
    target_entities: list[TargetEntity]
    graph_queries: list[GraphQueryPlan]
    # 按片隔离的影片 tt 号:由调用方(CLI/评测)注入,LLM 不参与;
    # None = 全局检索(场景 A,行为与历史逐字节一致)
    movie_id: str | None = None


def plan_retrieval(llm, question: str,
                   movie_id: str | None = None) -> RetrievalPlan:
    cfg = getattr(llm, "cfg", None)
    model = cfg.text_model if cfg is not None else "text"
    system = render(load_prompt("qa_planning"), QUESTION=question)
    raw = llm.chat_json(model=model,
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": "请输出检索计划 JSON。"}],
                        schema=PlannerOutput)
    valid_types = {"Character", "Event", "VirtualTime", "Location"}
    targets = [TargetEntity(type=t.type, name_hint=t.name_hint)
               for t in raw.target_entities if t.type in valid_types]
    valid_queries = [g for g in raw.graph_queries if g.template_id in TEMPLATES][:2]
    return RetrievalPlan(intent=raw.intent, rewritten_query=raw.rewritten_query,
                         target_entities=targets,
                         graph_queries=valid_queries, movie_id=movie_id)
