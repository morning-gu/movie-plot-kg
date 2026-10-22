# 按片问答(movie-scoped QA)设计

日期:2026-10-22 · 状态:已与用户对齐能力地图,待 builder 审查

## 意图与动机

问答系统目前只有全局检索一种模式:向量索引跨片共享,一部片的问题会捞到别片
事件(实证:rd_t1 的 top 命中全是别片)。用户要求两个场景并存:

- **场景 A · 按剧情描述搜片**:`kg ask "..."` 不指定电影,保持现状(全局检索);
- **场景 B · 指定电影问答**:`kg ask --movie tt0117500 "..."`,检索与回答只发生
  在该片子图内。

用户明确否决了"先全局检索再按 movie_id 过滤"(超采)方案,要求**真·片内检索**。
本设计同时修复遗留的跨片向量污染问题,并给出量化其影响的对照实验。

## 目标 / 非目标 / 反目标

**目标**
1. `ask --movie <tt_id>`:片内精确向量检索 + 片内实体绑定 + 校验影片存在
2. `eval qa`:每题自动隔离到所属片(默认),`--scope global` 保留旧测法,
   `--movies` 可只跑指定片
3. 全局模式行为**逐字节不变**(场景 A + 基线可比性)
4. 对照实验:同一 60 题集 scoped vs global,量化污染对 accuracy/hit_rate 的影响

**非目标(本轮不做)**
- 回答端/规划端提示词改动(含"当前电影是《XX》"上下文注入)——上两轮实证
  回答端改动易回归,如做须独立测量
- 专用"剧情搜片"UX(返回 Movie 节点排名)——场景 A 继续由全局 ask 兼任
- 按片标签 + 按片 ANN 向量索引——单片规模到万级才需要(见演进路径)
- 新影片的 QA 集生成

**反目标(做了即失败)**
- 全局模式任何行为变化(检索查询、绑定、分数)
- 动 schema / 重建图谱 / 重新嵌入
- scoped 模式出现"查不到本片结果时静默回落全局"——宁空不污染

## 已验证的事实基础(2026-10-22 真库实测)

| 事实 | 依据 |
|---|---|
| `vector.similarity.cosine()` 在 Neo4j 5.26.12 可用 | cypher-shell 实测 |
| 该函数返回 **(1+cos)/2 ∈ [0,1]**(非裸余弦) | 4 个探针点拟合(正交=0.5、相同=1、相反=0、对角=0.8536) |
| 片内精确扫描性能:Rock 1299 事件瞬时返回 | 成本 O(单片规模) × 1024 维 ≈ 1.3M 次浮点运算,与库内总片数无关 |
| 5 类标签的 `movie_id` B-tree 索引已存在 | `schema.py:6-10`,建库时创建,至今闲置 |
| 所有实体节点携带 `movie_id` 属性 | `writer.py` 逐类 SET(Event/Character/VirtualTime/Location/Scene) |
| **跨片同名角色现在就有**(非假设风险) | 7 个"未识别:xxx"占位名跨片碰撞("未识别:群演"3 片全有、`Agent` 在 Hackers+Rock);当前 bind 精确层就会随机绑到别片 |
| 图模板天然按片安全 | 全部按完整节点 ID 查询,ID 自带 tt 前缀;PRECEDES/CAUSES 链写图时按片构建 |
| 各片规模:RD 185 事件/21 角色、Hackers 579/94、Rock 1299/152 | 真库 COUNT |

## 方案

### 检索层(`qa/retriever.py`)

`vector_recall(session, llm, query, top_k=10, movie_id=None)`:

- `movie_id=None`:现有 ANN 路径**原样保留**(4 个 `db.index.vector.queryNodes` 调用);
- `movie_id` 有值:每标签一次片内精确扫描(标签来自现有 `INDEXES` 常量,非用户输入,
  可安全内插):

```cypher
MATCH (n:{label} {movie_id: $mid})
WHERE n.embedding IS NOT NULL
WITH n, vector.similarity.cosine(n.embedding, $vec) AS score
ORDER BY score DESC LIMIT $k
```

返回 `Evidence` 形状不变。**分数语义注意**:scoped 分数是 (1+cos)/2 重标度,
与 ANN 路径的裸 cosine 不可直接对比;当前无任何下游按分数阈值分叉(回答端优化
已整包回退),`Evidence.score` 仅展示用。

### 绑定层(同文件)

`bind_entities(session, targets, vector_hits, movie_id=None)`,三级全部收窄:

1. 精确层:`MATCH (n:{type}) WHERE n.movie_id = $mid AND (n.name = $h OR $h IN n.aliases) RETURN n.id AS id LIMIT 1`
2. 词级扫描层:`MATCH (n:{type}) WHERE n.movie_id = $mid RETURN n.id AS id, n.name AS name`(其余逻辑不变)
3. 向量回落层:不改——`vector_hits` 在 scoped 模式下已是片内结果

`movie_id=None` 时三级查询与现状逐字节一致。

### 计划层(`qa/planner.py`)

`RetrievalPlan` 增加 `movie_id: str | None = None` 字段;`plan_retrieval(llm,
question, movie_id=None)` 原样透传。**scope 由调用方注入,LLM 不参与**;规划
提示词一个字不动(改写查询含片名反而给嵌入添噪——事件描述里本就没有片名)。

`retrieve(session, llm, plan, top_k=10)` 签名不变,从 `plan.movie_id` 读 scope
下传两层。

### 评测层(`eval/qa_eval.py`)

- `run_qa(..., scope="global")`:**函数默认 global**——库层保持今日语义,现有
  测试直接测全局路径,而非借"无 gold 回落"的意外路径间接覆盖"全局不变"约束;
  CLI 的 `--scope` 默认 movie 才是产品面默认。scope=movie 时每题从
  `gold_entity_ids[0]` 用 `re.search(r"tt\d+", ...)` 推导 movie_id 注入 plan;
  无 gold 的题该题回落全局并计日志(当前 60 题全有 gold,此为防御)。
  scope=global 时全程 None。
- `QAResult` 增加 `movie_id: str | None = None`(scoped 时填充);报告在 scoped
  模式下增加**按片汇总表**(n / accuracy / mean_hit_rate)——"指定电影评测"的
  直接产物就是每部的分数。
- 报告文件名:`eval/qa_report_{mode}{scope 后缀}{movies 后缀}{k 后缀}.md`;
  scope=movie 加 `_scoped`(如 `qa_report_full_scoped.md`),global 沿用旧命名
  保证历史可比;`--movies` 过滤时追加 `_m{id1-id2…}`(如
  `qa_report_full_scoped_mtt0117500.md`)——**过滤运行绝不覆盖未过滤报告**:
  `qa_report_full.md` 是 0.5667 基线工件,被 `--scope global --movies …` 静默
  改写是本设计必须堵住的洞(builder 审查指出)。

### CLI(`cli.py`)

- `ask --movie <tt_id>`(可选):先 `MATCH (m:Movie {id:$mid})` 校验,不存在则
  报错并列出库内可用影片(id+title);`tt\d+` 正则先验格式。
- `eval qa --scope {movie,global}`(默认 movie)+ `--movies tt…,tt…`(按推导的
  movie 过滤题目,两种 scope 下都可用)。

## 实验与验收

1. **单测**:scoped 检索查询形状(每标签一次扫描、带 $mid、不走 ANN)、scoped
   绑定三级、plan 透传、run_qa 推导与回落、CLI 旗标与报错、报告命名。
2. **真库冒烟**(必做,经验:Mock 覆盖不了真库形状):沿用 `integration`
   pytest 标记 + `skipif(not NEO4J_URI)` 惯例(tests/test_writer_integration.py、
   pyproject markers);scoped ask 单题,断言证据 node_id 全部带目标片 tt 前缀;
   冒烟所用的嵌入必须 1024 维(ScriptedLLM 默认 8 维在真库 cosine 上会炸)。
3. **对照实验**(交付物):同一 60 题、k=10,scope=movie vs scope=global,
   产出 `eval/SCOPED_QA_REPORT.md`(沿既有惯例手工撰写,不做生成工具):
   总 accuracy/hit_rate 对照 + 按片分解 + 失败题变化清单。global 腿**复跑取
   新数为权威**(全局路径代码不变,复跑即复现性证明;历史 0.5667 引作参照);
   实验前先把工作区未提交的 eval 报告改动提交掉,避免基线工件歧义。
   **预期 scoped ≥ global**(污染是纯噪声);若反而下降是重要发现,
   说明某些题在靠别片证据得分,须逐题归因。

## 约束

- Neo4j 5.26.12 Community 单库;嵌入 1024 维(Qwen3-Embedding-0.6B)
- 全局模式行为不变是硬约束(回归测试保证)
- 遵循项目 SDD 流程:本设计 → 计划(`superpowers:writing-plans`)→ TDD 执行
- DESIGN.md 变更记录按项目惯例补一条

## 留给实现者的决定

- scoped 扫描查询的参数命名/结构细节(单次跑 4 标签还是复用循环)——两可
- `--movies` 过滤为空交集时的行为(报错退出即可)
- 测试中 MockSession 对 scoped 查询的断言粒度(建议断言 Cypher 片段含
  `movie_id` 参数而非全串比对,降低脆性)

## 演进路径(记录但不做)

- **按片标签 + 按片 ANN 索引**(每片 4 个向量索引):触发条件是单片节点规模
  到万级(精确扫描变慢)或需要跨片 ANN;当前 1299 事件上限差一个数量级
- **专用搜片 UX**:Movie 节点(含剧情概要嵌入)排名返回——场景 A 的正形
- **回答端电影上下文注入**:独立实验,单独测量

## builder 审查记录(2026-10-22)

全新视角审查结论:接近可直接出计划;承重事实在代码中复核全部成立
(`Evidence.score` 无下游消费、改动函数的调用方全在设计涉及的文件内、
60 题 gold 全带 tt 前缀、B-tree 索引属实、实验报告沿手工撰写惯例)。
提出两问,裁决如下:

1. **`--movies` 过滤运行的报告文件名**:原模板无 movies 组成部分,过滤运行会
   静默覆盖 `qa_report_full.md` 基线工件 → 裁决为文件名追加 `_m{ids}` 后缀,
   并要求实验前提交工作区既有改动、global 腿复跑取新数为权威。
2. **`run_qa` 库层默认 scope**:若函数默认 movie,现有测试只借"无 gold 回落"
   意外通过,全局不变约束变成间接覆盖 → 裁决为函数默认 global、CLI 默认 movie,
   现有裸调用保持原语义直接覆盖全局路径。
