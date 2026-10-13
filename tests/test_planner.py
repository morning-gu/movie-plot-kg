from movie_kg.qa.planner import plan_retrieval
from tests.fakes import ScriptedLLM

PLAN = {"intent": "causal", "rewritten_query": "导致决裂的事件链",
        "target_entities": [{"type": "Character", "name_hint": "张三"}],
        "graph_queries": [{"template_id": "causes_of",
                           "params": {"event_hint": "决裂", "depth": 3}}]}


def test_plan_happy_path():
    plan = plan_retrieval(ScriptedLLM(PLAN), "导致张三和李四决裂的事件链?")
    assert plan.intent == "causal"
    assert plan.rewritten_query == "导致决裂的事件链"
    assert plan.graph_queries[0].template_id == "causes_of"


def test_plan_drops_unknown_template():
    raw = dict(PLAN, graph_queries=[
        {"template_id": "drop_all", "params": {}},
        {"template_id": "causes_of", "params": {"event_hint": "决裂", "depth": 3}}])
    plan = plan_retrieval(ScriptedLLM(raw), "q")
    assert [g.template_id for g in plan.graph_queries] == ["causes_of"]


def test_plan_caps_graph_queries_at_two():
    raw = dict(PLAN, graph_queries=[
        {"template_id": "events_after", "params": {"event_hint": "a", "depth": 2}},
        {"template_id": "causes_of", "params": {"event_hint": "b", "depth": 3}},
        {"template_id": "subevents_of", "params": {"event_hint": "c"}}])
    plan = plan_retrieval(ScriptedLLM(raw), "q")
    assert len(plan.graph_queries) == 2
