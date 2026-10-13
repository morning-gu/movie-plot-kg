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


# typer.testing.CliRunner.invoke 只接受 Typer 实例,测试以 main 调用;
# 控制台脚本 kg = movie_kg.cli:main 同样按可调用对象工作(Typer 可调用)。
main = app
