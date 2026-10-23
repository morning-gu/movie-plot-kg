# -*- coding: utf-8 -*-
"""plan.intent 对齐测量(temporal 窗口前置门)。

用法:.venv/Scripts/python.exe scripts/measure_intent_alignment.py
产物:eval/INTENT_ALIGNMENT.md(混淆矩阵 + 逐题表 + 危险方向计数)

危险方向 = 题面 intent 非 temporal 而 plan.intent == "temporal"
(窗口会在这些题上生效,破坏「非 temporal 48 题路径逐字节不变」的守门归因;
spec:误判 >2 题则停止实现、回报用户)。
"""
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from movie_kg.config import load_models_config
from movie_kg.eval.qa_eval import load_qa_set, movie_of_item
from movie_kg.llm import LLMClient
from movie_kg.qa.planner import plan_retrieval

items = load_qa_set(REPO / "eval" / "qa_set.yaml")
llm = LLMClient(load_models_config(REPO / "config" / "models.yaml"))

matrix: Counter = Counter()
rows = []
for it in items:
    plan = plan_retrieval(llm, it.question, movie_id=movie_of_item(it))
    matrix[(it.intent, plan.intent)] += 1
    rows.append((it.id, it.intent, plan.intent))
    print(f"{it.id}: {it.intent} -> {plan.intent}", flush=True)

gold_intents = sorted({g for g, _ in matrix})
plan_intents = sorted({p for _, p in matrix})
lines = ["# plan.intent 对齐测量(2026-10-23,前置门)", "",
         "## 混淆矩阵(行=题面 intent,列=plan.intent)", "",
         "| 题面\\plan | " + " | ".join(plan_intents) + " |",
         "|---" * (len(plan_intents) + 1) + "|"]
for g in gold_intents:
    lines.append(f"| {g} | " + " | ".join(str(matrix[(g, p)]) for p in plan_intents) + " |")
danger = [(i, g, p) for i, g, p in rows if g != "temporal" and p == "temporal"]
missed = [(i, g, p) for i, g, p in rows if g == "temporal" and p != "temporal"]
lines += ["", f"**危险方向(题面非 temporal → plan 判为 temporal): {len(danger)} 题**",
          f"(漏判方向 temporal → 非 temporal: {len(missed)} 题,仅损失机会不破坏守门)",
          "", "## 逐题", "", "| id | 题面 intent | plan.intent |", "|---|---|---|"]
lines += [f"| {i} | {g} | {p} |" for i, g, p in rows]
lines += ["", f"门判定:危险方向 {len(danger)} 题 {'≤2,通过' if len(danger) <= 2 else '>2,停止并回报'}",
          "", "(危险明细: " + ", ".join(i for i, _, _ in danger) + ")"]
out = REPO / "eval" / "INTENT_ALIGNMENT.md"
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"\n危险方向 {len(danger)} 题 → {out}")
