import random

from pydantic import BaseModel

from movie_kg.eval.report import md_table
from movie_kg.models.extraction import SceneExtraction
from movie_kg.models.scene import MovieData

RELATION_TYPES = ["PRECEDES", "CAUSES", "SUBEVENT_OF", "PARTICIPATES_IN",
                  "RELATED_TO"]


class ScenePRF(BaseModel):
    scene_id: str
    gold: list[str]
    pred: list[str]
    precision: float
    recall: float
    f1: float


def character_prf(movie: MovieData,
                  extractions: list[SceneExtraction]) -> list[ScenePRF]:
    by_scene = {e.scene_id: e for e in extractions}
    rows: list[ScenePRF] = []
    for scene in movie.scenes:
        ext = by_scene.get(scene.meta.scene_id)
        gold = {c.name for c in scene.meta.characters}
        pred = set()
        if ext is not None:
            for ev in ext.events:
                for p in ev.participants:
                    if not p.name.startswith("未识别:"):
                        pred.add(p.name)
        precision = len(gold & pred) / len(pred) if pred else 0.0
        recall = len(gold & pred) / len(gold) if gold else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if precision + recall else 0.0)
        rows.append(ScenePRF(scene_id=scene.meta.scene_id, gold=sorted(gold),
                             pred=sorted(pred), precision=round(precision, 4),
                             recall=round(recall, 4), f1=round(f1, 4)))
    return rows


def aggregate_prf(rows: list[ScenePRF]) -> dict:
    if not rows:
        return {"scenes": 0}
    n = len(rows)
    return {"scenes": n,
            "precision": round(sum(r.precision for r in rows) / n, 4),
            "recall": round(sum(r.recall for r in rows) / n, 4),
            "f1": round(sum(r.f1 for r in rows) / n, 4)}


def sample_relations(session, movie_id: str, n_per_type: int = 30,
                     seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    rows: list[dict] = []
    for rtype in RELATION_TYPES:
        if not isinstance(n_per_type, int) or n_per_type < 0:
            raise ValueError("n_per_type 必须为非负整数")
        recs = list(session.run(
            f"MATCH (a)-[r:{rtype}]->(b) WHERE a.movie_id = $movie "
            "RETURN a.id AS start, b.id AS end, properties(r) AS props",
            movie=movie_id))
        for rec in rng.sample(recs, min(n_per_type, len(recs))):
            rows.append({"type": rtype, "start": rec["start"], "end": rec["end"],
                         "props": rec["props"], "label": ""})
    return rows


def score_relation_samples(rows: list[dict]) -> dict[str, float]:
    by_type: dict[str, list[str]] = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r.get("label", ""))
    return {t: round(sum(1 for lb in ls if lb == "correct") / len(ls), 4)
            for t, ls in by_type.items() if ls}


def merge_labels(existing: list[dict], sampled: list[dict]) -> list[dict]:
    """将已有人工标注按 (type, start, end) 键合并进新抽样行;未匹配行保持空 label。"""
    labeled = {(r["type"], r["start"], r["end"]): r.get("label", "")
               for r in existing}
    merged = []
    for r in sampled:
        row = dict(r)
        row["label"] = labeled.get((r["type"], r["start"], r["end"]),
                                   r.get("label", ""))
        merged.append(row)
    return merged


def write_build_report(path, prf_rows: list[ScenePRF], agg: dict,
                       relation_scores: dict | None = None) -> None:
    lines = ["# 构建端评估报告\n", "## 角色识别(逐场景)\n",
             md_table(["场景", "gold", "pred", "P", "R", "F1"],
                      [[r.scene_id, ",".join(r.gold), ",".join(r.pred),
                        r.precision, r.recall, r.f1] for r in prf_rows]),
             "\n## 汇总\n",
             md_table(["指标", "值"], [[k, v] for k, v in agg.items()])]
    if relation_scores:
        lines += ["\n## 关系抽样正确率(人工标注)\n",
                  md_table(["关系类型", "accuracy"],
                           [[t, s] for t, s in relation_scores.items()])]
    lines.append("\n## 失败场景\n")
    lines.append("(见构建日志;无则略)\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
