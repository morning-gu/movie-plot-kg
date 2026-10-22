import logging

from movie_kg.eval.qa_eval import (
    QAItem, judge_answers, load_qa_set, movie_of_item, run_qa, summarize,
    write_qa_report)
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


def test_run_qa_default_top_k_is_10():
    # 默认召回数 = 10(扩容实验 +5pp;旧行为 5 已被实验否决)
    items = load_qa_set(_make_set())
    session = MockSession(results=[[], [], [], []])
    run_qa(session, ScriptedLLM(PLAN, ANSWER), items, mode="full")
    ks = [p["k"] for q, p in session.queries if "queryNodes" in q]
    assert ks and all(k == 10 for k in ks)


def test_cli_eval_qa_top_k_flag(tmp_path, monkeypatch):
    # CLI 旗标穿线 + 报告文件名带 top_k 后缀(非默认值时)。
    # --scope global 为意图保持性显式参数:CLI 默认 scope 已改 movie(设计),
    # 本测试钉的是全局路径的 top_k 穿线与旧文件名,断言一字未动
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
        "--top-k", "30", "--scope", "global"])
    assert result.exit_code == 0, result.output
    ks = [p["k"] for q, p in session.queries if "queryNodes" in q]
    assert ks and all(k == 30 for k in ks)
    assert (tmp_path / "eval" / "qa_report_full_k30.md").exists()


def _cli_setup(tmp_path, monkeypatch):
    # eval qa CLI 测试共用搭建(新测试私有;不动既有测试)
    import shutil
    from pathlib import Path

    import movie_kg.cli as cli_mod
    from tests.fakes import MockDriver
    shutil.copytree(Path(__file__).resolve().parents[1] / "config",
                    tmp_path / "config")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw-test")
    monkeypatch.chdir(tmp_path)
    set_path = _make_scoped_set(tmp=tmp_path)
    monkeypatch.setattr(cli_mod, "_make_llm",
                        lambda cfg: ScriptedLLM(PLAN, ANSWER, JUDGE_OK))
    session = MockSession(results=[[], [], [], []])
    monkeypatch.setattr(cli_mod, "_make_driver",
                        lambda cfg: MockDriver(session))
    return session, set_path


def test_cli_eval_qa_default_scope_movie(tmp_path, monkeypatch):
    # CLI 默认 scope=movie(产品面默认):片内扫描 + _scoped 报告名
    from typer.testing import CliRunner
    from movie_kg.cli import main
    session, set_path = _cli_setup(tmp_path, monkeypatch)
    result = CliRunner().invoke(main, ["eval", "qa", "--qa-set", str(set_path)])
    assert result.exit_code == 0, result.output
    assert not any("queryNodes" in q for q, _ in session.queries)
    assert (tmp_path / "eval" / "qa_report_full_scoped.md").exists()


def test_cli_eval_qa_scope_global_keeps_legacy_name(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from movie_kg.cli import main
    session, set_path = _cli_setup(tmp_path, monkeypatch)
    result = CliRunner().invoke(main, ["eval", "qa", "--qa-set", str(set_path),
                                       "--scope", "global"])
    assert result.exit_code == 0, result.output
    assert any("queryNodes" in q for q, _ in session.queries)
    assert (tmp_path / "eval" / "qa_report_full.md").exists()


def test_cli_eval_qa_movies_filter_suffix_and_no_clobber(tmp_path, monkeypatch):
    # 过滤运行绝不覆盖未过滤报告:文件名追加 _m{ids}(builder 审查裁决)
    from typer.testing import CliRunner
    from movie_kg.cli import main
    session, set_path = _cli_setup(tmp_path, monkeypatch)
    result = CliRunner().invoke(main, ["eval", "qa", "--qa-set", str(set_path),
                                       "--movies", "tt0105236"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "eval" / "qa_report_full_scoped_mtt0105236.md").exists()
    assert not (tmp_path / "eval" / "qa_report_full_scoped.md").exists()


def test_cli_eval_qa_movies_empty_intersection_errors(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from movie_kg.cli import main
    session, set_path = _cli_setup(tmp_path, monkeypatch)
    result = CliRunner().invoke(main, ["eval", "qa", "--qa-set", str(set_path),
                                       "--movies", "tt9999999"])
    assert result.exit_code != 0
    assert "无匹配" in result.output


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


def _make_scoped_set(tmp=None):
    # gold 带 tt 前缀:movie_of_item 可推导出影片
    import tempfile
    from pathlib import Path
    d = Path(tmp) if tmp else Path(tempfile.mkdtemp())
    p = d / "qa.yaml"
    p.write_text("""items:
  - id: q001
    question: Mr. White 为什么坚持送医院?
    gold_answer: 因为同伴重伤。
    gold_entity_ids: [evt_tt0105236_008_02]
    intent: causal
""", encoding="utf-8")
    return p


def test_movie_of_item_derives_tt():
    item = load_qa_set(_make_scoped_set())[0]
    assert movie_of_item(item) == "tt0105236"
    assert movie_of_item(QAItem(id="x", question="q", gold_answer="a",
                                gold_entity_ids=[], intent="factual")) is None


def test_run_qa_default_scope_is_global():
    # 库层默认 global(builder 审查裁决):裸调用直接覆盖全局路径,
    # 而非借"推导不出回落"的意外路径间接覆盖
    items = load_qa_set(_make_set())
    session = MockSession(results=[[], [], [], []])
    run_qa(session, ScriptedLLM(PLAN, ANSWER), items, mode="full")
    assert any("queryNodes" in q for q, _ in session.queries)
    assert all("cosine" not in q for q, _ in session.queries)


def test_run_qa_scoped_derives_movie_per_item():
    items = load_qa_set(_make_scoped_set())
    session = MockSession(results=[[], [], [], []])
    results = run_qa(session, ScriptedLLM(PLAN, ANSWER), items,
                     mode="full", scope="movie")
    assert results[0].movie_id == "tt0105236"
    assert not any("queryNodes" in q for q, _ in session.queries)
    assert all(p.get("mid") == "tt0105236"
               for q, p in session.queries if "cosine" in q)


def test_run_qa_scoped_falls_back_when_movie_underivable(caplog):
    # gold 无 tt 前缀/为空:该题回落全局并计警告(防御路径,当前 60 题全有 gold)
    items = load_qa_set(_make_set())          # gold = evt_m1_001_01
    session = MockSession(results=[[], [], [], []])
    with caplog.at_level(logging.WARNING):
        results = run_qa(session, ScriptedLLM(PLAN, ANSWER), items,
                         mode="full", scope="movie")
    assert results[0].movie_id is None
    assert any("queryNodes" in q for q, _ in session.queries)
    assert caplog.text.strip()                # 必须留痕,不静默


def test_run_qa_rejects_bad_scope():
    import pytest
    with pytest.raises(ValueError, match="scope"):
        run_qa(MockSession(), ScriptedLLM(), load_qa_set(_make_set()),
               mode="full", scope="mov")


def test_summarize_and_report_per_movie(tmp_path):
    items = load_qa_set(_make_scoped_set())
    session = MockSession(results=[[], [], [], []])
    results = run_qa(session, ScriptedLLM(PLAN, ANSWER), items,
                     mode="full", scope="movie")
    judged = judge_answers(ScriptedLLM(JUDGE_OK), items, results)
    s = summarize(judged)
    assert s["per_movie"]["tt0105236"]["n"] == 1
    assert s["per_movie"]["tt0105236"]["accuracy"] == 1.0
    out = tmp_path / "r.md"
    write_qa_report(out, judged, s)
    text = out.read_text(encoding="utf-8")
    assert "按片" in text and "tt0105236" in text


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
