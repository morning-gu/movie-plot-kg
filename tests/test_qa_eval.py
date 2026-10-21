from movie_kg.eval.qa_eval import (
    judge_answers, load_qa_set, run_qa, summarize, write_qa_report)
from tests.fakes import MockSession, ScriptedLLM

PLAN = {"intent": "factual", "rewritten_query": "张三发怒",
        "target_entities": [], "graph_queries": []}
ANSWER = {"answer": "码头仓库。", "cited_ids": ["evt_m1_001_01"]}
JUDGE_OK = {"correct": True, "reason": "地点正确"}
JUDGE_BAD = {"correct": False, "reason": "答非所问"}


def test_load_qa_set(tmp_path):
    import textwrap
    p = tmp_path / "qa.yaml"
    p.write_text(textwrap.dedent("""\
        items:
          - id: q001
            question: q?
            gold_answer: a.
            gold_entity_ids: [evt_m1_001_01]
            intent: factual
        """), encoding="utf-8")
    items = load_qa_set(p)
    assert items[0].id == "q001" and items[0].intent == "factual"


def test_run_qa_full_vs_vector_only():
    items = load_qa_set(_make_set())
    session = MockSession(results=[[], [], [], []])
    llm = ScriptedLLM(PLAN, ANSWER)
    results = run_qa(session, llm, items, mode="vector-only")
    assert results[0].answer == "码头仓库。"
    # vector-only 模式下不应出现模板查询
    assert not any("RELATED_TO" in q or "PRECEDES" in q for q, _ in session.queries)


def test_run_qa_rejects_bad_mode():
    import pytest
    with pytest.raises(ValueError, match="mode"):
        run_qa(MockSession(), ScriptedLLM(), load_qa_set(_make_set()),
               mode="vecor-only")


def test_run_qa_threads_top_k():
    # 检索扩容实验:top_k 须从 run_qa 穿到 vector_recall(每个向量索引的 k)
    items = load_qa_set(_make_set())
    session = MockSession(results=[[], [], [], []])
    run_qa(session, ScriptedLLM(PLAN, ANSWER), items, mode="full", top_k=10)
    ks = [p["k"] for q, p in session.queries if "queryNodes" in q]
    assert ks and all(k == 10 for k in ks)


def test_cli_eval_qa_top_k_flag(tmp_path, monkeypatch):
    # CLI 旗标穿线 + 报告文件名带 top_k 后缀(非默认值时)
    import shutil
    from pathlib import Path

    import movie_kg.cli as cli_mod
    from typer.testing import CliRunner
    from movie_kg.cli import main
    from tests.fakes import MockDriver
    shutil.copytree(Path(__file__).resolve().parents[1] / "config",
                    tmp_path / "config")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw-test")
    monkeypatch.chdir(tmp_path)
    set_path = _make_set(tmp=tmp_path)
    monkeypatch.setattr(cli_mod, "_make_llm",
                        lambda cfg: ScriptedLLM(PLAN, ANSWER, JUDGE_OK))
    session = MockSession(results=[[], [], [], []])
    monkeypatch.setattr(cli_mod, "_make_driver",
                        lambda cfg: MockDriver(session))
    runner = CliRunner()
    result = runner.invoke(main, [
        "eval", "qa", "--qa-set", str(set_path), "--mode", "full",
        "--top-k", "10"])
    assert result.exit_code == 0, result.output
    ks = [p["k"] for q, p in session.queries if "queryNodes" in q]
    assert ks and all(k == 10 for k in ks)
    assert (tmp_path / "eval" / "qa_report_full_k10.md").exists()


def _make_set(tmp=None):
    import tempfile
    from pathlib import Path
    d = Path(tmp) if tmp else Path(tempfile.mkdtemp())
    p = d / "qa.yaml"
    p.write_text("""items:
  - id: q001
    question: 张三在哪里发怒了?
    gold_answer: 张三在码头仓库发怒。
    gold_entity_ids: [evt_m1_001_01]
    intent: factual
""", encoding="utf-8")
    return p


def test_hit_rate_computed():
    items = load_qa_set(_make_set())
    session = MockSession(results=[
        [{"node": {"id": "evt_m1_001_01", "topic": "对峙", "description": "d",
                   "event_type": "conflict"}, "score": 0.9}, ], [], [], []])
    llm = ScriptedLLM(PLAN, ANSWER)
    results = run_qa(session, llm, items, mode="full")
    assert results[0].hit_rate == 1.0
    assert "evt_m1_001_01" in results[0].evidence_ids


def test_judge_and_summarize():
    items = load_qa_set(_make_set())
    session = MockSession(results=[[], [], [], []])
    results = run_qa(session, ScriptedLLM(PLAN, ANSWER), items, mode="full")
    judged = judge_answers(ScriptedLLM(JUDGE_OK), items, results)
    assert judged[0].judged_correct is True
    s = summarize(judged)
    assert s["overall_accuracy"] == 1.0
    assert s["per_intent"]["factual"]["accuracy"] == 1.0


def test_report_records_mode(tmp_path):
    items = load_qa_set(_make_set())
    session = MockSession(results=[[], [], [], []])
    results = run_qa(session, ScriptedLLM(PLAN, ANSWER), items, mode="full")
    judged = judge_answers(ScriptedLLM(JUDGE_OK), items, results)
    out = tmp_path / "qa_report_vector-only.md"
    write_qa_report(out, judged, summarize(judged), mode="vector-only")
    text = out.read_text(encoding="utf-8")
    assert "运行模式: vector-only" in text


def test_report_written(tmp_path):
    items = load_qa_set(_make_set())
    session = MockSession(results=[[], [], [], []])
    results = run_qa(session, ScriptedLLM(PLAN, ANSWER), items, mode="full")
    judged = judge_answers(ScriptedLLM(JUDGE_BAD), items, results)
    out = tmp_path / "qa_report.md"
    write_qa_report(out, judged, summarize(judged))
    text = out.read_text(encoding="utf-8")
    assert "factual" in text and "0.0" in text
