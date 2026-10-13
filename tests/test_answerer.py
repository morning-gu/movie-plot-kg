from movie_kg.qa.answerer import answer
from movie_kg.qa.retriever import Evidence
from tests.fakes import ScriptedLLM

EV = [Evidence(source="vector", node_id="evt_m1_001_01",
               text="[conflict] 对峙:张三与李四对峙", score=0.9)]


def test_answer_returns_citations():
    llm = ScriptedLLM({"answer": "两人在码头仓库对峙,随后关系转为敌对。",
                       "cited_ids": ["evt_m1_001_01"]})
    result = answer(llm, "张三和李四怎么了?", EV)
    assert "对峙" in result.answer
    assert result.cited_ids == ["evt_m1_001_01"]
    # prompt 中包含证据行与问题
    _, messages = llm.calls[0]
    assert "[evt_m1_001_01]" in messages[0]["content"]
    assert "张三和李四怎么了" in messages[0]["content"]
