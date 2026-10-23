# temporal 宽窗口(ID 序时间窗)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** temporal 意图问题在检索阶段获得「锚点事件之后」的 ID 序时间窗证据,使 temporal 12 题 judged 从 2/12 升至 ≥6/12(B2 线,触发面实测 5 题)。

**Architecture:** 在 retriever 新增 `temporal_window(session, hits)`:对 Event 向量命中按 LOCATED_AT 多数票选锚点组(空名地点不计票),锚点取组内(场景号,序号)最大事件;从锚点按本片 ID 全序向后取「锚点同场景残余 + 后续事件(预算 24,遇第 2 个非空异组地点停)」,生成带时间序前缀的 Evidence。`retrieve()` 仅在 `plan.intent == "temporal"` 时触发;窗口行覆盖同 node_id 旧行。不走 PRECEDES 链、不用 Scene 节点。

**Tech Stack:** Python 3.11 / pydantic / Neo4j(Cypher split+toInteger 解析事件 ID)/ pytest(Mock 体系 + 真库集成标记)

**Spec:** `docs/superpowers/specs/2026-10-23-temporal-window-design.md`(v2,已批准;本计划从该 spec 展开,冲突以 spec 为准)

## Global Constraints

- planner 提示词**零改动**(spec 反目标)
- 非 temporal 检索路径**逐字节不变**:`retrieve()` 仅在 `plan.intent == "temporal"` 时发出窗口查询
- 不走 PRECEDES 链、不用 Scene 节点;Location 仅作软停止信号,**空名地点不计票不构段**(spec 图谱事实 3-6)
- 锚点解析自事件 ID 的 movie 前缀,窗口永远不跨片(含全局模式)
- 窗口行 `Evidence(source="temporal_window")`,text 前缀 `【时间序 {n}·场景{场景号}】`;同 node_id 时**覆盖**向量/图证据行
- 常量:`WINDOW_BUDGET = 24`、`WINDOW_STOP_LOCATIONS = 2`、`WINDOW_WALK_LIMIT = 60`(retriever 模块级)
- 成功线(B2,2026-10-23 修订):temporal judged ≥**6/12** 且触发面(5 题:rd_t1/rd_t2/hk_t1/hk_t4/rk_t1,rd_t4/hk_t3 不在触发面)mean hit_rate ≥0.5;非 temporal 48 题净变动 ≤±3;校准仅一轮(预算 24→32 或停止 2→3),复跑一次
- 集成测试嵌入须 **1024 维**(ScriptedLLM 默认 8 维会炸 cosine);全量评测后台跑(约 35-40 分钟)

## Review Focus

(spec 隐含但各任务测试未必覆盖、最可能咬人的五类输入;每条已钉到归属任务)

1. **plan.intent 把非 temporal 题误判为 temporal** → 窗口在守门集外生效,±3 归因失效 → Task 1 的测量门(误判 >2 题即停,回报不实现)
2. **全局模式锚点被别片事件抢走** → 窗口必须留在锚点本片 → Task 2 `test_temporal_window_movie_from_anchor_id`(锚点 ID 是 tt0117500,断言窗口查询 `mid="tt0117500"`)
3. **锚点位于全片末尾 / 窗口查询零结果** → 返回空列表,evidence 不变不崩 → Task 2 `test_temporal_window_empty_rows_returns_empty`
4. **空名 Location 超级节点(d41d8cd9,301 事件)赢得多数票** → 必须排除计票 → Task 2 vote 测试内含空名行断言
5. **预算与停止信号都不触发(超长同地点段)** → WINDOW_WALK_LIMIT=60 查询上限兜底,窗口按序截断不崩 → Task 2 `test_temporal_window_budget_cap`(24 截断)

---

### Task 1: plan.intent 对齐测量(前置门)

**Files:**
- Create: `scripts/measure_intent_alignment.py`
- Create(运行产物): `eval/INTENT_ALIGNMENT.md`

**Interfaces:**
- Consumes: `plan_retrieval(llm, question, movie_id)`、`load_qa_set`、`movie_of_item`、`LLMClient(load_models_config())`
- Produces: `eval/INTENT_ALIGNMENT.md`(混淆矩阵 + 逐题表 + 危险方向误判数)

一次性测量工具,无 TDD(非产品代码;验证=真跑)。planner 的 intent 从未被下游实测,守门归因依赖它(spec 机制设计·触发)。

- [ ] **Step 1: 写脚本**

脚本要点(体量 ~50 行,直接可写):逐题 `plan_retrieval(llm, it.question, movie_id=movie_of_item(it))`;统计 `Counter[(题面 intent, plan.intent)]`;输出 markdown:混淆矩阵表、逐题 `id/题面/plan` 表、以及**危险方向计数** = 题面非 temporal 且 plan.intent=="temporal" 的题数。脚本头部注释写明:`.venv/Scripts/python.exe scripts/measure_intent_alignment.py`,产物写 `eval/INTENT_ALIGNMENT.md`。

- [ ] **Step 2: 运行并落盘**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/measure_intent_alignment.py`
Expected: `eval/INTENT_ALIGNMENT.md` 生成,60 行逐题表完整(60 次 planner 调用,约 5-10 分钟)

- [ ] **Step 3: 门判定(spec:误判率 >2 题则先回报)**

读产物中的危险方向计数。
Expected: ≤2 → 记录数字,进 Task 2;>2 → **停止,向用户回报**(提示词不可动,只能调整归因规则后再议)

- [ ] **Step 4: Commit**

```bash
git add scripts/measure_intent_alignment.py eval/INTENT_ALIGNMENT.md
git commit -m "chore: plan.intent 对齐测量(前置门)——混淆矩阵落盘"
```

### Task 2: temporal_window 核心函数

**Files:**
- Modify: `src/movie_kg/qa/retriever.py`(Evidence.source 扩展 + 常量 + 函数)
- Test: `tests/test_retriever.py`

**Interfaces:**
- Consumes: `Evidence`(本文件);`_node_text` 事件格式
- Produces:
  - `temporal_window(session, hits: list[Evidence]) -> list[Evidence]`(空结果返回 `[]`)
  - 常量 `WINDOW_BUDGET = 24`、`WINDOW_STOP_LOCATIONS = 2`、`WINDOW_WALK_LIMIT = 60`
  - `Evidence.source: Literal["vector", "graph", "temporal_window"]`
  - 查询 A(计票,参数 `ids`):`MATCH (e:Event) WHERE e.id IN $ids OPTIONAL MATCH (e)-[:LOCATED_AT]->(l:Location) RETURN e.id AS id, l.id AS lid, l.name AS lname`
  - 查询 B(窗口,参数 `mid, scn, ord, lim`,scn/ord 为 int):
    `MATCH (e:Event {movie_id: $mid}) WITH e, split(e.id, '_') AS p WITH e, toInteger(p[2]) AS scn, toInteger(p[3]) AS ord WHERE scn > $scn OR (scn = $scn AND ord > $ord) OPTIONAL MATCH (e)-[:LOCATED_AT]->(l:Location) RETURN e.id AS id, e.topic AS topic, e.description AS description, e.event_type AS event_type, scn, ord, l.id AS locid, l.name AS loc ORDER BY scn, ord LIMIT $lim`

- [ ] **Step 1: 写失败测试**

```python
def _ev(nid, score=0.9, label="Event"):
    return Evidence(source="vector", node_id=nid, text=f"t:{nid}", score=score,
                    extra={"label": label})

VOTE = [  # 查询 A 的脚本化结果:4 票餐厅(非空)+ 2 票空名(不计)
    {"id": "evt_tt0105236_002_03", "lid": "loc_tt0105236_aaa", "lname": "餐厅"},
    {"id": "evt_tt0105236_002_05", "lid": "loc_tt0105236_aaa", "lname": "餐厅"},
    {"id": "evt_tt0105236_003_02", "lid": "loc_tt0105236_aaa", "lname": "餐厅"},
    {"id": "evt_tt0105236_003_07", "lid": "loc_tt0105236_aaa", "lname": "餐厅"},
    {"id": "evt_tt0105236_020_01", "lid": "loc_tt0105236_d41d8cd9", "lname": ""},
    {"id": "evt_tt0105236_021_01", "lid": None, "lname": None},
]

def _wrow(nid, scn, locid, loc, topic="t"):
    return {"id": nid, "topic": topic, "description": "d", "event_type": "action",
            "scn": scn, "ord": nid[-2:], "locid": locid, "loc": loc}

def test_temporal_window_vote_anchor_and_stop():
    hits = [_ev("evt_tt0105236_002_03", 0.9), _ev("evt_tt0105236_002_05", 0.8),
            _ev("evt_tt0105236_003_02", 0.7), _ev("evt_tt0105236_003_07", 0.6),
            _ev("evt_tt0105236_020_01", 0.5), _ev("evt_tt0105236_021_01", 0.4),
            _ev("char_tt0105236_x", 0.95, label="Character")]  # 非 Event 不计
    win = [  # 锚点=胜组最高分 002_03;残余 002_04/05;场景3 同地点不算新;
        # 004 新地点①收;005 新地点②停在其前(镜像真实 rd_t1:场景2-3 共享地点)
        _wrow("evt_tt0105236_002_04", 2, "loc_aaa", "餐厅"),
        _wrow("evt_tt0105236_002_05", 2, "loc_aaa", "餐厅"),
        _wrow("evt_tt0105236_003_01", 3, "loc_aaa", "餐厅"),
        _wrow("evt_tt0105236_003_02", 3, "loc_aaa", "餐厅"),
        _wrow("evt_tt0105236_004_01", 4, "loc_bbb", "仓库"),
        _wrow("evt_tt0105236_004_02", 4, "loc_bbb", "仓库"),
        _wrow("evt_tt0105236_005_01", 5, "loc_ccc", "闪回"),
        _wrow("evt_tt0105236_005_02", 5, "loc_ccc", "闪回"),
    ]
    session = MockSession(results=[VOTE, win])
    rows = temporal_window(session, hits)
    assert [r.node_id for r in rows] == [
        "evt_tt0105236_002_04", "evt_tt0105236_002_05",
        "evt_tt0105236_003_01", "evt_tt0105236_003_02",
        "evt_tt0105236_004_01", "evt_tt0105236_004_02"]
    assert all(r.source == "temporal_window" for r in rows)
    assert rows[0].text.startswith("【时间序 1·场景2】")
    assert rows[5].text.startswith("【时间序 6·场景4】")
    wq, wp = session.queries[1]          # 窗口查询参数:锚点位置 + 本片 + 上限
    assert wp == {"mid": "tt0105236", "scn": 2, "ord": 3, "lim": 60}

def test_temporal_window_budget_cap():
    # 锚点 002_01;残余 1(002_02)+ 后续同地点 29 行(永不触发停止)→
    # 后续按 WINDOW_BUDGET=24 截断 → 合计 25 行
    vote = [{"id": "evt_tt0105236_002_01", "lid": "loc_aaa", "lname": "餐厅"}]
    win = [_wrow("evt_tt0105236_002_02", 2, "loc_aaa", "餐厅")] + \
          [_wrow(f"evt_tt0105236_003_{i:02d}", 3, "loc_aaa", "餐厅")
           for i in range(1, 30)]
    session = MockSession(results=[vote, win])
    rows = temporal_window(session, [_ev("evt_tt0105236_002_01")])
    assert len(rows) == 25
    assert rows[24].node_id == "evt_tt0105236_003_24"

def test_temporal_window_all_empty_locations_falls_back_to_top_hit():
    vote = [{"id": "evt_tt0105236_002_03", "lid": "loc_d", "lname": ""},
            {"id": "evt_tt0105236_020_01", "lid": None, "lname": None}]
    win = [_wrow("evt_tt0105236_002_05", 2, "loc_x", "仓库")]
    session = MockSession(results=[vote, win])
    rows = temporal_window(session, [_ev("evt_tt0105236_002_03", 0.9),
                                     _ev("evt_tt0105236_020_01", 0.4)])
    assert [r.node_id for r in rows] == ["evt_tt0105236_002_05"]

def test_temporal_window_tie_breaks_by_top_scored_hit():
    # 平票 1:1 → 最高分命中(002_01,0.9)所在组胜,锚点即它
    vote = [{"id": "evt_tt0105236_002_01", "lid": "loc_a", "lname": "餐厅"},
            {"id": "evt_tt0105236_020_01", "lid": "loc_b", "lname": "仓库"}]
    win = [_wrow("evt_tt0105236_002_02", 2, "loc_a", "餐厅")]
    session = MockSession(results=[vote, win])
    temporal_window(session, [_ev("evt_tt0105236_002_01", 0.9),
                              _ev("evt_tt0105236_020_01", 0.4)])
    assert session.queries[1][1]["scn"] == 2     # 锚点在场景 2,非 20

def test_temporal_window_movie_from_anchor_id():
    # 全局模式锚点是别片事件 → 窗口按锚点本片开(Review Focus 2)
    vote = [{"id": "evt_tt0117500_008_04", "lid": "loc_r", "lname": "墓地"}]
    win = [_wrow("evt_tt0117500_009_01", 9, "loc_r2", "字幕")]
    session = MockSession(results=[vote, win])
    rows = temporal_window(session, [_ev("evt_tt0117500_008_04")])
    assert session.queries[1][1]["mid"] == "tt0117500"
    assert [r.node_id for r in rows] == ["evt_tt0117500_009_01"]

def test_temporal_window_empty_rows_returns_empty():
    vote = [{"id": "evt_tt0105236_002_03", "lid": "loc_a", "lname": "餐厅"}]
    session = MockSession(results=[vote, []])    # 锚点在全片末尾
    assert temporal_window(session, [_ev("evt_tt0105236_002_03")]) == []

def test_temporal_window_no_event_hits_returns_empty():
    session = MockSession(results=[])
    assert temporal_window(session, []) == []
    assert session.queries == []                  # 无 Event 命中不发任何查询
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_retriever.py -k temporal_window -v > /tmp/out.txt 2>&1; echo EXIT=$?; tail /tmp/out.txt`
Expected: FAIL(`ImportError: temporal_window` 或 `AttributeError`),EXIT=1(注意管道吞退出码,须重定向后 echo)

- [ ] **Step 3: 实现**

`Evidence.source` 扩为 `Literal["vector", "graph", "temporal_window"]`。`temporal_window` 主体:
1. 过滤 `hits` 中 `extra["label"] == "Event"` 的项;空则返回 `[]`
2. 查询 A → 计票:按 `lid` 分组,**`lname` 为空/None 的组不计票**;非空组取票数最多者,平票取「分数最高的命中」所在组;全部命中空名 → 锚点=分数最高的 Event 命中(其 lid/lname 记为锚点组)
3. 锚点事件 = **胜组内得分最高的命中**(spec v2.1:不用组内末事件——同一
   Location 多次到访时组末会冲到最晚到访;残余规则已保同场景向后全收);
   从其 ID 解析 `mid`(`re.match(r"evt_(tt\d+)_", ...)`,失配返回 `[]`)
4. 查询 B(`scn`/`ord` 为锚点位置 int)→ 行已按序:
   - **残余**:行中 `scn == 锚点scn` 的全部收
   - **后续**:`scn > 锚点scn` 的行按序收,维护 `new_locs`(非空名且 `locid != 锚点组lid` 的有序去重集合);行属于尚未入集的新地点时先入集,`len(new_locs) > WINDOW_STOP_LOCATIONS` 则**该行起全弃**;后续计数达 `WINDOW_BUDGET` 截断
5. 生成 Evidence:`text = f"【时间序 {n}·场景{scn}】" + _node_text形状体`(体=`f"[{event_type}] {topic}:{description}"`,与 `_node_text` 一致),`score=None`,`extra={"label": "Event"}`,n 从 1 递增

- [ ] **Step 4: 运行确认通过**

Run: 同 Step 2
Expected: 7 passed,EXIT=0

- [ ] **Step 5: 全套回归**

Run: `python -m pytest tests/test_retriever.py -v > /tmp/out.txt 2>&1; echo EXIT=$?; tail -3 /tmp/out.txt`
Expected: 既有测试全绿(本任务不动 `retrieve()`,无破坏面)

- [ ] **Step 6: Commit**

```bash
git add src/movie_kg/qa/retriever.py tests/test_retriever.py
git commit -m "feat: temporal_window 核心函数——多数票锚点+ID 序窗口+地点软停止"
```

### Task 3: retrieve() 集成

**Files:**
- Modify: `src/movie_kg/qa/retriever.py:106-160`(`retrieve()`)
- Test: `tests/test_retriever.py`

**Interfaces:**
- Consumes: Task 2 的 `temporal_window` 及常量
- Produces: `retrieve()` temporal 路径新行为;非 temporal 路径不变

- [ ] **Step 1: 写失败测试**

```python
def test_retrieve_temporal_plan_adds_window_and_supersedes():
    plan = RetrievalPlan(intent="temporal", rewritten_query="q",
                         target_entities=[], graph_queries=[])
    vec = [  # 事件索引一次返回 3 命中:2 票餐厅 + 1 票仓库(输票)
        {"node": {"id": "evt_tt0105236_003_02", "topic": "讨论", "description": "d",
                  "event_type": "dialogue"}, "score": 0.9},
        {"node": {"id": "evt_tt0105236_003_07", "topic": "告别", "description": "d",
                  "event_type": "dialogue"}, "score": 0.8},
        {"node": {"id": "evt_tt0105236_004_01", "topic": "对峙", "description": "d",
                  "event_type": "conflict"}, "score": 0.7},
    ]
    vote = [
        {"id": "evt_tt0105236_003_02", "lid": "loc_aaa", "lname": "餐厅"},
        {"id": "evt_tt0105236_003_07", "lid": "loc_aaa", "lname": "餐厅"},
        {"id": "evt_tt0105236_004_01", "lid": "loc_bbb", "lname": "仓库"},
    ]
    win = [  # 锚点=胜组最高分 003_02;残余 003_08/09;004_01 新地点①收
        # (004_01 同时是向量命中 → 覆盖语义);005 新地点②停
        _wrow("evt_tt0105236_003_08", 3, "loc_aaa", "餐厅"),
        _wrow("evt_tt0105236_003_09", 3, "loc_aaa", "餐厅"),
        _wrow("evt_tt0105236_004_01", 4, "loc_bbb", "仓库"),
        _wrow("evt_tt0105236_005_01", 5, "loc_ccc", "闪回"),
    ]
    session = MockSession(results=[vec, [], [], [], vote, win])
    evidence = retrieve(session, ScriptedLLM(), plan)
    assert any(q.startswith("MATCH (e:Event) WHERE e.id IN $ids")
               for q, _ in session.queries)
    marked = [e for e in evidence if e.node_id == "evt_tt0105236_004_01"]
    assert len(marked) == 1                     # 覆盖:只此一行
    assert marked[0].source == "temporal_window"
    assert marked[0].text.startswith("【时间序 3·场景4】")

def test_retrieve_non_temporal_never_issues_window_queries():
    plan = RetrievalPlan(intent="factual", rewritten_query="q",
                         target_entities=[], graph_queries=[])
    session = MockSession(results=[VEC_HITS, [], [], []])
    retrieve(session, ScriptedLLM(), plan)
    assert not any("LOCATED_AT" in q for q, _ in session.queries)
    # 逐字节钉:查询数与既有非 temporal 路径一致(4 召回,无绑定/模板/窗口)
    assert len(session.queries) == 4
```

(注:两测试各自脚本化完整查询队列——global 模式 4 次 queryNodes 召回 + temporal 时 2 次窗口查询,共 6;以 `session.queries` 顺序断言)

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_retriever.py -k "retrieve_temporal or retrieve_non_temporal_never" -v > /tmp/out.txt 2>&1; echo EXIT=$?; tail /tmp/out.txt`
Expected: `test_retrieve_temporal_plan_adds_window_and_supersedes` FAIL(无窗口查询);`test_retrieve_non_temporal_never...` 可能 PASS(钉既有行为)

- [ ] **Step 3: 实现**

`retrieve()` 内、图模板循环之后、去重之前:

```python
if plan.intent == "temporal":
    window_rows = temporal_window(session, hits)
    if window_rows:
        win_ids = {w.node_id for w in window_rows}
        evidence = [e for e in evidence if e.node_id not in win_ids] + window_rows
```

(窗口行覆盖同 node_id 旧行后追加尾部;`hits` 是召回原列表,与 `evidence` 头部同源,覆盖语义即 spec「时间序标记存活」)

- [ ] **Step 4: 运行确认通过 + 全文件回归**

Run: `python -m pytest tests/test_retriever.py -v > /tmp/out.txt 2>&1; echo EXIT=$?; tail -3 /tmp/out.txt`
Expected: 全绿(既有 `test_retrieve_*` 均为非 temporal plan,不受影响)

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/qa/retriever.py tests/test_retriever.py
git commit -m "feat: retrieve() temporal 集成——intent 硬门+窗口行覆盖追加"
```

### Task 4: 真库集成冒烟

**Files:**
- Create: `tests/test_temporal_window_integration.py`

**Interfaces:**
- Consumes: `retrieve()`、`RetrievalPlan`、Task 2/3 行为
- Produces: 无(验证性测试)

- [ ] **Step 1: 写测试(`pytest.mark.integration` + `skipif(not NEO4J_URI)`,LLM 嵌入 1024 维,参照 `tests/test_scoped_retrieval_integration.py` 的 session/LLM1024 模式)**

```python
def test_rd_t2_window_covers_gold(session):
    from movie_kg.qa.planner import RetrievalPlan
    from movie_kg.qa.retriever import retrieve
    plan = RetrievalPlan(intent="temporal", rewritten_query="仓库里 Mr. Blonde 到达后的对峙与冲突",
                         target_entities=[], graph_queries=[], movie_id="tt0105236")
    evidence = retrieve(session, LLM1024(), plan)
    win = [e for e in evidence if e.source == "temporal_window"]
    assert win, "temporal plan 必须产生窗口证据"
    assert all(e.node_id.startswith("evt_tt0105236_") for e in win)   # 不跨片
    scns = [int(e.node_id.split("_")[2]) for e in win]
    assert scns == sorted(scns)                                        # ID 序
    assert all("【时间序" in e.text for e in win)
    golds = {"evt_tt0105236_008_05", "evt_tt0105236_008_06",
             "evt_tt0105236_008_07", "evt_tt0105236_008_08"}
    assert golds <= {e.node_id for e in evidence}, "rd_t2 gold 应整体入证据(向量命中+窗口残余/后续)"

def test_rk_t1_subevent_anchor_still_opens_window(session):
    # 锚点可能落在链外子事件(008_02-05 PRECEDES 度 0)——ID 序窗口必须免疫
    plan = RetrievalPlan(intent="temporal", rewritten_query="Hummel 在妻子墓前的告别",
                         target_entities=[], graph_queries=[], movie_id="tt0117500")
    evidence = retrieve(session, LLM1024(), plan)
    win = [e for e in evidence if e.source == "temporal_window"]
    assert win and all(e.node_id.startswith("evt_tt0117500_") for e in win)
```

- [ ] **Step 2: 对真库运行**

Run: `NEO4J_URI`/`NEO4J_PASSWORD` 就绪(见记忆文件),`.venv/Scripts/python.exe -m pytest tests/test_temporal_window_integration.py -v > /tmp/out.txt 2>&1; echo EXIT=$?; tail /tmp/out.txt`
Expected: 2 passed。若 rd_t2 的 gold 断言因锚点误绑失败:**不得改断言迁就**——按 spec 多数票风险记录为 finding,交最终审查裁决

- [ ] **Step 3: Commit**

```bash
git add tests/test_temporal_window_integration.py
git commit -m "test: 真库冒烟——rd_t2 gold 整体入证据、子事件锚点免疫(ID 序)"
```

### Task 5: 全量评测与报告(实验任务)

**Files:**
- Create: `eval/TEMPORAL_WINDOW_REPORT.md`
- Modify(运行覆盖): `eval/qa_report_full_scoped.md`(基线锚定 617342f,可回溯)
- Modify(成功后): `docs/DESIGN.md` 附录版本行

**Interfaces:**
- Consumes: 全部前序任务;基线=617342f 的 `qa_report_full_scoped.md`(0.5833,逐题表在 git)

- [ ] **Step 1: 全量评测(后台,约 35-40 分钟)**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/kg.exe eval qa > /tmp/eval_tw.out 2>&1`(后台任务)
Expected: 完成行 `accuracy=… hit_rate=… -> eval/qa_report_full_scoped.md`

- [ ] **Step 2: 三道门判定(对照 617342f 基线逐题)**

1. temporal judged ≥6/12(B2);2. 触发面 5 题(rd_t1/rd_t2/hk_t1/hk_t4/rk_t1)hit_rate ≥0.5;3. 非 temporal 48 题净变动 ≤±3(对照基线逐题 judged)
逐题归因 tempora 失败:锚点绑对没有(计票查询可复现)/窗口长度(残余+预算覆盖 gold 与否)/作答吸收/判卷;另记录 rd_t4/hk_t3(非触发面)是否维持 True

- [ ] **Step 3: 条件校准(仅一轮,B2)**

若门 2 过、门 1 为 4-5:改 `WINDOW_BUDGET = 32` **或** `WINDOW_STOP_LOCATIONS = 3`(二选一,按逐题归因选:gold 在窗口外→预算;判卷要更多情节→停止信号),全量复跑一次,回到 Step 2
其余情形:不校准,如实报告

- [ ] **Step 4: 写报告**

`eval/TEMPORAL_WINDOW_REPORT.md`:设置(含 plan.intent 对齐测量结论)、总对照(0.5833→X)、temporal 逐题归因表、非 temporal 守门、结论(是否达标 0.667;未达则残因清单)。DESIGN 附录加版本行(成功时)

- [ ] **Step 5: Commit**

```bash
git add eval/TEMPORAL_WINDOW_REPORT.md eval/qa_report_full_scoped.md docs/DESIGN.md
git commit -m "eval: temporal 窗口全量对照——(按实绩填结论)"
```
