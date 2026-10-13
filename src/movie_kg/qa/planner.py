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


class PlannerOutput(BaseModel):
    intent: Literal["factual", "causal", "relational", "temporal", "synthesis"]
    rewritten_query: str
    target_entities: list[TargetEntity] = []
    graph_queries: list[GraphQueryPlan] = []


class RetrievalPlan(BaseModel):
    intent: str
    rewritten_query: str
    target_entities: list[TargetEntity]
    graph_queries: list[GraphQueryPlan]


def plan_retrieval(llm, question: str) -> RetrievalPlan:
    cfg = getattr(llm, "cfg", None)
    model = cfg.text_model if cfg is not None else "text"
    system = render(load_prompt("qa_planning"), QUESTION=question)
    raw = llm.chat_json(model=model,
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": "请输出检索计划 JSON。"}],
                        schema=PlannerOutput)
    valid_queries = [g for g in raw.graph_queries if g.template_id in TEMPLATES][:2]
    return RetrievalPlan(intent=raw.intent, rewritten_query=raw.rewritten_query,
                         target_entities=raw.target_entities,
                         graph_queries=valid_queries)
