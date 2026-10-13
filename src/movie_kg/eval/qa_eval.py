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


def load_qa_set(path: str | Path) -> list[QAItem]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return [QAItem.model_validate(i) for i in raw["items"]]


def run_qa(session, llm, items: list[QAItem],
           mode: Literal["full", "vector-only"]) -> list[QAResult]:
    if mode not in ("full", "vector-only"):
        raise ValueError(f"mode 必须为 full 或 vector-only: {mode!r}")
    results: list[QAResult] = []
    for item in items:
        plan = plan_retrieval(llm, item.question)
        if mode == "vector-only":
            plan = plan.model_copy(update={"graph_queries": []})
        evidence = retrieve(session, llm, plan)
        result = answer(llm, item.question, evidence)
        evidence_ids = [e.node_id for e in evidence]
        hit = (len(set(item.gold_entity_ids) & set(evidence_ids))
               / len(item.gold_entity_ids)) if item.gold_entity_ids else None
        results.append(QAResult(
            item_id=item.id, intent=item.intent, answer=result.answer,
            cited_ids=result.cited_ids, evidence_ids=evidence_ids,
            hit_rate=round(hit, 4) if hit is not None else None))
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
    return {
        "total": total,
        "judged": len(judged),
        "overall_accuracy": (round(sum(int(r.judged_correct) for r in judged)
                                   / len(judged), 4) if judged else None),
        "mean_hit_rate": (round(sum(hits) / len(hits), 4) if hits else None),
        "per_intent": {k: {"n": v["n"],
                           "accuracy": round(v["correct"] / v["n"], 4)}
                       for k, v in per_intent.items()},
    }


def write_qa_report(path: str | Path, results: list[QAResult], summary: dict,
                    mode: str = "full") -> None:
    lines = ["# 问答端评估报告\n", f"\n运行模式: {mode}\n", "## 汇总\n",
             md_table(["指标", "值"], [[k, v] for k, v in summary.items()
                                       if k != "per_intent"])]
    if summary.get("per_intent"):
        lines += ["\n## 分意图\n",
                  md_table(["意图", "n", "accuracy"],
                           [[k, v["n"], v["accuracy"]]
                            for k, v in summary["per_intent"].items()])]
    lines += ["\n## 逐题\n",
              md_table(["id", "intent", "judged", "hit_rate", "answer"],
                       [[r.item_id, r.intent, r.judged_correct, r.hit_rate,
                         r.answer[:50]] for r in results]),
              "\n失败案例(judged=false):\n"]
    for r in results:
        if r.judged_correct is False:
            lines.append(f"- **{r.item_id}**({r.judge_reason}): {r.answer}\n")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
