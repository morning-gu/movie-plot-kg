import logging
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from movie_kg.eval.report import md_table
from movie_kg.prompts import load_prompt, render
from movie_kg.qa.answerer import answer
from movie_kg.qa.planner import plan_retrieval
from movie_kg.qa.retriever import retrieve


class QAItem(BaseModel):
    id: str
    question: str
    gold_answer: str
    gold_entity_ids: list[str] = []
    intent: str


class JudgeVerdict(BaseModel):
    correct: bool
    reason: str = ""


class QAResult(BaseModel):
    item_id: str
    intent: str
    answer: str
    cited_ids: list[str] = []
    evidence_ids: list[str] = []
    hit_rate: float | None = None
    judged_correct: bool | None = None
    judge_reason: str = ""
    # scope=movie 时该题的隔离影片;global / 推导不出时为 None
    movie_id: str | None = None


def load_qa_set(path: str | Path) -> list[QAItem]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return [QAItem.model_validate(i) for i in raw["items"]]


def movie_of_item(item: QAItem) -> str | None:
    """从 gold 实体 ID 的 tt 前缀推导所属影片(evt_tt0105236_001_01 → tt0105236)。"""
    if not item.gold_entity_ids:
        return None
    m = re.search(r"tt\d+", item.gold_entity_ids[0])
    return m.group() if m else None


def run_qa(session, llm, items: list[QAItem],
           mode: Literal["full", "vector-only"],
           top_k: int = 10,
           scope: Literal["movie", "global"] = "global") -> list[QAResult]:
    if mode not in ("full", "vector-only"):
        raise ValueError(f"mode 必须为 full 或 vector-only: {mode!r}")
    if scope not in ("movie", "global"):
        raise ValueError(f"scope 必须为 movie 或 global: {scope!r}")
    results: list[QAResult] = []
    for item in items:
        # 库层默认 global(builder 审查裁决):裸调用保持历史语义;
        # CLI 层的 --scope 默认 movie 才是产品面默认
        movie_id = None
        if scope == "movie":
            movie_id = movie_of_item(item)
            if movie_id is None:
                # 防御:gold 推导不出影片(无 gold / 无 tt 前缀)→ 该题回落
                # 全局并留痕,不静默也不崩(当前 60 题全有 gold)
                logging.getLogger(__name__).warning(
                    "题目 %s 推导不出影片,该题回落全局检索", item.id)
        plan = plan_retrieval(llm, item.question, movie_id=movie_id)
        if mode == "vector-only":
            plan = plan.model_copy(update={"graph_queries": []})
        evidence = retrieve(session, llm, plan, top_k=top_k)
        result = answer(llm, item.question, evidence)
        evidence_ids = [e.node_id for e in evidence]
        hit = (len(set(item.gold_entity_ids) & set(evidence_ids))
               / len(item.gold_entity_ids)) if item.gold_entity_ids else None
        results.append(QAResult(
            item_id=item.id, intent=item.intent, answer=result.answer,
            cited_ids=result.cited_ids, evidence_ids=evidence_ids,
            hit_rate=round(hit, 4) if hit is not None else None,
            movie_id=movie_id))
    return results


def judge_answers(llm, items: list[QAItem], results: list[QAResult]) -> list[QAResult]:
    by_id = {i.id: i for i in items}
    cfg = getattr(llm, "cfg", None)
    model = cfg.text_model if cfg is not None else "text"
    out: list[QAResult] = []
    for r in results:
        item = by_id[r.item_id]
        system = render(load_prompt("judge"), QUESTION=item.question,
                        GOLD=item.gold_answer, SYSTEM=r.answer)
        verdict = llm.chat_json(model=model,
                                messages=[{"role": "system", "content": system},
                                          {"role": "user", "content": "请输出判定 JSON。"}],
                                schema=JudgeVerdict)
        out.append(r.model_copy(update={"judged_correct": verdict.correct,
                                        "judge_reason": verdict.reason}))
    return out


def summarize(results: list[QAResult]) -> dict:
    total = len(results)
    judged = [r for r in results if r.judged_correct is not None]
    per_intent: dict[str, dict] = {}
    for r in judged:
        d = per_intent.setdefault(r.intent, {"n": 0, "correct": 0})
        d["n"] += 1
        d["correct"] += int(r.judged_correct)
    hits = [r.hit_rate for r in results if r.hit_rate is not None]
    # 按片汇总(scope=movie 且可推导影片的结果才有 movie_id)
    acc: dict[str, dict] = {}
    for r in results:
        if not r.movie_id:
            continue
        d = acc.setdefault(r.movie_id, {"n": 0, "correct": 0,
                                        "hit_sum": 0.0, "hit_n": 0})
        if r.judged_correct is not None:
            d["n"] += 1
            d["correct"] += int(r.judged_correct)
        if r.hit_rate is not None:
            d["hit_sum"] += r.hit_rate
            d["hit_n"] += 1
    per_movie = {mid: {"n": d["n"],
                       "accuracy": (round(d["correct"] / d["n"], 4)
                                    if d["n"] else None),
                       "mean_hit_rate": (round(d["hit_sum"] / d["hit_n"], 4)
                                         if d["hit_n"] else None)}
                 for mid, d in acc.items()}
    return {
        "total": total,
        "judged": len(judged),
        "overall_accuracy": (round(sum(int(r.judged_correct) for r in judged)
                                   / len(judged), 4) if judged else None),
        "mean_hit_rate": (round(sum(hits) / len(hits), 4) if hits else None),
        "per_intent": {k: {"n": v["n"],
                           "accuracy": round(v["correct"] / v["n"], 4)}
                       for k, v in per_intent.items()},
        "per_movie": per_movie,
    }


def write_qa_report(path: str | Path, results: list[QAResult], summary: dict,
                    mode: str = "full") -> None:
    lines = ["# 问答端评估报告\n", f"\n运行模式: {mode}\n", "## 汇总\n",
             md_table(["指标", "值"], [[k, v] for k, v in summary.items()
                                       if k not in ("per_intent", "per_movie")])]
    if summary.get("per_intent"):
        lines += ["\n## 分意图\n",
                  md_table(["意图", "n", "accuracy"],
                           [[k, v["n"], v["accuracy"]]
                            for k, v in summary["per_intent"].items()])]
    if summary.get("per_movie"):
        lines += ["\n## 按片\n",
                  md_table(["影片", "n", "accuracy", "mean_hit_rate"],
                           [[k, v["n"], v["accuracy"], v["mean_hit_rate"]]
                            for k, v in summary["per_movie"].items()])]
    lines += ["\n## 逐题\n",
              md_table(["id", "intent", "judged", "hit_rate", "answer"],
                       [[r.item_id, r.intent, r.judged_correct, r.hit_rate,
                         r.answer[:50]] for r in results]),
              "\n失败案例(judged=false):\n"]
    for r in results:
        if r.judged_correct is False:
            lines.append(f"- **{r.item_id}**({r.judge_reason}): {r.answer}\n")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
