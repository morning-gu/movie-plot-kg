# 按片问答(movie-scoped QA)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 问答支持按电影 ID 隔离——`kg ask --movie` 与 `kg eval qa` 在指定片子图内做精确向量检索与实体绑定,全局模式逐字节不变,并产出 scoped vs global 污染对照实验。

**Architecture:** 检索层加 `movie_id` 可选参数:有值时向量召回不走全局 ANN 索引,改为每标签一次片内精确余弦扫描(`movie_id` B-tree 索引 + `vector.similarity.cosine`);三级实体绑定同样按片收窄。scope 由调用方注入 `RetrievalPlan.movie_id`(LLM 不参与),评测层从 gold 的 tt 前缀逐题推导。不动 schema、不重建、不重嵌、提示词一字不改。

**Tech Stack:** Python 3.11 / pydantic v2 / typer / Neo4j 5.26(单库,bolt)/ pytest。

**Spec:** `docs/superpowers/specs/2026-10-22-movie-scoped-qa-design.md`

## Global Constraints

- 全局模式(`movie_id=None`)行为**逐字节不变**:现有测试的断言不得修改以适配新行为
- scoped 空结果**宁空不污染**:绝不静默回落全局检索(设计反目标)
- 不改 schema、不重建图谱、不重新嵌入;`config/prompts/qa_planning.md`、`qa_answering.md` 一字不动
- scoped 分数是 **(1+cos)/2 重标度**,与 ANN 裸 cosine 不可比;无任何下游按分数分叉
- 报告文件名:`eval/qa_report_{mode}{_scoped}{_m{ids}}{_k{N}}.md`;**过滤运行绝不覆盖未过滤报告**(`qa_report_full.md` 是 0.5667 基线工件)
- 库层 `run_qa` 默认 `scope="global"`(现有裸调用保持原语义);CLI `--scope` 默认 `movie`(产品面默认)
- movie 推导:`re.search(r"tt\d+", gold_entity_ids[0])`;推导不出 → 该题回落全局 + logging 警告
- 测试命令一律带 `echo "EXIT=$?"` 收尾(本仓库两次踩坑:`| tail` 吞 pytest 退出码)

## Review Focus

1. **scoped 检索全部为空**(问题语义离本片远)→ 必须返回空证据,不回落全局 —— Task 3 `test_retrieve_threads_movie_id_from_plan` 断言 `evidence == []` 且无 `queryNodes` 查询
2. **某标签本片无嵌入节点**(VirtualTime 单节点边界)→ 该标签空结果不崩 —— Task 1 扫描测试队列含三个空标签结果
3. **gold 推导不出影片**(无 gold 或无 tt 前缀)→ 该题回落全局并计警告日志,不崩不静默 —— Task 4 `test_run_qa_scoped_falls_back_when_movie_underivable`
4. **`--movies` 无交集/格式非法** → 报错退出,不生成空报告、不静默全跑 —— Task 5 `test_cli_eval_qa_movies_empty_intersection_errors`
5. **过滤/消融运行覆盖基线工件** → `_m`/`_scoped` 后缀防覆盖 —— Task 5 `test_cli_eval_qa_movies_filter_suffix_and_no_clobber`
6. **`ask --movie` 格式合法但库中不存在** → 列出库内可用影片后报错退出 —— Task 5 `test_ask_movie_unknown_lists_available`

---

### Task 1: 片内精确向量召回

**Files:**
- Modify: `src/movie_kg/qa/retriever.py:26-37`(vector_recall)
- Test: `tests/test_retriever.py`

**Interfaces:**
- Consumes: 现有 `INDEXES`、`Evidence`、`_node_text`
- Produces: `vector_recall(session, llm, query: str, top_k: int = 10, movie_id: str | None = None) -> list[Evidence]`;scoped 路径每标签发一次扫描查询,记录键 `n`、`score`,参数 `mid`/`vec`/`k`

- [ ] **Step 1: Write the failing test**(加到 tests/test_retriever.py)

```python
def test_vector_recall_scoped_scans_movie_subgraph():
    # 片内精确扫描(设计:真·片内检索,否决超采过滤):每标签一次,
    # 带 $mid/$vec/$k,绝不走全局 ANN;某标签空结果不崩
    hit = {"n": {"id": "evt_tt0105236_001_01", "topic": "对峙",
                 "description": "d", "event_type": "conflict"}, "score": 0.9}
    session = MockSession(results=[[hit], [], [], []])   # Event 命中,其余标签空
    hits = vector_recall(session, ScriptedLLM(), "仓库对峙", movie_id="tt0105236")
    assert len(hits) == 1 and hits[0].node_id == "evt_tt0105236_001_01"
    assert hits[0].extra["label"] == "Event" and hits[0].score == 0.9
    assert not any("queryNodes" in q for q, _ in session.queries), \
        "scoped 不得走全局 ANN"
    scans = [(q, p) for q, p in session.queries if "cosine" in q]
    assert len(scans) == 4
    assert all(p == {"mid": "tt0105236", "k": 10, "vec": [0.0] * 8}
               for _, p in scans)
    labels = {l for q, _ in scans
              for l in ("Event", "Character", "VirtualTime", "Location")
              if f"(n:{l} " in q}
    assert labels == {"Event", "Character", "VirtualTime", "Location"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_retriever.py::test_vector_recall_scoped_scans_movie_subgraph -v; echo "EXIT=$?"`
Expected: FAIL — `vector_recall() got an unexpected keyword argument 'movie_id'`

- [ ] **Step 3: Implement scoped path in `vector_recall`**

`movie_id is None` 分支保持现有代码不动;否则对 `INDEXES` 每个标签发 spec 钉死的扫描查询(标签来自常量,安全内插;查询为设计原文):

```cypher
MATCH (n:{label} {movie_id: $mid})
WHERE n.embedding IS NOT NULL
WITH n, vector.similarity.cosine(n.embedding, $vec) AS score
ORDER BY score DESC LIMIT $k
```

记录转 Evidence 的方式与 ANN 路径一致(`dict(rec["n"])`、`rec.get("score")`、`extra={"label": label}`)。

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_retriever.py -v; echo "EXIT=$?"`
Expected: PASS(含既有 `test_vector_recall_queries_all_four_indexes`,证明全局路径未动)

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/qa/retriever.py tests/test_retriever.py
git commit -m "feat: 片内精确向量召回——movie_id 有值时每标签一次 vector.similarity.cosine 扫描(O(单片规模)),不走全局 ANN"
```

### Task 2: 三级绑定按片收窄

**Files:**
- Modify: `src/movie_kg/qa/retriever.py:40-73`(bind_entities)
- Test: `tests/test_retriever.py`

**Interfaces:**
- Consumes: Task 1 无依赖(本任务独立于 vector_recall 的改动)
- Produces: `bind_entities(session, targets: list[TargetEntity], vector_hits: list[Evidence], movie_id: str | None = None) -> dict[str, str]`

- [ ] **Step 1: Write the failing tests**

```python
def test_bind_entities_scoped_tiers_carry_movie_id():
    # 精确层与词级扫描层都带 movie_id 条件(修现存跨片同名错绑:
    # "未识别:群演"3 片全有、"Agent" 在 Hackers+Rock)
    session = MockSession(results=[[], []])   # 精确无 + 词级无 → 不绑
    bound = bind_entities(session,
                          [TargetEntity(type="Character", name_hint="张三")],
                          vector_hits=[], movie_id="tt0105236")
    assert bound == {}
    t1, t2 = session.queries[0], session.queries[1]
    assert "n.movie_id = $mid" in t1[0] and t1[1]["mid"] == "tt0105236"
    assert "n.movie_id = $mid" in t2[0] and t2[1]["mid"] == "tt0105236"


def test_bind_entities_global_queries_unchanged():
    # 全局模式逐字节不变:查询串与参数都不得出现 movie_id/mid
    session = MockSession(results=[[], []])
    bind_entities(session,
                  [TargetEntity(type="Character", name_hint="张三")],
                  vector_hits=[])
    for q, p in session.queries:
        assert "movie_id" not in q and "mid" not in p
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_retriever.py::test_bind_entities_scoped_tiers_carry_movie_id tests/test_retriever.py::test_bind_entities_global_queries_unchanged -v; echo "EXIT=$?"`
Expected: FAIL — unexpected keyword argument `movie_id`

- [ ] **Step 3: Implement**

tier1 与 tier2 的 Cypher 按 `movie_id` 是否为 None 分别构造字符串(None 时与现状逐字节一致);tier3(向量回落)不改——scoped 模式下 `vector_hits` 已是片内结果。绑定成功/失败逻辑全部不动。

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_retriever.py -v; echo "EXIT=$?"`
Expected: PASS(含既有 `test_bind_entities_exact_then_alias_then_fallback`)

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/qa/retriever.py tests/test_retriever.py
git commit -m "feat: bind_entities 三级绑定按片收窄——精确层/词级层加 movie_id 条件(修跨片同名错绑),向量回落层不改"
```

### Task 3: 计划层透传(宁空不污染)

**Files:**
- Modify: `src/movie_kg/qa/planner.py:33-54`(RetrievalPlan + plan_retrieval)
- Modify: `src/movie_kg/qa/retriever.py:76-79`(retrieve 头部)
- Test: `tests/test_planner.py`、`tests/test_retriever.py`

**Interfaces:**
- Consumes: Task 1 `vector_recall(..., movie_id=...)`、Task 2 `bind_entities(..., movie_id=...)`
- Produces: `RetrievalPlan` 新字段 `movie_id: str | None = None`;`plan_retrieval(llm, question: str, movie_id: str | None = None) -> RetrievalPlan`(原样透传,LLM 不参与);`retrieve` 从 `plan.movie_id` 读 scope 下传两层(签名不变)

- [ ] **Step 1: Write the failing tests**

tests/test_planner.py(沿用该文件 PLAN 替身):

```python
def test_plan_retrieval_passes_movie_id():
    plan = plan_retrieval(ScriptedLLM(PLAN), "q", movie_id="tt0105236")
    assert plan.movie_id == "tt0105236"
    assert plan_retrieval(ScriptedLLM(PLAN), "q").movie_id is None
```

tests/test_retriever.py:

```python
def test_retrieve_threads_movie_id_from_plan():
    # plan.movie_id 穿到召回与绑定;scoped 全空时保持空(宁空不污染),
    # 绝不回落全局 ANN(设计反目标)
    plan = RetrievalPlan(intent="factual", rewritten_query="q",
                         target_entities=[TargetEntity(
                             type="Character", name_hint="张三")],
                         graph_queries=[], movie_id="tt0105236")
    session = MockSession(results=[[], [], [], [], []])   # 4 扫描 + 绑定全空
    evidence = retrieve(session, ScriptedLLM(), plan)
    assert evidence == []
    assert not any("queryNodes" in q for q, _ in session.queries)
    assert all(p.get("mid") == "tt0105236" for _, p in session.queries)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_planner.py::test_plan_retrieval_passes_movie_id tests/test_retriever.py::test_retrieve_threads_movie_id_from_plan -v; echo "EXIT=$?"`
Expected: FAIL — `RetrievalPlan` 无 `movie_id` 字段(pydantic 额外字段报错)

- [ ] **Step 3: Implement**

`RetrievalPlan` 加字段;`plan_retrieval` 加参数并在构造时透传(提示词不碰);`retrieve` 里 `vector_recall(..., movie_id=plan.movie_id)`、`bind_entities(..., movie_id=plan.movie_id)`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_planner.py tests/test_retriever.py -v; echo "EXIT=$?"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/qa/planner.py src/movie_kg/qa/retriever.py tests/test_planner.py tests/test_retriever.py
git commit -m "feat: RetrievalPlan.movie_id 由调用方注入并穿到召回/绑定——scope 不经 LLM,提示词不动"
```

### Task 4: 评测层 scope(逐题推导 + 按片汇总)

**Files:**
- Modify: `src/movie_kg/eval/qa_eval.py:14-35`(QAItem 不动;QAResult 加字段)、`43-62`(run_qa)、`83-101`(summarize)、`104-122`(write_qa_report)
- Test: `tests/test_qa_eval.py`

**Interfaces:**
- Consumes: Task 3 `plan_retrieval(llm, question, movie_id=...)`
- Produces: `movie_of_item(item: QAItem) -> str | None`(模块级函数,Task 5 CLI 复用);`run_qa(session, llm, items, mode, top_k=10, scope="global") -> list[QAResult]`;`QAResult.movie_id: str | None = None`;`summarize` 返回值新增 `per_movie`(`{movie_id: {n, accuracy, mean_hit_rate}}`);`write_qa_report` 在 `per_movie` 非空时渲染"## 按片"表(列:影片/n/accuracy/mean_hit_rate)

- [ ] **Step 1: Write the failing tests**(加到 tests/test_qa_eval.py;文件顶部导入补 `import logging`,并从 `movie_kg.eval.qa_eval` 增导入 `QAItem, movie_of_item`)

```python
def _make_scoped_set(tmp=None):
    # gold 带 tt 前缀:movie_of_item 可推导出影片
    import tempfile
    from pathlib import Path
    d = Path(tmp) if tmp else Path(tempfile.mkdtemp())
    p = d / "qa.yaml"
    p.write_text("""items:
  - id: q001
    question: Mr. White 为什么坚持送医院?
    gold_answer: 因为同伴重伤。
    gold_entity_ids: [evt_tt0105236_008_02]
    intent: causal
""", encoding="utf-8")
    return p


def test_movie_of_item_derives_tt():
    item = load_qa_set(_make_scoped_set())[0]
    assert movie_of_item(item) == "tt0105236"
    assert movie_of_item(QAItem(id="x", question="q", gold_answer="a",
                                gold_entity_ids=[], intent="factual")) is None


def test_run_qa_default_scope_is_global():
    # 库层默认 global(builder 审查裁决):裸调用直接覆盖全局路径,
    # 而非借"推导不出回落"的意外路径间接覆盖
    items = load_qa_set(_make_set())
    session = MockSession(results=[[], [], [], []])
    run_qa(session, ScriptedLLM(PLAN, ANSWER), items, mode="full")
    assert any("queryNodes" in q for q, _ in session.queries)
    assert all("cosine" not in q for q, _ in session.queries)


def test_run_qa_scoped_derives_movie_per_item():
    items = load_qa_set(_make_scoped_set())
    session = MockSession(results=[[], [], [], []])
    results = run_qa(session, ScriptedLLM(PLAN, ANSWER), items,
                     mode="full", scope="movie")
    assert results[0].movie_id == "tt0105236"
    assert not any("queryNodes" in q for q, _ in session.queries)
    assert all(p.get("mid") == "tt0105236"
               for q, p in session.queries if "cosine" in q)


def test_run_qa_scoped_falls_back_when_movie_underivable(caplog):
    # gold 无 tt 前缀/为空:该题回落全局并计警告(防御路径,当前 60 题全有 gold)
    items = load_qa_set(_make_set())          # gold = evt_m1_001_01
    session = MockSession(results=[[], [], [], []])
    with caplog.at_level(logging.WARNING):
        results = run_qa(session, ScriptedLLM(PLAN, ANSWER), items,
                         mode="full", scope="movie")
    assert results[0].movie_id is None
    assert any("queryNodes" in q for q, _ in session.queries)
    assert caplog.text.strip()                # 必须留痕,不静默


def test_run_qa_rejects_bad_scope():
    import pytest
    with pytest.raises(ValueError, match="scope"):
        run_qa(MockSession(), ScriptedLLM(), load_qa_set(_make_set()),
               mode="full", scope="mov")


def test_summarize_and_report_per_movie(tmp_path):
    items = load_qa_set(_make_scoped_set())
    session = MockSession(results=[[], [], [], []])
    results = run_qa(session, ScriptedLLM(PLAN, ANSWER), items,
                     mode="full", scope="movie")
    judged = judge_answers(ScriptedLLM(JUDGE_OK), items, results)
    s = summarize(judged)
    assert s["per_movie"]["tt0105236"]["n"] == 1
    assert s["per_movie"]["tt0105236"]["accuracy"] == 1.0
    out = tmp_path / "r.md"
    write_qa_report(out, judged, s)
    text = out.read_text(encoding="utf-8")
    assert "按片" in text and "tt0105236" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_qa_eval.py -v -k "movie_of or scope or per_movie"; echo "EXIT=$?"`
Expected: FAIL — `movie_of_item` 导入错误

- [ ] **Step 3: Implement**

`movie_of_item`:gold 非空时 `re.search(r"tt\d+", item.gold_entity_ids[0])`,命中返回 `.group()`,否则 None。`run_qa`:scope 校验(movie/global,镜像 mode 的 ValueError);scope=movie 时逐题 `movie_of_item`,None 则 `logging.getLogger(__name__).warning(...)` 该题全局;`plan_retrieval(llm, item.question, movie_id=...)`;`QAResult(movie_id=...)`。`summarize`:按 `movie_id` 非空分组计 n/accuracy(判定非 None 的),mean_hit_rate 取该片 hit_rate 非 None 的均值。`write_qa_report`:`summary.get("per_movie")` 非空时渲染"## 按片"表。

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_qa_eval.py -v; echo "EXIT=$?"`
Expected: PASS(既有测试含 CLI 旗标测试全部保持绿)

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/eval/qa_eval.py tests/test_qa_eval.py
git commit -m "feat: 评测层按片隔离——run_qa 逐题从 gold 推导 movie(scope 默认 global)、按片汇总表、无 gold 回落全局并计日志"
```

### Task 5: CLI(ask --movie / eval qa --scope --movies)

**Files:**
- Modify: `src/movie_kg/cli.py:93-108`(ask)、`164-190`(eval_qa)
- Test: `tests/test_cli_ask.py`、`tests/test_qa_eval.py`

**Interfaces:**
- Consumes: Task 3 `plan_retrieval(movie_id=...)`、Task 4 `movie_of_item` / `run_qa(scope=...)`
- Produces: `ask --movie <tt号>`(可选旗标);`eval qa --scope {movie,global}`(默认 movie)与 `--movies tt…,tt…`;报告名后缀拼接顺序 `{mode}{_scoped}{_m{ids}}{_k{N}}`

- [ ] **Step 1: Write the failing tests**

tests/test_cli_ask.py(沿用该文件的 runner/REPO_CONFIG/PLAN/ANSWER 模式):

```python
def test_ask_movie_scoped_happy_path(tmp_path, monkeypatch):
    # --movie:先校验影片存在,随后片内扫描,全程无 queryNodes
    shutil.copytree(REPO_CONFIG, tmp_path / "config")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw-test")
    monkeypatch.chdir(tmp_path)
    session = MockSession(results=[[{"title": "Reservoir Dogs"}],
                                   [], [], [], [], []])  # 影片行 + 4 扫描 + 绑定
    llm = ScriptedLLM(PLAN, ANSWER)
    monkeypatch.setattr(cli_mod, "_make_llm", lambda cfg: llm)
    monkeypatch.setattr(cli_mod, "_make_driver", lambda cfg: MockDriver(session))
    result = runner.invoke(
        main, ["ask", "Mr. White 为什么坚持送医院?", "--movie", "tt0105236"])
    assert result.exit_code == 0, result.output
    assert "MATCH (m:Movie" in session.queries[0][0]
    assert session.queries[0][1]["mid"] == "tt0105236"
    assert not any("queryNodes" in q for q, _ in session.queries[1:])


def test_ask_movie_rejects_bad_format(tmp_path, monkeypatch):
    shutil.copytree(REPO_CONFIG, tmp_path / "config")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw-test")
    monkeypatch.chdir(tmp_path)
    session = MockSession(results=[])
    monkeypatch.setattr(cli_mod, "_make_llm", lambda cfg: ScriptedLLM())
    monkeypatch.setattr(cli_mod, "_make_driver", lambda cfg: MockDriver(session))
    result = runner.invoke(main, ["ask", "q", "--movie", "abc"])
    assert result.exit_code != 0
    assert session.queries == []          # 格式非法不触库


def test_ask_movie_unknown_lists_available(tmp_path, monkeypatch):
    shutil.copytree(REPO_CONFIG, tmp_path / "config")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw-test")
    monkeypatch.chdir(tmp_path)
    session = MockSession(results=[
        [],                                                   # 影片不存在
        [{"id": "tt0105236", "title": "Reservoir Dogs"}]])    # 可用列表
    monkeypatch.setattr(cli_mod, "_make_llm", lambda cfg: ScriptedLLM())
    monkeypatch.setattr(cli_mod, "_make_driver", lambda cfg: MockDriver(session))
    result = runner.invoke(main, ["ask", "q", "--movie", "tt9999999"])
    assert result.exit_code != 0
    assert "tt0105236" in result.output     # 列出库内可用影片
```

tests/test_qa_eval.py(沿用 `test_cli_eval_qa_top_k_flag` 的搭建模式,共用 `PLAN/ANSWER/JUDGE_OK`):

```python
def test_cli_eval_qa_default_scope_movie(tmp_path, monkeypatch):
    # CLI 默认 scope=movie(产品面默认):片内扫描 + _scoped 报告名
    ...同 top_k 旗标测试的搭建,qa-set 用 _make_scoped_set(tmp_path)...
    result = runner.invoke(main, ["eval", "qa", "--qa-set", str(set_path)])
    assert result.exit_code == 0, result.output
    assert not any("queryNodes" in q for q, _ in session.queries)
    assert (tmp_path / "eval" / "qa_report_full_scoped.md").exists()


def test_cli_eval_qa_scope_global_keeps_legacy_name(tmp_path, monkeypatch):
    ...同上搭建,invoke [..., "--scope", "global"]...
    assert any("queryNodes" in q for q, _ in session.queries)
    assert (tmp_path / "eval" / "qa_report_full.md").exists()


def test_cli_eval_qa_movies_filter_suffix_and_no_clobber(tmp_path, monkeypatch):
    # 过滤运行绝不覆盖未过滤报告:文件名追加 _m{ids}(builder 审查裁决)
    ...同上搭建,invoke [..., "--movies", "tt0105236"]...
    assert (tmp_path / "eval" / "qa_report_full_scoped_mtt0105236.md").exists()
    assert not (tmp_path / "eval" / "qa_report_full_scoped.md").exists()


def test_cli_eval_qa_movies_empty_intersection_errors(tmp_path, monkeypatch):
    ...同上搭建,invoke [..., "--movies", "tt9999999"]...
    assert result.exit_code != 0
    assert "无匹配" in result.output
```

(搭建重复四份属于测试局部样板,允许;不抽取公共 helper 以免动既有测试。)

**既有测试的一处意图保持性修改**(plan 自查发现,必须做):`test_cli_eval_qa_top_k_flag` 的 invoke 参数加 `"--scope", "global"`,断言全部不动。原因:CLI 默认 scope 已按批准设计改为 movie,该测试的本意是钉住**全局路径**的 top_k 穿线 + 旧文件名 `qa_report_full_k30.md`;不加此参数,它会退化为测 scoped 回落路径且文件名断言破裂。这不是"改断言迁就新行为"——断言一字未动,加的是保持被测路径所需的显式参数。

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_ask.py tests/test_qa_eval.py -v -k "movie or scope or movies"; echo "EXIT=$?"`
Expected: FAIL — `ask` 不认识 `--movie`(typer 报 no such option)

- [ ] **Step 3: Implement**

`ask`:加可选 `--movie`;有值时先 `re.fullmatch(r"tt\d+", movie)` 校验格式(不过则 echo 错误 + `typer.Exit(1)`,不建 driver);再查 `MATCH (m:Movie {id: $mid}) RETURN m.title AS title`,空则查 `MATCH (m:Movie) RETURN m.id AS id, m.title AS title ORDER BY m.id` 列出可用影片并 `Exit(1)`;通过后 `plan_retrieval(llm, question, movie_id=movie)`。

`eval_qa`:加 `--scope`(默认 "movie")与 `--movies`(逗号分隔);`--movies` 逐项 `re.fullmatch(r"tt\d+", ...)` 校验,再按 `movie_of_item(i) in wanted` 过滤 items,空交集 echo "无匹配题目" + `Exit(1)`;`run_qa(..., scope=scope)`;报告后缀按 Global Constraints 顺序拼接:`_scoped`(scope=movie 时)+ `_m{"-".join(wanted)}`(有过滤时)+ `_k{top_k}`(≠10 时)。

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_ask.py tests/test_qa_eval.py -v; echo "EXIT=$?"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/cli.py tests/test_cli_ask.py tests/test_qa_eval.py
git commit -m "feat: CLI——ask --movie(格式/存在性校验,未知列出可用影片)+ eval qa --scope/--movies(默认 movie;过滤运行 _m 后缀防覆盖基线)"
```

### Task 6: 真库冒烟(integration)

**Files:**
- Create: `tests/test_scoped_retrieval_integration.py`
- Modify: 无生产代码

**Interfaces:**
- Consumes: `retrieve`、`bind_entities`、`RetrievalPlan`、`TargetEntity`(Tasks 1-3 成品)
- Produces: 无(只读验证;**不得写/删真库数据**——区别于 test_writer_integration.py 的 DETACH DELETE 模式)

- [ ] **Step 1: Write the test**

```python
import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("NEO4J_URI"),
                       reason="需要 NEO4J_URI / NEO4J_PASSWORD"),
]


class LLM1024:
    """embed 须与真库节点嵌入同维(1024);ScriptedLLM 默认 8 维会炸 cosine。"""

    def embed(self, texts):
        return [[0.1] * 1024 for _ in texts]


@pytest.fixture
def session():
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ.get("NEO4J_USER", "neo4j"),
              os.environ.get("NEO4J_PASSWORD", "")))
    s = driver.session()
    yield s
    s.close()
    driver.close()


def test_scoped_retrieve_only_returns_target_movie(session):
    # 宁空不污染的正面钉子:证据 node_id 全部属于目标片,零跨片混入
    from movie_kg.qa.planner import RetrievalPlan, TargetEntity
    from movie_kg.qa.retriever import retrieve
    plan = RetrievalPlan(
        intent="causal", rewritten_query="仓库里 Mr. White 的对峙与冲突",
        target_entities=[], graph_queries=[], movie_id="tt0105236")
    evidence = retrieve(session, LLM1024(), plan)
    assert evidence, "RD 185 事件/21 角色,片内扫描必有结果"
    prefixes = ("evt_tt0105236_", "char_tt0105236_",
                "loc_tt0105236_", "vt_tt0105236_")
    assert all(e.node_id.startswith(prefixes) for e in evidence)
    assert not any(("tt0113243" in e.node_id) or ("tt0117500" in e.node_id)
                   for e in evidence)


def test_scoped_bind_hits_target_movie_character(session):
    from movie_kg.qa.planner import TargetEntity
    from movie_kg.qa.retriever import bind_entities
    bound = bind_entities(
        session, [TargetEntity(type="Character", name_hint="Mr. White")],
        vector_hits=[], movie_id="tt0105236")
    assert bound.get("Mr. White", "").startswith("char_tt0105236_")
```

- [ ] **Step 2: Run test to verify it fails or skips**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scoped_retrieval_integration.py -v; echo "EXIT=$?"`
Expected: SKIP(NEO4J_URI 未设)——EXIT=0

- [ ] **Step 3: Run against the real graph(Neo4j 在跑)**

Run: `NEO4J_URI=bolt://localhost:7687 NEO4J_PASSWORD=moviekg-dev .venv/Scripts/python.exe -m pytest tests/test_scoped_retrieval_integration.py -v; echo "EXIT=$?"`
Expected: 2 passed(真库 3 片俱在;RD 数据含 Mr. White 角色)

- [ ] **Step 4: Full suite stays green**

Run: `.venv/Scripts/python.exe -m pytest; echo "EXIT=$?"`
Expected: 全绿(新测试在无 NEO4J_URI 环境下 SKIP,不拖累默认套件)

- [ ] **Step 5: Commit**

```bash
git add tests/test_scoped_retrieval_integration.py
git commit -m "test: 真库冒烟——scoped 检索证据全属目标片、scoped 绑定命中本片角色(只读,不碰数据)"
```

### Task 7: 对照实验 + 报告 + DESIGN 变更记录

**Files:**
- Create: `eval/SCOPED_QA_REPORT.md`
- Modify: `eval/qa_report_full.md`(复跑覆盖,取新数为权威)、`eval/qa_report_full_scoped.md`(新)、`docs/DESIGN.md`(附录 C 加行)
- Test: 无(运行实验与文档;结构性检查在 Expected 里)

**Interfaces:**
- Consumes: Task 5 CLI 成品;真库(Neo4j 运行中)+ SiliconFlow 余额
- Produces: 污染影响量化结论 + 新官方基线(scoped 语义)

- [ ] **Step 1: 固定基线工件**

Run: `git add eval/build_report.md eval/qa_report_full.md eval/relation_samples.csv && git commit -m "chore: 实验前提交工作区既有 eval 报告改动(设计要求:固定基线,避免歧义)"`
Expected: 提交成功(这三处是会话前遗留的未提交改动)

- [ ] **Step 2: 复跑 global 腿(权威数)**

Run: `.venv/Scripts/kg.exe eval qa --scope global; echo "EXIT=$?"`
Expected: accuracy ≈ 0.55-0.60(历史 0.5667,judge 有方差)、hit_rate ≈ 0.61;报告写 `eval/qa_report_full.md`

- [ ] **Step 3: 跑 scoped 腿**

Run: `.venv/Scripts/kg.exe eval qa; echo "EXIT=$?"`
Expected: 报告写 `eval/qa_report_full_scoped.md`,含"按片"表(3 片 n=20 左右各一档);**scoped ≥ global 为预期**,若反而下降须逐题对比两份报告的逐题表归因,再定稿结论(那是重要发现,不是失败)

- [ ] **Step 4: 写对照报告**

手写 `eval/SCOPED_QA_REPORT.md`(沿 RETRIEVAL_EXPANSION_REPORT.md 惯例):总 accuracy/hit_rate 对照表、按片分解表、失败题翻转清单(两份报告逐题表 diff)、结论(跨片污染量化 + scoped 新基线定值)。

- [ ] **Step 5: DESIGN.md 附录 C 加行 + 提交**

附录 C 修订记录加一行 `v0.4.2 | 2026-10-22 | 按片问答:检索/绑定按 movie_id 收窄(片内精确余弦扫描替代全局 ANN,场景化双模式);eval 逐题隔离与按片汇总;对照实验量化跨片污染` ,并更新文末文档版本脚注。

```bash
git add eval/SCOPED_QA_REPORT.md eval/qa_report_full.md eval/qa_report_full_scoped.md docs/DESIGN.md
git commit -m "docs: scoped vs global 对照实验——量化跨片污染影响,确立 scoped 新基线;DESIGN v0.4.2"
```

---

## Self-Review 记录

1. **Spec 覆盖**:检索层(T1)/绑定层(T2)/计划层(T3)/评测层(T4)/CLI(T5)对应方案五节;实验与验收 1→T1-T5 单测、2→T6、3→T7;DESIGN 变更记录→T7。无缺口。
2. **Step 扫描**:实现步均只给签名+一句路径,Cypher 仅在 spec 钉死处出现(T1);测试步给全码。
3. **类型一致性**:`movie_id: str | None` 贯穿;`movie_of_item(item) -> str | None` 在 T4 产出、T5 消费;`run_qa(scope="global")` 默认值与 Global Constraints 一致。
4. **Review Focus**:6 条全部钉到具体测试(见各条标注)。
5. **比例**:spec 165 行,plan 以测试码为主,实现码占比低,合格。
6. **既有测试交互**(自查补查全仓):唯一受 CLI 默认值变化影响的是 `test_cli_eval_qa_top_k_flag`(test_qa_eval.py:62-89,全仓仅此一处调 eval qa CLI);处理见 Task 5 Step 1 的意图保持性修改。其余裸 `run_qa` 调用走库层默认 global,不受影响。
