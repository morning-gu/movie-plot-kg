from movie_kg.qa.planner import plan_retrieval
from tests.fakes import ScriptedLLM

PLAN = {"intent": "causal", "rewritten_query": "导致决裂的事件链",
        "target_entities": [{"type": "Character", "name_hint": "张三"}],
        "graph_queries": [{"template_id": "causes_of",
                           "params": {"id": "决裂", "depth": 3}}]}


def test_plan_happy_path():
    plan = plan_retrieval(ScriptedLLM(PLAN), "导致张三和李四决裂的事件链?")
    assert plan.intent == "causal"
    assert plan.rewritten_query == "导致决裂的事件链"
    assert plan.graph_queries[0].template_id == "causes_of"


def test_plan_drops_unknown_template():
    raw = dict(PLAN, graph_queries=[
        {"template_id": "drop_all", "params": {}},
        {"template_id": "causes_of", "params": {"id": "决裂", "depth": 3}}])
    plan = plan_retrieval(ScriptedLLM(raw), "q")
    assert [g.template_id for g in plan.graph_queries] == ["causes_of"]


def test_plan_caps_graph_queries_at_two():
    raw = dict(PLAN, graph_queries=[
        {"template_id": "events_after", "params": {"id": "a", "depth": 2}},
        {"template_id": "causes_of", "params": {"id": "b", "depth": 3}},
        {"template_id": "subevents_of", "params": {"id": "c"}}])
    plan = plan_retrieval(ScriptedLLM(raw), "q")
    assert len(plan.graph_queries) == 2


def test_planner_filters_invalid_entity_types():
    # LLM 偶发词表外类型(实测 "Song"):过滤而非崩溃;合法项保留
    from types import SimpleNamespace
    from movie_kg.config import ModelsConfig
    from movie_kg.llm import LLMClient
    from movie_kg.qa.planner import plan_retrieval
    from tests.fakes import FakeOpenAI

    cfg = ModelsConfig(base_url="https://x", api_key="sk", vision_model="v",
                       text_model="t", embedding_model="e", embedding_dims=8)
    raw = ('{"intent": "factual", "rewritten_query": "like a virgin 解读", '
           '"target_entities": [{"type": "Song", "name_hint": "Like a Virgin"},'
           ' {"type": "Character", "name_hint": "Mr. Brown"}], "graph_queries": []}')
    llm = LLMClient(cfg, client=FakeOpenAI([raw]))
    plan = plan_retrieval(llm, "Mr. Brown 对 Like a Virgin 的解读?")
    assert [(t.type, t.name_hint) for t in plan.target_entities] == \
        [("Character", "Mr. Brown")]
