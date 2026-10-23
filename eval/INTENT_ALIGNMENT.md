# plan.intent 对齐测量(2026-10-23,前置门)

## 混淆矩阵(行=题面 intent,列=plan.intent)

| 题面\plan | causal | factual | relational | temporal |
|---|---|---|---|---|
| causal | 12 | 0 | 0 | 0 |
| factual | 3 | 8 | 1 | 0 |
| relational | 0 | 1 | 11 | 0 |
| synthesis | 6 | 3 | 3 | 0 |
| temporal | 6 | 1 | 0 | 5 |

**危险方向(题面非 temporal → plan 判为 temporal): 0 题**
(漏判方向 temporal → 非 temporal: 7 题,仅损失机会不破坏守门)

## 逐题

| id | 题面 intent | plan.intent |
|---|---|---|
| rd_f1 | factual | factual |
| rd_f2 | factual | causal |
| rd_f3 | factual | factual |
| rd_f4 | factual | relational |
| rd_c1 | causal | causal |
| rd_c2 | causal | causal |
| rd_c3 | causal | causal |
| rd_c4 | causal | causal |
| rd_r1 | relational | relational |
| rd_r2 | relational | relational |
| rd_r3 | relational | relational |
| rd_r4 | relational | relational |
| rd_t1 | temporal | temporal |
| rd_t2 | temporal | temporal |
| rd_t3 | temporal | causal |
| rd_t4 | temporal | causal |
| rd_s1 | synthesis | factual |
| rd_s2 | synthesis | relational |
| rd_s3 | synthesis | relational |
| rd_s4 | synthesis | causal |
| hk_f1 | factual | factual |
| hk_f2 | factual | factual |
| hk_f3 | factual | factual |
| hk_f4 | factual | factual |
| hk_c1 | causal | causal |
| hk_c2 | causal | causal |
| hk_c3 | causal | causal |
| hk_c4 | causal | causal |
| hk_r1 | relational | relational |
| hk_r2 | relational | relational |
| hk_r3 | relational | relational |
| hk_r4 | relational | factual |
| hk_t1 | temporal | temporal |
| hk_t2 | temporal | causal |
| hk_t3 | temporal | causal |
| hk_t4 | temporal | temporal |
| hk_s1 | synthesis | causal |
| hk_s2 | synthesis | causal |
| hk_s3 | synthesis | factual |
| hk_s4 | synthesis | relational |
| rk_f1 | factual | factual |
| rk_f2 | factual | factual |
| rk_f3 | factual | causal |
| rk_f4 | factual | causal |
| rk_c1 | causal | causal |
| rk_c2 | causal | causal |
| rk_c3 | causal | causal |
| rk_c4 | causal | causal |
| rk_r1 | relational | relational |
| rk_r2 | relational | relational |
| rk_r3 | relational | relational |
| rk_r4 | relational | relational |
| rk_t1 | temporal | temporal |
| rk_t2 | temporal | causal |
| rk_t3 | temporal | factual |
| rk_t4 | temporal | causal |
| rk_s1 | synthesis | causal |
| rk_s2 | synthesis | factual |
| rk_s3 | synthesis | causal |
| rk_s4 | synthesis | causal |

门判定:危险方向 0 题 ≤2,通过

(危险明细: )
