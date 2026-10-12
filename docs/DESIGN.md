# Movie Plot Knowledge Graph - 设计文档

> 基于多模态大模型构建影视剧情知识图谱并提供问答能力

| 字段 | 内容 |
|-------|-------|
| 项目名称 | movie-plot-kg |
| 版本 | v0.1.0 |
| 创建日期 | 2026-10-12 |
 |

---

## 目录

1. 项目概述
2. 系统架构
3. 输入数据规范
4. 知识图谱模式定义
5. 知识图谱构建流程
6. 向量数据库与图数据库关联
7. 问答流程
8. 技术选型
9. 项目结构
10. 实现计划

---

## 1. 项目概述

### 1.1 目标

构建一个基于多模态大模型(MLLM)的系统,从原始电影数据中自动构建剧情事件知识图谱,并提供基于图谱的智能问答能力。系统覆盖从多模态数据摄入、事件实体与关系抽取、知识图谱与向量库双库存储、到多轮检索增强问答的完整链路。

### 1.2 核心能力

- **多模态理解**: 融合视频片段、音频、台词文本和关键帧,提取剧情事件结构化信息
- **知识图谱构建**: 自动提取事件、角色、虚拟时间、地点四类实体,以及时序先后、因果关系、子事件关系、参与关系、关联关系五类关系
- **双库协同**: 图数据库存储结构化拓扑关系,向量数据库存储语义嵌入,支持混合检索
- **智能问答**: LLM 驱动的多轮检索 -- 向量召回 + 图遍历 + 过滤总结 -- 回答关于剧情的复杂问题

### 1.3 术语表

| 术语 | 定义 |
|------|------|
| 场景(Scene) | 同一时间同一地点发生的事件集合,是知识图谱构建的基本单元 |
| 事件(Event) | 场景内可独立描述的剧情行为单元 |
| 虚拟时间(Virtual Time) | 剧情世界内部的时间,区别于电影播放时间戳 |
| MLLM | 多模态大语言模型(Multimodal Large Language Model) |
| KG | 知识图谱(Knowledge Graph) |
| RAG | 检索增强生成(Retrieval-Augmented Generation) |

---

## 2. 系统架构

### 2.1 分层架构

```
用户问答层:    [问题理解] -> [向量召回] -> [图遍历] -> [总结回答]
检索存储层:    [向量数据库(Qdrant)] <--- ID关联 ---> [图数据库(Neo4j)]
构建处理层:    [场景事件识别] -> [事件-事件成图] -> [角色-事件成图] -> [角色-角色成图] -> [Embedding生成+双库写入]
数据输入层:    [原始电影数据] [场景标注划分点] [场景元数据]
```

### 2.2 数据流

```
原始电影数据 --> MLLM多模态理解 --> 场景事件结构化输出
    --> 实体与关系抽取
    --> 事件-事件/角色-事件/角色-角色 关系图构建
    --> 图数据库写入 --> Embedding生成 --> 向量数据库写入 --> 双库ID关联 --> 问答服务就绪
```

---

## 3. 输入数据规范

### 3.1 原始电影数据(输入1)

对齐好的多模态数据,按时间轴对齐:

| 数据类型 | 格式 | 说明 |
|------|--------|-------------|
| 视频片段 | MP4/WebM | 按场景切割的视频,含时间戳 |
| 音频轨 | WAV/MP3 | 与视频对齐的音轨,含对白和环境音 |
| 台词文本 | SRT/ASS/JSON | 带时间戳的字幕/台词文件 |
| 关键帧 | JPG/PNG | 按场景提取的代表性关键帧图片 |

### 3.2 场景划分标注(输入2)

基于播放顺序拆分的场景划分点,每个场景为同一时间同一地点发生的事件集合。

### 3.3 场景元数据标注(输入3)

每个场景的标注信息,包括角色及其台词列表(含情绪和动作)、剧情地点和时间、物品道具等。

---

## 4. 知识图谱模式定义

### 4.1 实体定义

#### 4.1.1 Event

| 属性 | 类型 | 必填 | 说明 |
|----------|------|----------|-------------|
| id | string | 是 | 事件唯一标识,格式 evt_{scene_id}_{seq} |
| event_type | string | 是 | 事件类型,如 对话/冲突/行动/转折/发现 |
| topic | string | 是 | 事件主题,一句话概括事件核心内容 |
| description | string | 是 | 事件描述,完整描述事件发生了什么 |
| scene_id | string | 是 | 所属场景ID |
| virtual_time_id | string | 否 | 关联的虚拟时间ID |
| location_id | string | 否 | 关联的地点ID |
| order | int | 是 | 事件在场景内的序号 |

Neo4j label: `:Event`

#### 4.1.2 Character

| 属性 | 类型 | 必填 | 说明 |
|----------|------|----------|-------------|
| id | string | 是 | 角色唯一标识,格式 char_{md5(name)[:8]} |
| name | string | 是 | 角色名称 |
| description | string | 是 | 角色描述,包括身份、性格、背景等 |

Neo4j label: `:Character`

#### 4.1.3 VirtualTime

| 属性 | 类型 | 必填 | 说明 |
|----------|------|----------|-------------|
| id | string | 是 | 时间唯一标识,格式 vt_{md5(name)[:8]} |
| name | string | 是 | 时间名称,如第一天傍晚/三天后 |
| description | string | 是 | 时间描述,说明该时间点/段在剧情中的意义 |

Neo4j label: `:VirtualTime`

#### 4.1.4 Location

| 属性 | 类型 | 必填 | 说明 |
|----------|------|----------|-------------|
| id | string | 是 | 地点唯一标识,格式 loc_{md5(name)[:8]} |
| name | string | 是 | 地点名称 |
| description | string | 是 | 地点描述,包括环境、氛围等 |

Neo4j label: `:Location`

### 4.2 关系定义

#### 4.2.1 PRECEDES (Event -> Event, unidirectional)

| 属性 | 类型 | 说明 |
|----------|------|-------------|
| gap | string | 时间间隔描述,如紧接着/第二天/三小时后 |

语义: 源事件在时序上先于目标事件发生。

#### 4.2.2 CAUSES (Event -> Event, unidirectional)

| 属性 | 类型 | 说明 |
|----------|------|-------------|
| reason | string | 因果说明,描述为什么源事件导致了目标事件 |

语义: 源事件直接或间接导致了目标事件的发生。

#### 4.2.3 SUBEVENT_OF (Event -> Event, unidirectional)

| 属性 | 类型 | 说明 |
|----------|------|-------------|
| scope | string | 子事件在父事件中的范围说明 |

语义: 源事件是目标事件的组成部分或子环节。

#### 4.2.4 PARTICIPATES_IN (Character -> Event, unidirectional)

| 属性 | 类型 | 说明 |
|----------|------|-------------|
| emotion | string | 角色在此事件中的情绪状态,如愤怒/悲伤/期待 |
| role_description | string | 角色在此事件中的作用描述,如主动发起者/旁观者 |
| items | string[] | 角色在此事件中使用的物品道具列表 |

语义: 角色参与了该事件,并携带情绪和角色功能信息。

#### 4.2.5 RELATED_TO (Character <-> Character, bidirectional)

| 属性 | 类型 | 说明 |
|----------|------|-------------|
| relation_type | string | 关系类型,如朋友/敌人/恋人/亲属/师徒 |
| start_event_id | string | 关系开始的触发事件ID |

语义: 两个角色之间存在某种社会关系。双向关系通过创建两条方向相反的边表示。start_event_id指向关系首次建立的事件,关系变更时标记旧边失效并创建新边。

### 4.3 辅助关系(构建过程需要)

| 关系 | 方向 | 说明 |
|----------|-----------|-------------|
| OCCURS_AT | 事件 -> 虚拟时间 | 事件发生的虚拟时间 |
| LOCATED_AT | 事件 -> 地点 | 事件发生的地点 |
| SCENE_OF | 事件 -> 场景 | 事件所属场景(场景为辅助节点) |

---

## 5. 知识图谱构建流程

### 5.1 流程总览

```
输入数据 -> [Step 1: 场景事件识别] -> [Step 2: 事件-事件成图]
         -> [Step 3: 角色-事件成图] -> [Step 4: 角色-角色成图]
         -> [Step 5: Embedding生成 + 双库写入]
```

### 5.2 Step 1: 场景事件识别

目标: 从每个场景的多模态数据中提取结构化的事件信息。

处理步骤:
1. 多模态数据组装: 将场景的视频片段(1fps采样)、音频(Whisper转写)、关键帧图片和台词文本组装为MLLM的多模态输入
2. Prompt构造: 构造结构化抽取Prompt,要求MLLM输出JSON格式的事件信息
3. MLLM调用: 调用多模态大模型(如GPT-4o),输入多模态数据和抽取Prompt
4. 输出解析: 解析MLLM返回的JSON,提取事件实体和关联信息

MLLM抽取Prompt模板:

```text
你是一位影视剧剧情分析专家。请分析场景的多模态数据,提取剧情事件。

场景信息: scene_id, order, annotated location, virtual time, characters, dialogues, props
多模态数据: video frames, audio, keyframes, dialogue text

输出JSON:
{
  "events": [{
    "event_type": "dialogue/conflict/action/turning_point/discovery",
    "topic": "事件一句话概括",
    "description": "事件详细描述",
    "participating_characters": [{
      "name": "角色名称",
      "emotion": "角色在此事件中的情绪",
      "role_description": "角色在此事件中的作用",
      "items": ["使用的物品道具"]
    }],
    "subevents": ["子事件描述1", "子事件描述2"]
  }],
  "character_relations": [{
    "char_a": "角色A",
    "char_b": "角色B",
    "relation_type": "friend/enemy/lover/family/mentor",
    "is_new": true,
    "change_description": "关系变更描述(如适用)"
  }]
}
```

异常处理: MLLM输出格式错误时重试(最多3次); 场景无事件时标记为过渡场景; 角色名不一致时以标注为准。

### 5.3 Step 2: 事件-事件成图

目标: 按时序、因果、子事件三类关系将事件插入知识图谱。

1. 时序关系构建: 场景内事件按order顺序建立PRECEDES边; 跨场景按场景顺序建立; 通过虚拟时间推断时间间隔gap
2. 因果关系构建: 使用MLLM进行事件对因果推理(滑动窗口策略,仅在相邻N个事件内检测); MLLM判断事件A是否导致事件B
3. 子事件关系构建: 基于Step 1中MLLM输出的subevents字段; 将子事件描述与主事件匹配,建立SUBEVENT_OF边

### 5.4 Step 3: 角色-事件成图

目标: 建立角色与事件的参与关系,携带情绪、作用和物品属性。

1. 角色实体去重与合并: 同一部电影中同一角色名只创建一个Character节点; 后续场景中补充更新描述
2. 参与关系建立: 基于Step 1输出,为每个角色-事件对创建PARTICIPATES_IN边; 边属性包括emotion/role_description/items
3. 事件关联实体: 将事件通过OCCURS_AT和LOCATED_AT边关联虚拟时间和地点; 实体做去重(同名合并)

### 5.5 Step 4: 角色-角色成图

目标: 建立和更新角色间的关系。

1. 关系建立: 基于is_new=true的条目; 创建双向RELATED_TO边,包含relation_type和start_event_id
2. 关系变更: 当is_new=false时; 标记旧边valid=false/end_event_id=当前事件; 创建新边
3. 关系推断: 通过MLLM推断未显式标注的角色关系; 推断结果标记inferred=true

### 5.6 Step 5: Embedding生成与双库写入

目标: 为所有实体和关系生成描述文本的向量嵌入,写入向量数据库,并与图数据库建立ID关联。

1. 描述文本组装: 实体描述文本 = 实体所有属性的拼接; 关系描述文本 = 关系类型 + 两端实体描述 + 关系属性
2. Embedding生成: 调用embedding模型(text-embedding-3-large,3072维)生成向量
3. 向量库写入: 向量记录payload含entity_id/entity_type/text/scene_id/movie_id等字段
4. 双库ID关联: 向量库payload中的entity_id与图数据库节点id一致; 图节点增加embedding_id属性

---

## 6. 向量数据库与图数据库关联

### 6.1 双库架构

向量数据库(Qdrant): 支持语义相似度检索、元数据过滤、Top-K召回。Payload中含entity_id。
图数据库(Neo4j): 支持多跳图遍历、路径查询、子图提取。
通过entity_id关联: 向量库payload中的entity_id与图数据库节点id一致。

### 6.2 检索策略

| 检索类型 | 适用场景 | 使用数据库 | 示例 |
|------|----------|---------|---------|
| 语义检索 | 张三在哪里发怒了? | 向量库 | 向量相似度召回愤怒+张三相关事件 |
| 结构检索 | 张三和李四是什么关系? | 图数据库 | 遍历 Character->RELATED_TO->Character |
| 多跳推理 | 导致决裂的事件链? | 图库+向量库 | 先向量召回决裂事件,再图遍历CAUSES链 |
| 时序检索 | 发现真相后发生了什么? | 图数据库 | 从发现事件沿PRECEDES遍历 |

---

## 7. 问答流程

### 7.1 流程总览

```
用户问题
    |
    v
[Step 1] LLM问题理解 -> 识别需要检索的实体类型和关系类型
    |
    v
[Step 2] LLM生成向量查询 -> 向量库召回Top-K相关实体/关系
    |
    v
[Step 3] LLM过滤召回结果 -> 判断是否需要图数据库补充检索
    |
    +--> [不需要] -> 直接进入Step 5
    v [需要]
[Step 4] LLM生成图查询语句 -> 图库执行遍历/路径查询
    |
    v
[Step 5] LLM过滤+总结 -> 生成最终回答
```

### 7.2 Step 1: 问题理解与实体关系识别

LLM分析用户问题,识别需要检索的实体类型、关系类型和关键信息。

输出: 意图分类(事实查询/因果推理/关系查询/时序追踪/综合分析)、目标实体(类型/描述/关键词)、目标关系(类型/描述)、是否需要图遍历(布尔值)、遍历提示(方向和深度)。

### 7.3 Step 2: 向量库召回

LLM将检索计划转换为向量查询描述文本; 使用embedding模型将查询文本转为向量; 在向量库中执行Top-K相似度检索; 可选使用payload元数据过滤。

### 7.4 Step 3: 结果过滤与图检索判断

LLM过滤向量召回结果,并判断是否需要进一步查询图数据库。

决策逻辑:
- 问题涉及多跳关系 -> graph_query_needed=true
- 问题涉及路径追踪(事件链) -> graph_query_needed=true
- 问题仅涉及单个实体属性 -> is_sufficient=true

### 7.5 Step 4: 图数据库检索

LLM根据graph_query_description和已有召回的实体ID生成Cypher查询; 在Neo4j中执行; 将查询结果(子图或路径)返回给LLM。

Cypher查询示例:

```cypher
// 角色关系链
MATCH (a:Character {name: "Zhang"})-[r:RELATED_TO*1..3]-(b:Character {name: "Li"})
WHERE r.valid = true OR r.valid IS NULL
RETURN a, r, b

// 某事件之后的事件
MATCH (e1:Event)-[:PRECEDES*1..5]->(e2:Event)
WHERE e1.id = $event_id
RETURN e1, e2 ORDER BY e2.order

// 导致某事件的因果链
MATCH path = (cause:Event)-[:CAUSES*1..5]->(effect:Event)
WHERE effect.id = $event_id
RETURN path
```

### 7.6 Step 5: 过滤与总结回答

LLM综合所有检索结果,过滤无关信息,生成最终回答。

回答要求: 仅基于检索结果回答,不编造; 不足时明确说明缺少什么信息; 因果和时序问题按逻辑顺序组织; 引用来源实体/关系ID以支持可追溯性。

### 7.7 多轮问答支持

系统维护对话上下文,支持多轮追问: 每轮问答后存入对话历史; 下一轮问题时LLM可参考对话历史进行实体消歧; 实体引用通过ID关联,避免重复检索。

---

## 8. 技术选型

| 组件 | 技术选型 | 备选 | 选择理由 |
|-----------|--------|-------------|-----------|
| 多模态大模型 | GPT-4o | Claude 3.5 Sonnet, Qwen-VL-Max | 支持视频帧+图片+文本多模态输入 |
| Embedding模型 | text-embedding-3-large | bge-large-zh, gte-large | 多语言,维度3072,语义区分度好 |
| 图数据库 | Neo4j 5.x | NebulaGraph, ArangoDB | Cypher成熟,生态丰富,GDS图算法库 |
| 向量数据库 | Qdrant | Milvus, Chroma, Weaviate | payload过滤灵活,与Neo4j集成好 |
| 开发语言 | Python 3.11+ | - | ML/NLP生态最佳 |
| LLM编排框架 | LangChain | LlamaIndex | 社区活跃,Agent+Tool抽象完善 |
| API框架 | FastAPI | Flask | 异步支持,自动文档 |
| 任务队列 | Celery+Redis | - | 异步处理长耗时KG构建任务 |

### MLLM调用规格

| 参数 | 值 | 说明 |
|-----------|-------|-------------|
| 视频帧采样 | 1fps | 每秒取1帧送入MLLM |
| 关键帧上限 | 10张/场景 | 超过时均匀采样 |
| 音频处理 | Whisper转写 | 音频先转文本再送入 |
| max_tokens | 4096 | 输出上限 |
| temperature | 0.1 | 低温度保证抽取一致性 |
| 重试次数 | 3 | 格式错误自动重试 |

### 图数据库索引与约束

```cypher
CREATE INDEX event_id IF NOT EXISTS FOR (e:Event) ON (e.id);
CREATE INDEX character_id IF NOT EXISTS FOR (c:Character) ON (c.id);
CREATE INDEX character_name IF NOT EXISTS FOR (c:Character) ON (c.name);
CREATE INDEX location_id IF NOT EXISTS FOR (l:Location) ON (l.id);
CREATE INDEX virtualtime_id IF NOT EXISTS FOR (vt:VirtualTime) ON (vt.id);
CREATE INDEX event_scene_id IF NOT EXISTS FOR (e:Event) ON (e.scene_id);

CREATE CONSTRAINT event_unique IF NOT EXISTS FOR (e:Event) REQUIRE e.id IS UNIQUE;
CREATE CONSTRAINT char_unique IF NOT EXISTS FOR (c:Character) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT loc_unique IF NOT EXISTS FOR (l:Location) REQUIRE l.id IS UNIQUE;
CREATE CONSTRAINT vt_unique IF NOT EXISTS FOR (vt:VirtualTime) REQUIRE vt.id IS UNIQUE;
```

---

## 9. 项目结构

```
movie-plot-kg/
  docs/DESIGN.md
  src/
    config.py, models/{entities,relations,scene}.py
    ingestion/{data_loader,multimodal}.py
    kg_builder/{event_extractor,event_graph,char_event,char_char,embedding,graph_writer}.py
    qa/{query_planner,vector_retriever,result_filter,graph_retriever,answer_generator}.py
    db/{neo4j_client,qdrant_client}.py
    api/server.py
  config/{neo4j.yaml,qdrant.yaml,prompts/}
  data/movies/
  tests/
  requirements.txt, .env.example, README.md
```

---

## 10. 实现计划

### 10.1 阶段划分

| 阶段 | 内容 | 预估时间 | 交付物 |
|-------|---------|------|-------------|
| P0 | 基础设施搭建: Neo4j+Qdrant+项目脚手架 | 2天 | 可运行的项目骨架 |
| P1 | 输入数据模型与加载器 | 1天 | data_loader+multimodal模块 |
| P2 | Step 1 场景事件识别(MLLM抽取) | 3天 | event_extractor模块+Prompt模板 |
| P3 | Step 2-4 成图逻辑(图写入) | 3天 | graph_writer+三个成图模块 |
| P4 | Step 5 Embedding+双库写入 | 2天 | embedding+qdrant_client |
| P5 | QA Step 1-3(问题理解+向量召回+过滤) | 3天 | query_planner+vector_retriever+result_filter |
| P6 | QA Step 4-5(图检索+总结回答) | 2天 | graph_retriever+answer_generator |
| P7 | API服务+端到端测试 | 2天 | FastAPI server+测试用例 |
| P8 | 优化与文档 | 2天 | README+性能优化 |

### 10.2 验收标准

1. 给定电影多模态数据和标注,系统能自动构建包含四类实体和五类关系的知识图谱
2. 图数据库中事件节点按时序、因果、子事件关系正确连接
3. 角色参与关系携带情绪、作用和物品属性
4. 角色关联关系正确建立并支持变更追踪
5. 所有实体和关系的描述文本已embedding入向量库
6. 用户可通过自然语言提问,系统经向量召回+图遍历后返回正确答案
7. 对于多跳推理问题(如导致决裂的事件链),系统能正确生成Cypher并遍历图谱

### 10.3 依赖项

```
neo4j>=5.14.0
qdrant-client>=1.9.0
openai>=1.30.0
langchain>=0.2.0
langchain-openai>=0.1.0
pydantic>=2.7.0
fastapi>=0.110.0
uvicorn>=0.29.0
celery>=5.4.0
redis>=5.0.0
pillow>=10.0.0
av>=12.0.0
openai-whisper>=20231117
python-dotenv>=1.0.0
```

---

## 附录A: 实体ID生成规则

| 实体 | ID格式 | 示例 |
|--------|-----------|---------|
| Event | evt_{scene_id}_{seq} | evt_scene001_03 |
| Character | char_{md5(name)[:8]} | char_a1b2c3d4 |
| VirtualTime | vt_{md5(name)[:8]} | vt_e5f67890 |
| Location | loc_{md5(name)[:8]} | loc_12345678 |

## 附录B: 参考文献映射

| 文献 | 对应设计部分 |
|-----------|-------------|
| Drammar戏剧本体(Damiano et al., 2019) | 角色意图、事件-角色关系的概念基础 |
| MAVEN-ERE(Wang et al., EMNLP 2022) | 事件四种关系类型(时序/因果/子事件/共指)的定义 |
| What is EKG Survey(Guan et al., TKDE 2022) | 事件知识图谱本体和模式归纳方法 |
| Labatut & Bost(ACM Computing Surveys, 2019) | 角色-角色关系类型和构建流程 |
| Movie Scene Event Extraction(Yi et al., 2023) | 电影场景事件论元角色和SRC概念 |
| NEEG(Li et al., IJCAI 2018) | 事件演化图和事件间关系网络化 |
| Event-QA(Costa et al., 2018) | 事件中心KG问答架构 |
| GraphThinker(Cheng et al., 2026) | 事件图思维强化视频推理 |
| ExCAR(Du et al., ACL 2021) | 事件图知识增强因果推理 |

---

*文档版本: v0.1.0 | 最后更新: 2026-10-12*
