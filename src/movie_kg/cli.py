import typer

from movie_kg.config import load_models_config, load_neo4j_config

app = typer.Typer(help="movie-plot-kg: 剧情知识图谱构建 / 问答 / 评估")
eval_app = typer.Typer(help="评估")
app.add_typer(eval_app, name="eval")


def _make_llm(cfg):
    from movie_kg.llm import LLMClient
    return LLMClient(cfg)


def _make_driver(cfg):
    from neo4j import GraphDatabase
    return GraphDatabase.driver(cfg.uri, auth=(cfg.user, cfg.password))


@app.command()
def build(movie: str = typer.Option(..., "--movie"),  # typer 0.27 无默认参数是位置实参;计划测试用 --movie
          from_scratch: bool = typer.Option(False, "--from-scratch"),
          scenes: str = typer.Option(None, "--scenes", help="如 1-5,成本校准试跑")):
    """Phase A(抽取+checkpoint)-> B(全局 pass)-> C(写图+向量化)。"""
    from pathlib import Path
    from movie_kg.embeddings.embedder import embed_all
    from movie_kg.extraction.checkpoints import CheckpointStore
    from movie_kg.extraction.global_passes import (
        build_causal_pairs, collect_name_contexts, collect_vts,
        pass_b1_order_vts, pass_b2_merge_aliases, pass_b3_causal)
    from movie_kg.extraction.pipeline import run_phase_a
    from movie_kg.graph.schema import apply_schema
    from movie_kg.graph.writer import GraphWriter
    from movie_kg.ingestion.data_loader import load_movie

    movie_dir = Path("data/movies") / movie
    data = load_movie(movie_dir)
    store = CheckpointStore(movie_dir)
    llm = _make_llm(load_models_config())
    scene_range = None
    if scenes:
        lo, hi = scenes.split("-")
        scene_range = (int(lo), int(hi))
    a = run_phase_a(llm, data, store, scene_range=scene_range,
                    from_scratch=from_scratch)
    typer.echo(f"Phase A: 完成 {len(a.completed)}, 失败 {len(a.failed)}, "
               f"跳过 {a.skipped}, tokens={a.usage}")
    if a.failed:
        typer.echo(f"失败场景: {a.failed}")
    extractions = [e for e in (store.load(s.meta.scene_id) for s in data.scenes)
                   if e is not None]
    if not extractions:
        typer.echo("无可用抽取结果,退出")
        raise typer.Exit(1)
    typer.echo("Phase B: 时间线 / 别名 / 因果 ...")
    ordered_vts = pass_b1_order_vts(llm, collect_vts(data))
    chars, locs = collect_name_contexts(data, extractions)
    alias_plan = pass_b2_merge_aliases(llm, chars, locs)
    causal = pass_b3_causal(llm, build_causal_pairs(data, extractions))
    typer.echo("Phase C: 写图 + 向量化 ...")
    driver = _make_driver(load_neo4j_config())
    with driver.session() as session:
        apply_schema(session)
        writer = GraphWriter(session, alias_plan=alias_plan)
        writer.ensure_movie(data.movie_id, data.title)
        for scene in data.scenes:
            ext = store.load(scene.meta.scene_id)
            if ext is None:
                continue
            writer.ensure_scene(scene.meta)
            writer.write_extraction(ext, scene.meta)
        writer.write_precedes_chain(extractions, [s.meta for s in data.scenes])
        for ext in extractions:
            writer.write_relations(ext.relations, data.movie_id)
        writer.apply_vt_order(ordered_vts, data.movie_id)
        writer.write_causal_links(causal)
        n = embed_all(session, llm, data.movie_id)
    driver.close()
    typer.echo(f"完成: 嵌入 {n} 个实体。图谱就绪。")


@app.command()
def ask(question: str):
    """单轮问答: 规划 -> 检索 -> 回答(固定 2 次 LLM 调用)。"""
    from movie_kg.qa.answerer import answer
    from movie_kg.qa.planner import plan_retrieval
    from movie_kg.qa.retriever import retrieve

    llm = _make_llm(load_models_config())
    plan = plan_retrieval(llm, question)
    driver = _make_driver(load_neo4j_config())
    with driver.session() as session:
        evidence = retrieve(session, llm, plan)
    driver.close()
    result = answer(llm, question, evidence)
    typer.echo(result.answer)
    typer.echo(f"引用: {', '.join(result.cited_ids) if result.cited_ids else '(无)'}")


@eval_app.command("build")
def eval_build(movie: str = typer.Option(..., "--movie")):
    """构建端评估: 角色识别 P/R/F1 + 关系抽样清单。"""
    from pathlib import Path
    import csv

    from movie_kg.eval.build_eval import (aggregate_prf, character_prf,
                                          merge_labels, sample_relations,
                                          score_relation_samples,
                                          write_build_report)
    from movie_kg.extraction.checkpoints import CheckpointStore
    from movie_kg.ingestion.data_loader import load_movie

    movie_dir = Path("data/movies") / movie
    data = load_movie(movie_dir)
    store = CheckpointStore(movie_dir)
    extractions = [e for e in (store.load(s.meta.scene_id) for s in data.scenes)
                   if e is not None]
    rows = character_prf(data, extractions)
    agg = aggregate_prf(rows)
    Path("eval").mkdir(exist_ok=True)
    samples = []
    driver = None
    try:
        driver = _make_driver(load_neo4j_config())
        with driver.session() as session:
            samples = sample_relations(session)
    finally:
        if driver is not None:
            driver.close()
    samples_path = Path("eval/relation_samples.csv")
    existing: list[dict] = []
    if samples_path.exists():
        with open(samples_path, newline="", encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
    samples = merge_labels(existing, samples)
    with open(samples_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["type", "start", "end", "props", "label"])
        w.writeheader()
        w.writerows(samples)
    relation_scores = None
    if any(s["label"] for s in samples):
        relation_scores = score_relation_samples(samples)
    write_build_report("eval/build_report.md", rows, agg, relation_scores)
    if relation_scores is not None:
        typer.echo(f"角色识别 F1={agg.get('f1')};已合并人工标注并计算关系正确率 "
                   f"-> eval/build_report.md")
    else:
        typer.echo(f"角色识别 F1={agg.get('f1')};抽样清单 -> eval/relation_samples.csv"
                   f"(人工填 label 列后重新运行可算正确率)")


@eval_app.command("qa")
def eval_qa(qa_set: str = typer.Option("eval/qa_set.yaml", "--qa-set"),
            mode: str = typer.Option("full", "--mode",
                                     help="full | vector-only(消融)")):
    """问答端评估: 跑 QA 集 + LLM judge + 汇总(消融用 --mode vector-only)。"""
    from movie_kg.eval.qa_eval import (judge_answers, load_qa_set, run_qa,
                                       summarize, write_qa_report)

    items = load_qa_set(qa_set)
    llm = _make_llm(load_models_config())
    driver = _make_driver(load_neo4j_config())
    with driver.session() as session:
        results = run_qa(session, llm, items, mode=mode)
    driver.close()
    judged = judge_answers(llm, items, results)
    summary = summarize(judged)
    write_qa_report("eval/qa_report.md", judged, summary)
    typer.echo(f"accuracy={summary['overall_accuracy']} "
               f"hit_rate={summary['mean_hit_rate']} -> eval/qa_report.md")


# typer.testing.CliRunner.invoke 只接受 Typer 实例,测试以 main 调用;
# 控制台脚本 kg = movie_kg.cli:main 同样按可调用对象工作(Typer 可调用)。
main = app
