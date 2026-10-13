# movie-plot-kg

基于 Qwen 系列大模型从电影数据构建剧情事件知识图谱,提供图增强检索问答与双端评估。
设计文档: docs/DESIGN.md(v0.2.0)。

## 环境准备

1. Python 3.11+;安装: `python -m venv .venv && source .venv/Scripts/activate && pip install -e ".[dev]"`
2. 启动 Neo4j(≥5.13,含向量索引):
   `docker run -d --name moviekg-neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_PASSWORD=moviekg-dev neo4j:5`
3. 复制 `.env.example` 为 `.env`,填 `LLM_API_KEY`(SiliconFlow 等 OpenAI 兼容端点)
4. 按需调整 `config/models.yaml` 中的模型型号

## 数据准备

```
data/movies/<movie_id>/
  movie.json                     # {"movie_id": "m1", "title": "..."}
  video/scene_001.mp4 ...        # 按场景切割
  subtitles/scene_001.srt ...    # 每场景台词
  scenes/scene_001.json ...      # 场景元数据(字段见 docs/DESIGN.md 3.3)
```

## 使用

```bash
kg build --movie m1 --scenes 1-5   # 先试跑 5 场景校准成本
kg build --movie m1                # 全量构建(断点续跑)
kg ask "导致张三和李四决裂的事件链是什么?"
kg eval build --movie m1           # 构建端评估 -> eval/build_report.md
kg eval qa --qa-set eval/qa_set.yaml --mode full          # 问答端评估
kg eval qa --qa-set eval/qa_set.yaml --mode vector-only   # 消融: 纯向量
```

## 测试

```bash
python -m pytest                       # 单元测试(离线)
NEO4J_URI=bolt://localhost:7687 NEO4J_PASSWORD=moviekg-dev \
  python -m pytest -m integration      # 集成测试(需真实 Neo4j)
```
