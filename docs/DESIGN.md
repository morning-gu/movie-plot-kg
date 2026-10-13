# Movie Plot Knowledge Graph - 设计文档

> 基于 Qwen 系列大模型从电影数据构建剧情事件知识图谱,提供图增强检索问答,并对构建端与问答端进行双重量化评估

| 字段 | 内容 |
|-------|-------|
| 项目名称 | movie-plot-kg |
| 版本 | v0.2.0 |
| 创建日期 | 2026-10-12 |
| 最后更新 | 2026-10-12 |

---

## 目录

1. 项目概述
2. 系统架构
3. 输入数据规范
4. 知识图谱模式定义
5. 知识图谱构建流程
6. 存储与检索(Neo4j 单库)
7. 问答流程
8. 评估方案
9. 技术选型
10. 项目结构
11. 实现计划
- 附录 A: 实体 ID 生成规则
- 附录 B: 参考文献映射
- 附录 C: 修订记录

---

## 1. 项目概述

### 1.1 目标

构建一个基于大模型的系统,从单部中文电影的结构化数据中自动构建剧情事件知识图谱,并提供基于图谱的检索问答能力。系统覆盖:场景级事件抽取、全局关系建模、Neo4j 单库存储(图 + 向量)、模板化图增强问答,以及构建端与问答端的双重量化评估。

本项目为研究性项目,验证两个命题:

1. **构建端**: LLM 驱动的抽取管线能否从电影场景数据中产出高质量的事件图谱
2. **问答端**: 图增强检索(向量召回 + 图遍历)相对纯向量检索在剧情问答上的增益

### 1.2 范围界定(v0.2.0 收缩)

**做**:

- 单部电影全流程:输入校验 → 场景抽取 → 全局建模 → 图谱写入 → 向量化
- 单轮问答(CLI 交互,每问题固定 2 次 LLM 调用)
- 构建端与问答端评估及消融实验

**不做(future work)**:

- 多轮对话
- API 服务化(FastAPI)
- 异步任务队列(Celery)
- 多电影批量/增量构建(模式已预留 movie_id,流程按单电影设计)
- 事件共指 COREF(见 4.2.7)
- 音频模态直接输入(音频信息经台词文本进入,见 3.1)

### 1.3 核心能力

- **视觉-文本双模态抽取**: 每场景采样 ≤16 帧 + SRT 台词 + 场景元数据先验,经 Qwen3-VL 抽取结构化事件
- **事件知识图谱**: 6 类实体(Movie / Scene / Event / Character / VirtualTime / Location)、9 类关系,含全局时间线排序与角色别名归一
- **单库混合检索**: Neo4j 5.x 原生向量索引(语义召回)+ Cypher 图遍历(结构检索),无跨库同步
- **模板化图增强问答**: LLM 只做检索规划与总结,图查询从参数化模板库中选择,每问题固定 2 次 LLM 调用
- **两端量化评估**: 构建端对照人工标注,问答端 QA 集 + 消融实验

### 1.4 术语表

| 术语 | 定义 |
|------|------|
| 场景(Scene) | 同一时间同一地点发生的事件集合,抽取与评估的基本单元 |
| 事件(Event) | 场景内可独立描述的剧情行为单元;子事件亦为 Event |
| 虚拟时间(Virtual Time) | 剧情世界内部的时间,以 timeline_index 排成全序 |
| 帧采样 | 从场景视频中均匀抽取 ≤16 帧作为视觉输入,关键帧优先 |
| Checkpoint | 每场景抽取结果落盘(JSON),支持断点续跑 |
| 检索规划 | 问答第一次 LLM 调用,输出查询改写、目标实体与图查询模板选择 |
| 实体绑定 | 将 LLM 给出的名称提示解析为图中实体 ID 的过程 |
| MLLM | 多模态大语言模型 |
| KG / RAG | 知识图谱 / 检索增强生成 |

---

## 2. 系统架构

### 2.1 分层架构

```
CLI 交互层:    kg build | kg ask | kg eval                    (typer)
问答层:        [LLM-1 检索规划] -> [向量召回+实体绑定+模板图查询] -> [LLM-2 综合回答]
存储层:        Neo4j 5.x 单库 = 图数据 + 原生向量索引
构建层:        Phase A 场景级抽取(A1 事件 / A2 角色-角色关系,checkpoint)
               Phase B 全局 pass(B1 时间线排序 / B2 别名归一 / B3 因果抽取)
               Phase C 写入与向量化(C1 图写入 / C2 embedding / C3 全局序号)
数据输入层:    场景视频(MP4) + 场景划分 + 场景元数据(JSON) + 台词(SRT)
模型层:        Qwen3-VL(抽取) / Qwen3 系文本(规划、回答、全局 pass、评估判分)
               / Qwen3-Embedding(向量化) —— 全部经 OpenAI 兼容端点(SiliconFlow 类聚合平台)
```

### 2.2 数据流

```
场景视频 --帧采样(≤16)--> 帧 --┐
SRT 台词 ------------------------┤--> A1/A2(Qwen3-VL)--> checkpoints/*.json
场景元数据(先验) ----------------┘                          |
                                                           v
              B1/B2/B3 全局 pass(时间线 / 别名 / 因果) <---+
                                |
                                v
              C1 图写入(Neo4j, MERGE 幂等)
                                |
                                v
              C2 向量化(Qwen3-Embedding -> 节点 embedding 属性)
                                |
                                v
              问答就绪: kg ask -> LLM-1 规划 -> 检索执行 -> LLM-2 回答
              评估:     kg eval build / kg eval qa -> markdown 报告
```

---

## 3. 输入数据规范

目录布局:

```
data/movies/<movie_id>/
  video/scene_001.mp4 ...          # 按场景切割的视频片段
  subtitles/scene_001.srt ...      # 每场景台词(必备)
  scenes/scene_001.json ...        # 场景元数据(手工标注)
  checkpoints/scene_001.json ...   # 构建期生成(非输入)
```

### 3.1 输入 1: 场景视频(MP4)

唯一视觉来源。构建时每场景**均匀采样 ≤16 帧**;若元数据提供关键帧时间点(keyframes),优先包含后用均匀采样补足。

**音频轨不输入模型** —— 音频信息经 SRT 台词进入系统,不引入 Whisper。系统为"视觉帧 + 文本"双模态(原 v0.1 宣称"融合音频"名不副实,已修正)。

### 3.2 输入 2: 场景划分

场景切分点列表,每场景含 scene_id、起止时间戳。场景间顺序即播放顺序。

### 3.3 输入 3: 场景元数据(手工标注,JSON)

每个场景一个 JSON 文件,pydantic schema 校验:

```json
{
  "scene_id": "scene_001",
  "movie_id": "m1",
  "start_ts": "00:03:12",
  "end_ts": "00:05:40",
  "characters": [{"name": "张三", "aliases": ["老张"]}],
  "dialogues": [
    {"char": "张三", "text": "你到底想怎样?", "ts": "00:03:20",
     "emotion": "愤怒", "action": "拍桌"}
  ],
  "location": "码头仓库",
  "virtual_time": "第一天傍晚",
  "props": ["枪", "皮箱"],
  "keyframes": ["00:03:30", "00:04:45"]
}
```

必备字段:scene_id / movie_id / start_ts / end_ts / characters / dialogues / location / virtual_time;可选:aliases、emotion、action、props、keyframes。

**双重角色**: 抽取先验(角色名单用于名称对齐,标注地点/时间作为提示)+ 构建端评估 ground truth(见第 8 章)。

### 3.4 输入校验

`kg build` 首先跑 schema 校验:文件齐全、时间戳单调且在影片范围内、dialogues.char 能对齐到 characters(含 aliases)、SRT 时间范围与场景区间一致。校验失败即报错退出,不进入抽取。

---

## 4. 知识图谱模式定义

### 4.0 总则

- 所有业务实体携带 `movie_id` 属性,并连 `BELONGS_TO` 边指向 `:Movie` 节点;单电影内查询以属性过滤为准
- ID 生成规则见附录 A(全部含 movie 前缀);事件 ID 在 A1 解析后即按序确定性分配,checkpoint 中已含 ID
- 数值型排序字段(order / global_order / timeline_index)为构建期赋值的确定值

### 4.1 实体定义

#### 4.1.1 Movie

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | movie_id,如 m1 |
| title | string | 是 | 影片名 |

Neo4j label: `:Movie`

#### 4.1.2 Scene

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | scene_{movie}_{seq},如 scene_m1_003 |
| seq | int | 是 | 场景序号(播放顺序) |
| start_ts / end_ts | string | 是 | 起止时间戳 |
| annotated_location | string | 否 | 标注地点(评估对照用) |
| annotated_time | string | 否 | 标注虚拟时间 |
| summary | string | 否 | 场景概要(事件 topic 汇总生成) |

Neo4j label: `:Scene`(v0.1 中 SCENE_OF 指向未定义的"辅助节点",已正式节点化)

#### 4.1.3 Event

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | evt_{movie}_{scene_seq}_{seq} |
| event_type | string | 是 | dialogue / conflict / action / turning_point / discovery |
| topic | string | 是 | 一句话概括 |
| description | string | 是 | 完整描述 |
| scene_id | string | 是 | 所属场景(构建期确定值,与 SCENE_OF 边同源) |
| order | int | 是 | 场景内序号 |
| global_order | int | 是 | 全局序号(seq × 1000 + order),排序查询免递归 |
| is_subevent | bool | 是 | 是否为子事件(默认 false) |

时间与地点**不存节点属性,只建边**(OCCURS_AT / LOCATED_AT),避免双写漂移。

Neo4j label: `:Event`

#### 4.1.4 Character

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | char_{movie}_{md5(name)[:8]} |
| name | string | 是 | 规范名(标注优先) |
| aliases | string[] | 是 | 别名(B2 归一填入,可为空数组) |
| description | string | 是 | 身份/性格/背景(系统生成,别名合并后重生成) |

Neo4j label: `:Character`

#### 4.1.5 VirtualTime

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | vt_{movie}_{md5(name)[:8]} |
| name | string | 是 | 如 第一天傍晚 / 三天后 |
| description | string | 是 | 该时间点/段在剧情中的意义 |
| timeline_index | int | 是 | **全局时间线序号(B1 赋值,从 0 起)** |

v0.1 中 VirtualTime 节点彼此孤立("第一天傍晚"与"第二天清晨"无序关系),时间线碎片化;timeline_index 修复该问题。

Neo4j label: `:VirtualTime`

#### 4.1.6 Location

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | loc_{movie}_{md5(name)[:8]} |
| name | string | 是 | 地点名称 |
| aliases | string[] | 是 | 别名(B2 归一填入) |
| description | string | 是 | 环境、氛围说明 |

Neo4j label: `:Location`

### 4.2 关系定义

#### 4.2.1 PRECEDES(Event -> Event,链式)

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| gap | string | 否 | 时间间隔描述;仅跨场景断点边填写(取后场景标注 virtual_time),场景内为 null |

**链式连接**: 仅相邻事件连边 —— 场景内按 order 链接,跨场景由前场景末事件 → 后场景首事件。不做全对连接(n² 条边);后继/区间查询经可变长路径 + `global_order` 排序完成。

语义: 源事件在时序上紧邻先于目标事件。

#### 4.2.2 CAUSES(Event -> Event)

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| reason | string | 是 | 因果说明 |

由 B3 批量判定;检测窗口 = 同场景内事件对 + 相邻场景边界事件对。

#### 4.2.3 SUBEVENT_OF(Event -> Event)

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| scope | string | 否 | 子事件在父事件中的范围说明 |

**子事件是完整 Event 节点**: A1 抽取时即结构化输出(含 event_type / topic / description / participants),不是文本描述。约束: 父事件不应同时是另一事件的子事件(单层嵌套;多层嵌套标记告警)。

#### 4.2.4 PARTICIPATES_IN(Character -> Event)

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| emotion | string | 否 | 角色在此事件中的情绪,如 愤怒/悲伤/期待 |
| role_description | string | 否 | 角色作用,如 主动发起者/旁观者 |
| items | string[] | 否 | 使用的物品道具(道具不做实体) |

#### 4.2.5 RELATED_TO(Character - Character,单条边)

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| relation_type | string | 是 | friend / enemy / lover / family / mentor / other |
| start_event_id | string | 是 | 关系建立依据事件 |
| end_event_id | string | 否 | 关系失效依据事件 |
| valid | bool | 是 | 默认 true;关系变更时旧边置 false 并创建新边 |

**单条边存储**(创建时按先出现角色指向后出现角色,方向无语义);查询一律无方向匹配 `-[]-`。`valid` 过滤由查询模板内置,不暴露给 LLM(v0.1 用两条有向边模拟双向,已被替换)。

#### 4.2.6 辅助关系

| 关系 | 方向 | 说明 |
|------|------|------|
| BELONGS_TO | 全部实体 -> Movie | 归属 |
| SCENE_OF | Event -> Scene | 所属场景 |
| OCCURS_AT | Event -> VirtualTime | 发生时间 |
| LOCATED_AT | Event -> Location | 发生地点 |

#### 4.2.7 明确排除: COREF(事件共指)

理由: 单部电影、字幕驱动的场景化抽取中,同一事件的跨场景提及已通过场景元数据(角色/时间标注)间接对齐;显式共指链边际收益低,且标注与评估成本高。列为 future work。

---

## 5. 知识图谱构建流程

### 5.1 总览

```
kg build --movie m1 [--from-scratch] [--scenes 1-5]
  Phase A  逐场景: A1 事件抽取 -> A2 角色关系   (checkpoint 落盘,断点续跑)
  Phase B  全局:   B1 时间线排序 | B2 别名归一 | B3 因果抽取   (三者无依赖,可并行)
  Phase C  收尾:   C1 图写入 -> C2 向量化 -> C3 global_order
```

### 5.2 Phase A: 场景级抽取

#### A1 事件抽取(每场景 1 次调用)

输入: 采样帧(≤16,`image_url`)+ 本场景 SRT 台词全文 + 场景元数据先验(角色名单/标注地点/标注时间)
输出: `events[]` —— 事件与子事件均为结构化对象;participants 带 emotion / role_description / items

Prompt 模板(`config/prompts/event_extraction.md`):

```text
你是影视剧剧情分析专家。分析以下场景的帧序列与台词,提取剧情事件。

场景先验: 角色名单={...}, 标注地点={...}, 标注时间={...}
台词(SRT): {...}

要求:
- 事件按发生顺序排列;子事件同样输出完整结构,不输出纯文本描述
- 角色名必须从角色名单中选择;无法确定时标记 "未识别:原名"
- event_type ∈ dialogue / conflict / action / turning_point / discovery

输出 JSON:
{
  "events": [{
    "event_type": "...",
    "topic": "...",
    "description": "...",
    "participating_characters": [
      {"name": "...", "emotion": "...", "role_description": "...", "items": ["..."]}
    ],
    "subevents": [
      {"event_type": "...", "topic": "...", "description": "...",
       "participating_characters": ["...同上结构..."]}
    ]
  }]
}
```

#### A2 角色关系抽取(每场景 1 次调用)

输入: A1 事件摘要(带序号)+ 角色互动描述 + **已建关系表**(自已完成 checkpoint 汇总,断点续跑兼容)
输出: `character_relations[]` —— `is_new=true` 为新建;`false` 为变更(附 change_description);`evidence_event_index` 引用本场景 A1 事件序号,构建器据此映射为 start_event_id

A1/A2 拆分解决了 v0.1 单 Prompt 输出过载的问题(事件+子事件+角色关系挤 4096 token 上限)。

#### Checkpoint 与失败策略

- 每场景 A1+A2 结果写 `checkpoints/scene_{seq}.json`(含原始输出与解析后结构,便于回溯调试)
- 重跑自动跳过已完成场景;`--from-scratch` 忽略全部 checkpoint
- JSON 解析失败重试 3 次(temperature 0.1);仍失败标记该场景 `failed`,**不阻塞后续场景**,构建结束汇总 failed 清单
- **角色名对齐**: 以元数据角色名单为字典做模糊匹配(编辑距离 ≤2 或包含关系);未匹配保留 `未识别:原名` 并在日志告警,不静默丢弃

#### 成本控制

`kg build --movie m1 --scenes 1-5` 先试跑校准每场景 token 消耗,据此外推全片成本后再全量构建。成本公式: 每场景 ≈ 帧数 × 每帧 token + 台词/先验文本 + prompt 模板;全片 = 场景数 × (A1 + A2)。

### 5.3 Phase B: 全局 pass

| pass | 输入 | 输出 |
|------|------|------|
| B1 VT 时间线排序 | 全部 VT 名称(含出现场景上下文) | 全序列表 → 各 VT 赋 timeline_index |
| B2 别名归一 | 全部角色/地点名称 + 出现上下文样本 | merge_groups:{canonical, aliases[]};合并时 description 重生成 |
| B3 因果抽取 | 窗口内事件对(带 ID):同场景内 + 相邻场景边界 | [{cause_id, effect_id, reason}] → CAUSES 边 |

三者无依赖、可并行;均为 Qwen3 文本调用。B2 在 C1 之前执行,归一结果直接作用于图写入。

### 5.4 Phase C: 写入与向量化

1. **C1 图写入**: 应用 B1/B2 结果后按 checkpoint 顺序写入;全部 MERGE on id,幂等可重跑。跨场景 PRECEDES 断点边在此建立,gap 取后场景标注 virtual_time
2. **C2 向量化**: 实体描述文本 = 属性拼接(name / type / topic / description 等);Qwen3-Embedding 生成;写入节点 `embedding` 属性并建向量索引。**仅嵌入 4 类实体**(Event / Character / VirtualTime / Location);Scene/Movie 不嵌(场景级检索由 events_at_place/time 模板覆盖);关系不嵌(检索入口是实体,关系靠图遍历获得)
3. **C3 global_order**: 赋 `seq × 1000 + order`

---

## 6. 存储与检索(Neo4j 单库)

### 6.1 单库架构

Neo4j 5.13+ 同时承担图存储与向量检索:

- **图数据**: 6 类节点 + 9 类关系,Cypher 遍历 / 路径查询
- **向量索引**: 4 个 HNSW 索引(Event / Character / VirtualTime / Location 的 embedding 属性,cosine)

相对 v0.1 的 Neo4j + Qdrant 双库:跨库 ID 同步问题整体消失,Docker 服务减一,单电影规模(几百节点)下 Neo4j 向量检索性能充裕。

### 6.2 向量检索语义

```cypher
CALL db.index.vector.queryNodes('event_vec', 10, $query_vector)
YIELD node, score
RETURN node, score
```

注意: Neo4j 向量检索为**先 ANN 后过滤**。单电影几百节点无影响;多电影场景需按 movie_id 分库或分索引(future work 注记)。

### 6.3 检索策略

| 检索类型 | 示例 | 路径 |
|------|------|------|
| 语义检索 | 张三在哪里发怒了? | 向量召回(事件 topic / description) |
| 结构检索 | 张三和李四是什么关系? | 实体绑定 → relation_between_chars 模板 |
| 多跳推理 | 导致决裂的事件链是什么? | 向量定位事件 → causes_of 模板 |
| 时序检索 | 发现真相后发生了什么? | 向量定位事件 → events_after 模板 |
| 归属检索 | 码头发生了哪些事件? | 实体绑定 Location → events_at_place 模板 |

---

## 7. 问答流程

单轮,每问题固定 2 次 LLM 调用(v0.1 为 5 次串行调用)。

### 7.1 流程

```
用户问题
   |
   v
[LLM-1 检索规划]  (Qwen3, 结构化 JSON 输出)
   |
   v
[纯代码执行, 0 次 LLM]
   1. 向量召回: rewritten_query -> embedding -> 4 个向量索引各 Top-5, 合并
   2. 实体绑定: name_hint -> name/aliases 精确匹配; 失败则取向量召回中最近的同类实体
   3. 图查询: 执行所选模板(每问题上限 2 个), valid 过滤与排序内置
   |
   v
[LLM-2 综合回答]  (Qwen3)
   |
   v
回答 + 引用实体 ID 列表
```

### 7.2 LLM-1: 检索规划

输出 JSON(pydantic 校验):

```json
{
  "intent": "factual | causal | relational | temporal | synthesis",
  "rewritten_query": "向量检索查询文本",
  "target_entities": [{"type": "Character|Event|VirtualTime|Location", "name_hint": "张三"}],
  "graph_queries": [{"template_id": "causes_of", "params": {"id": "决裂", "depth": 3}}]
}
```

意图类型对齐评估的 5 类问题(见 8.2),保证消融实验可分意图统计。

### 7.3 模板库(7 个)

| template_id | Cypher 骨架(示意) | 参数 |
|------|------|------|
| relation_between_chars | `MATCH (a:Character)-[r:RELATED_TO*1..2]-(b:Character) WHERE a.id=$a AND b.id=$b AND all(x IN r WHERE x.valid) RETURN a, r, b` | a, b(实体绑定后 ID) |
| events_after | `MATCH (e1:Event)-[:PRECEDES*1..$depth]->(e2:Event) WHERE e1.id=$id RETURN e2 ORDER BY e2.global_order` | id, depth(默认 3,上限 5) |
| causes_of | `MATCH p=(c:Event)-[:CAUSES*1..$depth]->(e:Event) WHERE e.id=$id RETURN p` | id, depth |
| events_of_char | `MATCH (c:Character)-[r:PARTICIPATES_IN]->(e:Event) WHERE c.id=$id RETURN e, r ORDER BY e.global_order` | id, emotion(可选过滤) |
| events_at_place | `MATCH (e:Event)-[:LOCATED_AT]->(l:Location) WHERE l.id=$id RETURN e ORDER BY e.global_order` | id |
| events_at_time | `MATCH (e:Event)-[:OCCURS_AT]->(vt:VirtualTime) WHERE vt.id=$id RETURN e ORDER BY e.global_order` | id |
| subevents_of | `MATCH (s:Event)-[:SUBEVENT_OF]->(p:Event) WHERE p.id=$id RETURN s` | id |

**安全边界**: 模板参数仅接受实体绑定产出的 ID、枚举值、深度整数(1–5);LLM 全程不生成 Cypher 文本,无注入面。v0.1 由 LLM 自由生成 Cypher(注入、语法错误、valid 过滤遗漏)的风险由此消除。

### 7.4 LLM-2: 综合回答

输入: 问题 + 证据集(向量召回结果 + 模板查询结果,均含实体 ID)
要求:

- 仅基于证据回答,不编造;证据不足时明确说明缺什么
- 因果/时序问题按逻辑顺序组织
- 文末列出引用实体 ID(可追溯)

### 7.5 多轮对话

v0.2 不实现。机制预留:LLM-1 的输入加入对话历史即可扩展(规划阶段天然支持上下文),列为 future work。

---

## 8. 评估方案

### 8.1 构建端(ground truth = 场景元数据标注)

| 指标 | 方法 | 自动化程度 |
|------|------|------|
| 角色识别 P / R / F1 | 每场景:标注角色名单 vs 抽取事件的参与角色(别名归一后匹配) | 全自动 |
| 事件完整性 / 正确性 | 随机抽 10 个场景,人工对照标注判**漏抽**与**幻觉** | 抽样人评 |
| 关系正确率 | 每类关系(PRECEDES / CAUSES / SUBEVENT_OF / PARTICIPATES_IN / RELATED_TO)各随机抽 30–50 条,人工判对错,报每类 accuracy | 抽样人评 |
| 时间线一致性(可选) | timeline_index vs 人工排序,Kendall's tau | 半自动 |

### 8.2 问答端

**QA 集**(`eval/qa_set.yaml`,手工编写): 50–100 题,5 类意图(factual / causal / relational / temporal / synthesis)每类 ≥10 题;每题含 question / gold_answer / gold_entity_ids / intent。

**指标**:

- **answer accuracy**: LLM-as-judge(Qwen3)按 rubric 判对/错并给出理由;随机抽 20% 人工复核
- **retrieval hit rate**: gold_entity_ids 中出现在证据集的比例

**消融实验**: 同一 QA 集跑两条配置 —— 纯向量(禁用图模板) vs 向量+图,分意图类型对比 accuracy 与 hit rate。这是"图增强是否有效"命题(1.1)的直接证据。

### 8.3 工具

```
kg eval build --movie m1                 # 构建端指标 -> eval/build_report.md
kg eval qa --qa-set eval/qa_set.yaml \
           --mode {full | vector-only}   # 问答端 -> eval/qa_report.md
```

报告为 markdown,含指标汇总表与失败案例列表。

---

## 9. 技术选型

| 组件 | 选型 | 说明 |
|------|------|------|
| MLLM(抽取) | Qwen3-VL 系列 | SiliconFlow 类聚合平台,OpenAI 兼容 `image_url` 帧输入;具体型号经 `config/models.yaml` 配置 |
| 文本 LLM | Qwen3 系列 | 规划 / 回答 / B1–B3 / 评估判分,同端点 |
| Embedding | Qwen3-Embedding 系列 | OpenAI 兼容 `/embeddings`;首选 0.6B / 1024 维;若端点无 Qwen 系 embedding,备选 bge-m3 |
| 图数据库 | Neo4j 5.13+ | 图 + 原生向量索引,单库 |
| 开发语言 | Python 3.11+ | |
| LLM 编排 | openai SDK + pydantic | 结构化输出;不引入 LangChain |
| CLI | typer | `kg build / ask / eval`;不引入 FastAPI |
| 视频处理 | ffmpeg + Pillow / PyAV | 帧采样;不引入 Whisper |
| 任务处理 | 同步执行 + JSONL checkpoint | 不引入 Celery / Redis |

### MLLM 调用规格

| 参数 | 值 | 说明 |
|------|------|------|
| 帧采样 | 每场景 ≤16 帧 | 均匀采样,关键帧标注优先包含 |
| temperature | 0.1 | 低温度保证抽取一致性 |
| max_tokens | 4096 | A1/A2 拆分后单次输出减半,不再紧张 |
| 重试 | 3 次 | JSON 校验失败自动重试 |
| 成本校准 | 前 5 场景试跑外推 | 见 5.2 |

### 图数据库索引与约束

```cypher
CREATE INDEX event_id IF NOT EXISTS FOR (e:Event) ON (e.id);
CREATE INDEX character_id IF NOT EXISTS FOR (c:Character) ON (c.id);
CREATE INDEX character_name IF NOT EXISTS FOR (c:Character) ON (c.name);
CREATE INDEX location_id IF NOT EXISTS FOR (l:Location) ON (l.id);
CREATE INDEX virtualtime_id IF NOT EXISTS FOR (vt:VirtualTime) ON (vt.id);
CREATE INDEX scene_id IF NOT EXISTS FOR (s:Scene) ON (s.id);
CREATE INDEX event_scene_id IF NOT EXISTS FOR (e:Event) ON (e.scene_id);
CREATE INDEX event_movie_id IF NOT EXISTS FOR (e:Event) ON (e.movie_id);
-- character / virtualtime / location / scene 的 movie_id 索引同理

CREATE CONSTRAINT event_unique IF NOT EXISTS FOR (e:Event) REQUIRE e.id IS UNIQUE;
CREATE CONSTRAINT char_unique IF NOT EXISTS FOR (c:Character) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT loc_unique IF NOT EXISTS FOR (l:Location) REQUIRE l.id IS UNIQUE;
CREATE CONSTRAINT vt_unique IF NOT EXISTS FOR (vt:VirtualTime) REQUIRE vt.id IS UNIQUE;
CREATE CONSTRAINT scene_unique IF NOT EXISTS FOR (s:Scene) REQUIRE s.id IS UNIQUE;

CREATE VECTOR INDEX event_vec IF NOT EXISTS FOR (e:Event) ON (e.embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity`: 'cosine'}};
-- character_vec / virtualtime_vec / location_vec 结构同理
```

向量维度须与 `config/models.yaml` 中的 embedding 型号一致。

---

## 10. 项目结构

```
movie-plot-kg/
  docs/DESIGN.md
  src/movie_kg/
    config.py
    models/            # pydantic: entities, relations, scene, extraction
    ingestion/         # data_loader(schema 校验), frame_sampler
    extraction/        # event_extractor(A1), relation_extractor(A2), global_passes(B1-B3)
    graph/             # schema(DDL), writer(C1/C3), query_templates(7 个)
    embeddings/        # embedder(C2)
    qa/                # planner(LLM-1), retriever(向量召回+实体绑定), answerer(LLM-2)
    eval/              # build_eval, qa_eval, judge, report
    cli.py             # kg build / ask / eval
  config/
    neo4j.yaml
    models.yaml        # 端点与型号(可按平台可用型号调整)
    prompts/           # event_extraction, relation_update, vt_ordering,
                       # alias_merge, causal_detection, qa_planning, qa_answering, judge
  data/movies/<movie_id>/
  eval/                # qa_set.yaml, build_report.md, qa_report.md
  tests/
  pyproject.toml
  .env.example
  README.md
```

---

## 11. 实现计划

### 11.1 阶段划分(18 人日,全职专注估算;兼职按比例放大)

| 阶段 | 内容 | 估时 | 交付物 |
|------|------|------|------|
| P0 | Neo4j Docker + 项目脚手架 + CLI 骨架 | 1d | 可运行骨架 |
| P1 | 数据 JSON Schema + 校验加载器 + 帧采样器 | 2d | ingestion 模块 |
| P2 | A1/A2 场景抽取 + checkpoint | 3d | extraction 模块 + Prompt 模板 |
| P3 | B1–B3 全局 pass + C1 图写入 | 3d | global_passes + graph/writer |
| P4 | C2 向量化 + 向量索引 | 1d | embeddings 模块 |
| P5 | QA 管线(LLM-1/2 + 7 模板) | 3d | qa 模块 |
| P6 | QA 集编写 + eval 命令 | 3d(另需 1–2 天人工写题) | eval 模块 + qa_set |
| P7 | 端到端跑通 + 评估报告 + 调优 | 2d | 评估报告 |

注: 电影标注(输入 3)的人工时间不在估算内。

### 11.2 验收标准(初始阈值,跑出基线后校准)

1. 单部电影全自动构建;人为中断后重跑,断点续跑行为正确
2. 构建端: 角色识别 F1 ≥ 0.9;关系抽样正确率 ≥ 0.8
3. 问答端: QA 集 answer accuracy ≥ 0.7;causal / temporal 类问题上"向量+图"优于"纯向量"
4. 每个问题恰好 2 次 LLM 调用;全片构建成本可由校准公式预估(误差 ±30% 内)

### 11.3 依赖项

```
neo4j>=5.14.0          # Neo4j Python driver
openai>=1.30.0         # SiliconFlow 等 OpenAI 兼容端点
pydantic>=2.7.0
typer>=0.12.0
pillow>=10.0.0
av>=12.0.0             # 帧提取(或以 ffmpeg 子进程替代)
pyyaml>=6.0
python-dotenv>=1.0.0
pytest>=8.0            # dev
```

---

## 附录 A: 实体 ID 生成规则

| 实体 | 格式 | 示例 |
|------|------|------|
| Movie | {movie_id} | m1 |
| Scene | scene_{movie}_{seq:03d} | scene_m1_003 |
| Event | evt_{movie}_{scene_seq}_{seq} | evt_m1_003_02 |
| Character | char_{movie}_{md5(name)[:8]} | char_m1_a1b2c3d4 |
| VirtualTime | vt_{movie}_{md5(name)[:8]} | vt_m1_e5f67890 |
| Location | loc_{movie}_{md5(name)[:8]} | loc_m1_12345678 |

## 附录 B: 参考文献映射

| 文献 | 对应设计部分 |
|------|------|
| Drammar 戏剧本体(Damiano et al., 2019) | 角色-事件关系、角色情感属性的概念基础 |
| MAVEN-ERE(Wang et al., EMNLP 2022) | 事件关系类型(时序/因果/子事件)的定义;共指的排除决策 |
| What is EKG Survey(Guan et al., TKDE 2022) | 事件知识图谱本体和模式归纳方法 |
| Labatut & Bost(ACM Computing Surveys, 2019) | 角色-角色关系类型和构建流程 |
| Movie Scene Event Extraction(Yi et al., 2023) | 电影场景事件论元角色概念 |
| NEEG(Li et al., IJCAI 2018) | 事件演化图和事件间关系网络化 |
| Event-QA(Costa et al., 2018) | 事件中心 KG 问答架构 |
| GraphThinker(Cheng et al., 2026) | 事件图思维增强视频推理 |
| ExCAR(Du et al., ACL 2021) | 事件图知识增强因果推理 |

## 附录 C: 修订记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1.0 | 2026-10-12 | 初稿: GPT-4o + Neo4j/Qdrant 双库 + 5 步 QA + API 服务 |
| v0.2.0 | 2026-10-12 | 范围收缩到核心闭环(CLI、单轮、砍 FastAPI/Celery/LangChain/Whisper);单库 Neo4j(原生向量索引)替代双库;模型栈切换为 Qwen 系列(SiliconFlow 端点);QA 5 步收敛为 2 次 LLM 调用 + 7 个参数化图查询模板(LLM 不再生成 Cypher);构建流程重构为 3 Phase(A 场景级双 Prompt + JSONL checkpoint,B 全局 pass:时间线/别名/因果,C 写入向量化);图谱模式修订(新增 Movie/Scene 实体与 movie_id、VirtualTime.timeline_index、PRECEDES 链式、结构化子事件、RELATED_TO 单边、COREF 明确排除);输入规范修订(音频删除、帧预算 ≤16、元数据 JSON Schema 定稿并兼作评估 ground truth);新增第 8 章评估方案(构建端 + 问答端 + 消融)与量化验收标准 |
| v0.2.1 | 2026-10-14 | §7.2 勘误:检索规划示例参数键 event_hint→id(与模板参数契约一致) |

---

*文档版本: v0.2.1 | 最后更新: 2026-10-14*
