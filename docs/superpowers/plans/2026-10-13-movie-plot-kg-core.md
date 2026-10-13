# Movie Plot KG v0.2.0 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 docs/DESIGN.md v0.2.0 实现单部电影剧情知识图谱的构建、问答与评估核心闭环(CLI)。

**Architecture:** 三阶段构建管线(场景级抽取 A1/A2 + JSONL checkpoint → 全局 pass B1-B3 → 幂等图写入与向量化);Neo4j 5.x 单库同时承担图存储与向量检索;问答固定 2 次 LLM 调用(检索规划 + 综合回答),图查询走 7 个参数化模板,LLM 全程不生成 Cypher。

**Tech Stack:** Python 3.11+,pydantic v2,openai SDK(SiliconFlow 兼容端点:Qwen3-VL / Qwen3 / Qwen3-Embedding),neo4j Python driver,typer,PyAV + Pillow,pytest。

**Spec:** docs/DESIGN.md(v0.2.0,commit 5c14686)

## Global Constraints

以下约束适用于所有任务,值从 spec 抄录:

- Python `>=3.11`;依赖下限:`neo4j>=5.14.0`、`openai>=1.30.0`、`pydantic>=2.7.0`、`typer>=0.12.0`、`pillow>=10.0.0`、`av>=12.0.0`、`pyyaml>=6.0`、`python-dotenv>=1.0.0`、dev: `pytest>=8.0`
- **禁止引入**:LangChain、Celery、redis、FastAPI、uvicorn、qdrant-client、openai-whisper
- 所有 LLM 调用经 `config/models.yaml` 配置的 OpenAI 兼容端点;API key 从环境变量 `LLM_API_KEY` 读取(.env)
- MLLM 调用规格:每场景帧采样 ≤16、`temperature=0.1`、`max_tokens=4096`、JSON 校验失败重试 3 次
- Neo4j `>=5.13`(原生向量索引);向量维度 1024,必须与 `models.yaml` 的 `embedding_dims` 一致
- 实体 ID 规则见 spec 附录 A;所有实体带 `movie_id` 属性并连 `BELONGS_TO` 边
- LLM 不生成 Cypher;模板参数仅接受实体 ID、枚举值、深度整数(1-5)
- 每个问题恰好 2 次 LLM 调用(规划 + 回答)
- 数据为中文电影;源码 src 布局,包名 `movie_kg`,CLI 入口 `kg`
- 单元测试不访问网络与数据库;需要真实 Neo4j 的测试标记 `@pytest.mark.integration`,无 `NEO4J_URI` 环境变量时跳过
- 所有命令在仓库根目录、激活 `.venv` 后运行(Git Bash);测试命令统一 `python -m pytest`

## Review Focus

spec 隐含但易被任务测试遗漏的 5 类输入/失效模式(每条已 pin 到所属任务的测试):

1. **LLM 输出非合法 JSON / 不合 schema** → 重试 3 次后抛 `LLMError`,场景标记 failed、构建继续不阻塞。测试:Task 4(重试与报错)、Task 8(failed 场景不阻塞)。
2. **角色名变体**("老张"/"张叁"/名单外名称)→ 对齐到名单规范名;对不上的保留 `未识别:` 前缀并告警,不静默丢弃。测试:Task 5(align_name 四种情形)。
3. **模板参数注入**(`Zhang'} RETURN n //`、`depth="3 OR 1=1"`、depth=6)→ `TemplateParamError` 拒绝,不触达 Cypher。测试:Task 12。
4. **构建中断后重跑** → checkpoint 跳过已完成场景;`--from-scratch` 忽略全部。测试:Task 6(roundtrip)、Task 8(跳过与重跑)。
5. **角色关系变更**(`is_new=false`)→ 旧 RELATED_TO 边 `valid=false` + `end_event_id`,新边创建;查询只见 valid 边。测试:Task 10(writer 变更逻辑)、Task 12(模板 valid 过滤)。

---

### Task 1: 项目脚手架与配置加载

**Files:**
- Create: `pyproject.toml`、`.env.example`、`config/models.yaml`、`config/neo4j.yaml`、`src/movie_kg/__init__.py`、`src/movie_kg/config.py`、`tests/__init__.py`(空)、`tests/conftest.py`、`tests/test_config.py`

**Interfaces:**
- Produces: `ModelsConfig`(字段 `base_url: str, api_key: str, vision_model: str, text_model: str, embedding_model: str, embedding_dims: int`)、`Neo4jConfig`(`uri: str, user: str, password: str, database: str`)、`load_models_config(path: str | Path) -> ModelsConfig`、`load_neo4j_config(path: str | Path) -> Neo4jConfig`。后续所有任务经这两个工厂取配置。

- [ ] **Step 1: 写 pyproject 与配置文件**

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "movie-plot-kg"
version = "0.2.0"
requires-python = ">=3.11"
dependencies = [
    "neo4j>=5.14.0",
    "openai>=1.30.0",
    "pydantic>=2.7.0",
    "typer>=0.12.0",
    "pillow>=10.0.0",
    "av>=12.0.0",
    "pyyaml>=6.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
kg = "movie_kg.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
markers = ["integration: 需要真实 Neo4j(设置 NEO4J_URI)"]
testpaths = ["tests"]
```

`config/models.yaml`(型号按 SiliconFlow 实际可用项调整,代码只读配置):

```yaml
base_url: https://api.siliconflow.cn/v1
api_key_env: LLM_API_KEY
vision_model: Qwen/Qwen3-VL-32B-Instruct
text_model: Qwen/Qwen3-32B
embedding_model: Qwen/Qwen3-Embedding-0.6B
embedding_dims: 1024
```

`config/neo4j.yaml`:

```yaml
uri: bolt://localhost:7687
user: neo4j
password_env: NEO4J_PASSWORD
database: neo4j
```

`.env.example`:

```text
LLM_API_KEY=sk-your-key
NEO4J_PASSWORD=moviekg-dev
```

`tests/conftest.py`:

```python
import pytest
import sys
sys.path.insert(0, "tests")


@pytest.fixture
def repo_config_paths(tmp_path, monkeypatch):
    """复制仓库 config/*.yaml 到临时目录并注入测试环境变量。"""
    import shutil
    from pathlib import Path
    shutil.copy("config/models.yaml", tmp_path / "models.yaml")
    shutil.copy("config/neo4j.yaml", tmp_path / "neo4j.yaml")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw-test")
    return tmp_path / "models.yaml", tmp_path / "neo4j.yaml"
```

- [ ] **Step 2: 写失败测试**

`tests/test_config.py`:

```python
from movie_kg.config import load_models_config, load_neo4j_config


def test_load_models_config(repo_config_paths):
    models_path, _ = repo_config_paths
    cfg = load_models_config(models_path)
    assert cfg.base_url.startswith("https://")
    assert cfg.api_key == "sk-test"
    assert cfg.vision_model and cfg.text_model and cfg.embedding_model
    assert cfg.embedding_dims == 1024


def test_load_neo4j_config(repo_config_paths):
    _, neo4j_path = repo_config_paths
    cfg = load_neo4j_config(neo4j_path)
    assert cfg.uri.startswith("bolt://")
    assert cfg.password == "pw-test"
    assert cfg.database == "neo4j"


def test_missing_api_key_raises(repo_config_paths, monkeypatch):
    models_path, _ = repo_config_paths
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    import pytest
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        load_models_config(models_path)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'movie_kg'`

- [ ] **Step 4: 安装为可编辑包并实现 config.py**

先 `python -m venv .venv && source .venv/Scripts/activate && pip install -e ".[dev]"`(Windows Git Bash)。然后写 `src/movie_kg/config.py`:

```python
import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ModelsConfig:
    base_url: str
    api_key: str
    vision_model: str
    text_model: str
    embedding_model: str
    embedding_dims: int


@dataclass
class Neo4jConfig:
    uri: str
    user: str
    password: str
    database: str


def _read_yaml(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_models_config(path: str | Path = "config/models.yaml") -> ModelsConfig:
    raw = _read_yaml(path)
    api_key = os.environ.get(raw["api_key_env"], "")
    if not api_key:
        raise RuntimeError(f"缺少环境变量 {raw['api_key_env']}(在 .env 中设置)")
    return ModelsConfig(
        base_url=raw["base_url"], api_key=api_key,
        vision_model=raw["vision_model"], text_model=raw["text_model"],
        embedding_model=raw["embedding_model"], embedding_dims=int(raw["embedding_dims"]),
    )


def load_neo4j_config(path: str | Path = "config/neo4j.yaml") -> Neo4jConfig:
    raw = _read_yaml(path)
    return Neo4jConfig(
        uri=raw["uri"], user=raw["user"],
        password=os.environ.get(raw["password_env"], ""),
        database=raw.get("database", "neo4j"),
    )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_config.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .env.example config/ src/ tests/
git commit -m "feat: project scaffold with config loading"
```

---

### Task 2: 场景元数据模型、SRT 解析与数据加载校验

**Files:**
- Create: `src/movie_kg/models/__init__.py`、`src/movie_kg/models/scene.py`、`src/movie_kg/ingestion/__init__.py`、`src/movie_kg/ingestion/srt.py`、`src/movie_kg/ingestion/data_loader.py`、`tests/fixtures.py`、`tests/test_srt.py`、`tests/test_data_loader.py`

**Interfaces:**
- Produces: `CharacterMeta(name: str, aliases: list[str] = [])`、`Dialogue(char, text, ts, emotion=None, action=None)`、`SceneMeta(scene_id, movie_id, start_ts, end_ts, characters: list[CharacterMeta], dialogues: list[Dialogue], location: str, virtual_time: str, props: list[str] = [], keyframes: list[str] = [])`(全部 pydantic BaseModel)、`SrtEntry(start: str, end: str, text: str)`、`parse_ts(ts: str) -> float`、`parse_srt(text: str) -> list[SrtEntry]`、`SceneData(meta: SceneMeta, srt_text: str, video_path: Path)`、`MovieData(movie_id: str, title: str, scenes: list[SceneData])`、`InputValidationError(errors: list[str])`、`load_movie(movie_dir: str | Path) -> MovieData`、测试助手 `make_movie_dir(tmp_path: Path, n_scenes: int = 2) -> Path`、`make_video(path: Path, seconds: int = 2, fps: int = 12) -> None`(tests/fixtures.py,后续任务复用)

- [ ] **Step 1: 写测试助手与失败测试**

`tests/fixtures.py`:

```python
from pathlib import Path

import av
from PIL import Image


def make_video(path: Path, seconds: int = 2, fps: int = 12) -> None:
    """生成逐帧变色的合成视频,供帧采样与端到端测试使用。"""
    container = av.open(str(path), "w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
    for i in range(seconds * fps):
        img = Image.new("RGB", (64, 48), ((i * 7) % 256, (i * 13) % 256, (i * 29) % 256))
        for packet in stream.encode(av.VideoFrame.from_image(img)):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def make_movie_dir(tmp_path: Path, n_scenes: int = 2) -> Path:
    """构造完整的单部电影输入目录(视频/SRT/元数据),返回目录路径。"""
    import json
    import shutil

    movie_dir = tmp_path / "data" / "movies" / "m1"
    for sub in ("video", "subtitles", "scenes", "checkpoints"):
        (movie_dir / sub).mkdir(parents=True, exist_ok=True)
    (movie_dir / "movie.json").write_text(
        json.dumps({"movie_id": "m1", "title": "测试影片"}), encoding="utf-8")
    windows = [("00:00:00", "00:00:02"), ("00:00:02", "00:00:04"),
               ("00:00:04", "00:00:06")]
    meta = {
        "characters": [{"name": "张三", "aliases": ["老张"]}, {"name": "李四", "aliases": []}],
        "dialogues": [
            {"char": "张三", "text": "你到底想怎样?", "ts": "00:00:01",
             "emotion": "愤怒", "action": "拍桌"},
            {"char": "李四", "text": "我只是来谈生意的。", "ts": "00:00:01"},
        ],
        "location": "码头仓库", "virtual_time": "第一天傍晚",
        "props": ["枪"], "keyframes": [],
    }
    for i in range(n_scenes):
        seq = i + 1
        sid = f"scene_{seq:03d}"
        start, end = windows[i]
        m = dict(meta, scene_id=sid, movie_id="m1", start_ts=start, end_ts=end,
                 location="码头仓库" if i == 0 else "警局办公室",
                 virtual_time="第一天傍晚" if i == 0 else "第二天清晨")
        (movie_dir / "scenes" / f"{sid}.json").write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8")
        (movie_dir / "subtitles" / f"{sid}.srt").write_text(
            f"1\n{start},000 --> {end},000\n你到底想怎样?\n\n"
            f"2\n{start},500 --> {end},000\n我只是来谈生意的。\n", encoding="utf-8")
        make_video(movie_dir / "video" / f"{sid}.mp4", seconds=2)
    return movie_dir
```

`tests/test_srt.py`:

```python
from movie_kg.ingestion.srt import parse_srt, parse_ts


def test_parse_ts():
    assert parse_ts("00:03:12") == 192.0
    assert parse_ts("00:03:12,500") == 192.5
    assert parse_ts("01:00:00") == 3600.0


def test_parse_srt_multiline():
    text = "1\n00:00:01,000 --> 00:00:03,500\n第一行\n第二行\n\n2\n00:00:04,000 --> 00:00:05,000\n你到底想怎样?\n"
    entries = parse_srt(text)
    assert len(entries) == 2
    assert entries[0].text == "第一行\n第二行"
    assert entries[1].start == "00:00:04,000"
```

`tests/test_data_loader.py`:

```python
import pytest

from movie_kg.ingestion.data_loader import InputValidationError, load_movie
from tests.fixtures import make_movie_dir


def test_load_movie_happy_path(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    movie = load_movie(movie_dir)
    assert movie.movie_id == "m1" and movie.title == "测试影片"
    assert [s.meta.scene_id for s in movie.scenes] == ["scene_001", "scene_002"]
    s1 = movie.scenes[0]
    assert s1.meta.characters[0].name == "张三"
    assert "你到底想怎样?" in s1.srt_text
    assert s1.video_path.name == "scene_001.mp4"


def test_missing_video_reports_error(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    (movie_dir / "video" / "scene_001.mp4").unlink()
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("scene_001.mp4" in msg for msg in e.value.errors)


def test_dialogue_char_not_in_roster(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    import json
    p = movie_dir / "scenes" / "scene_001.json"
    m = json.loads(p.read_text(encoding="utf-8"))
    m["dialogues"][0]["char"] = "王五"
    p.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("王五" in msg for msg in e.value.errors)


def test_srt_outside_scene_window(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    (movie_dir / "subtitles" / "scene_001.srt").write_text(
        "1\n00:10:00,000 --> 00:10:05,000\n越界台词\n", encoding="utf-8")
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("00:10:00" in msg or "越界" in msg for msg in e.value.errors)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_srt.py tests/test_data_loader.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'movie_kg'`(或 models/ingestion 子模块)

- [ ] **Step 3: 实现 models/scene.py、ingestion/srt.py、ingestion/data_loader.py**

`src/movie_kg/models/scene.py`:

```python
from pathlib import Path

from pydantic import BaseModel


class CharacterMeta(BaseModel):
    name: str
    aliases: list[str] = []


class Dialogue(BaseModel):
    char: str
    text: str
    ts: str
    emotion: str | None = None
    action: str | None = None


class SceneMeta(BaseModel):
    scene_id: str
    movie_id: str
    start_ts: str
    end_ts: str
    characters: list[CharacterMeta]
    dialogues: list[Dialogue]
    location: str
    virtual_time: str
    props: list[str] = []
    keyframes: list[str] = []


class SrtEntry(BaseModel):
    start: str
    end: str
    text: str


class SceneData(BaseModel):
    meta: SceneMeta
    srt_text: str
    video_path: Path

    model_config = {"arbitrary_types_allowed": True}


class MovieData(BaseModel):
    movie_id: str
    title: str
    scenes: list[SceneData]
```

`src/movie_kg/ingestion/srt.py`:

```python
import re

from movie_kg.models.scene import SrtEntry

_TS = re.compile(r"(\d{2,}:\d{2}:\d{2})[,.](\d{1,3})")


def parse_ts(ts: str) -> float:
    """'00:03:12' / '00:03:12,500' / '00:03:12.500' -> 秒(float)。"""
    m = _TS.match(ts.strip())
    if m:
        ms = float(m.group(2).ljust(3, "0")) / 1000.0
        base = m.group(1)
    else:
        ms, base = 0.0, ts.strip()
    h, mi, s = (float(x) for x in base.split(":"))
    return h * 3600 + mi * 60 + s + ms


def parse_srt(text: str) -> list[SrtEntry]:
    entries: list[SrtEntry] = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 3:
            continue
        m = re.match(r"(.+?)\s*-->\s*(.+)", lines[1])
        if not m:
            continue
        entries.append(SrtEntry(start=m.group(1).strip(), end=m.group(2).strip(),
                                text="\n".join(lines[2:])))
    return entries
```

`src/movie_kg/ingestion/data_loader.py`:

```python
import json
from pathlib import Path

from movie_kg.ingestion.srt import parse_srt, parse_ts
from movie_kg.models.scene import MovieData, SceneData, SceneMeta


class InputValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def load_movie(movie_dir: str | Path) -> MovieData:
    movie_dir = Path(movie_dir)
    errors: list[str] = []
    movie_id = movie_dir.name
    info = json.loads((movie_dir / "movie.json").read_text(encoding="utf-8"))
    scene_files = sorted((movie_dir / "scenes").glob("scene_*.json"))
    scenes: list[SceneData] = []
    prev_end = -1.0
    for sf in scene_files:
        meta = SceneMeta.model_validate_json(sf.read_text(encoding="utf-8"))
        if meta.movie_id != movie_id:
            errors.append(f"{sf.name}: movie_id={meta.movie_id} 与目录 {movie_id} 不一致")
        video = movie_dir / "video" / f"{meta.scene_id}.mp4"
        srt_file = movie_dir / "subtitles" / f"{meta.scene_id}.srt"
        if not video.exists():
            errors.append(f"{meta.scene_id}: 缺少视频 {video.name}")
        if not srt_file.exists():
            errors.append(f"{meta.scene_id}: 缺少字幕 {srt_file.name}")
        start, end = parse_ts(meta.start_ts), parse_ts(meta.end_ts)
        if start >= end:
            errors.append(f"{meta.scene_id}: 起止时间非法({meta.start_ts} >= {meta.end_ts})")
        if start < prev_end:
            errors.append(f"{meta.scene_id}: 场景窗口与上一场景重叠")
        prev_end = end
        roster = {c.name for c in meta.characters} | {a for c in meta.characters for a in c.aliases}
        for d in meta.dialogues:
            if d.char not in roster:
                errors.append(f"{meta.scene_id}: 台词角色 {d.char} 不在角色名单中")
        srt_text = srt_file.read_text(encoding="utf-8") if srt_file.exists() else ""
        for entry in parse_srt(srt_text):
            if parse_ts(entry.start) < start or parse_ts(entry.end) > end:
                errors.append(f"{meta.scene_id}: 字幕越界({entry.start})")
        scenes.append(SceneData(meta=meta, srt_text=srt_text, video_path=video))
    if errors:
        raise InputValidationError(errors)
    return MovieData(movie_id=movie_id, title=info.get("title", movie_id), scenes=scenes)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_srt.py tests/test_data_loader.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/ tests/
git commit -m "feat: scene metadata models, SRT parsing, validated movie loader"
```

---

### Task 3: 帧采样器

**Files:**
- Create: `src/movie_kg/ingestion/frame_sampler.py`、`tests/test_frame_sampler.py`

**Interfaces:**
- Consumes: `make_video`(tests/fixtures.py)
- Produces: `Frame(ts: float, jpeg: bytes)`(dataclass)、`sample_frames(video_path: Path, keyframes: list[str], max_frames: int = 16) -> list[Frame]`。Task 7 的 A1 抽取器消费 `list[Frame]`。

- [ ] **Step 1: 写失败测试**

`tests/test_frame_sampler.py`:

```python
from tests.fixtures import make_video
from movie_kg.ingestion.frame_sampler import sample_frames


def test_uniform_sampling_caps_at_16(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=4)
    frames = sample_frames(video, keyframes=[])
    assert len(frames) == 16
    assert all(f.jpeg[:2] == b"\xff\xd8" for f in frames)  # JPEG magic
    assert frames == sorted(frames, key=lambda f: f.ts)


def test_keyframe_priority_included(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=4)
    frames = sample_frames(video, keyframes=["00:00:01"])
    assert 15 <= len(frames) <= 16  # 关键帧落在均匀网格上时允许少 1 帧
    assert any(abs(f.ts - 1.0) <= 0.5 for f in frames)


def test_small_budget(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=4)
    frames = sample_frames(video, keyframes=["00:00:01"], max_frames=4)
    assert 3 <= len(frames) <= 4
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_frame_sampler.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'movie_kg.ingestion.frame_sampler'`

- [ ] **Step 3: 实现帧采样器**

`src/movie_kg/ingestion/frame_sampler.py`:

```python
import io
from dataclasses import dataclass
from pathlib import Path

import av

from movie_kg.ingestion.srt import parse_ts


@dataclass
class Frame:
    ts: float
    jpeg: bytes


def sample_frames(video_path: Path, keyframes: list[str], max_frames: int = 16) -> list[Frame]:
    """关键帧时间点优先,均匀采样补足,总数 ≤ max_frames。"""
    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        duration = (container.duration or 0) / 1_000_000.0
        targets: list[float] = sorted(
            {min(max(parse_ts(k), 0.0), duration) for k in keyframes})
        fill = max_frames - len(targets)
        if fill > 0 and duration > 0:
            step = duration / (fill + 1)
            targets += [step * (i + 1) for i in range(fill)]
        deduped: list[float] = []
        for ts in sorted(targets):
            if not deduped or ts - deduped[-1] > 0.1:
                deduped.append(ts)
        frames: list[Frame] = []
        for ts in deduped[:max_frames]:
            container.seek(int(ts * 1_000_000), stream=stream)
            got = None
            for packet in container.demux(stream):
                if got is not None:
                    break
                for decoded in packet.decode():
                    got = decoded
                    break
            if got is not None:
                buf = io.BytesIO()
                got.to_image().save(buf, format="JPEG", quality=85)
                frames.append(Frame(ts=ts, jpeg=buf.getvalue()))
        return frames
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_frame_sampler.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/ingestion/frame_sampler.py tests/test_frame_sampler.py
git commit -m "feat: scene frame sampler with keyframe priority and 16-frame budget"
```

---

### Task 4: LLM 客户端(OpenAI 兼容,JSON 重试与用量统计)

**Files:**
- Create: `src/movie_kg/llm.py`、`tests/fakes.py`、`tests/test_llm.py`

**Interfaces:**
- Consumes: `ModelsConfig`(Task 1)
- Produces: `LLMError(Exception)`、`LLMClient(cfg: ModelsConfig, client=None)`:
  - `chat_json(*, model: str, messages: list[dict], schema: type[BaseModel], temperature: float = 0.1, max_tokens: int = 4096, max_retries: int = 3) -> BaseModel`(带 `response_format={"type": "json_object"}`,校验失败重试,耗尽抛 `LLMError`)
  - `embed(texts: list[str]) -> list[list[float]]`
  - 属性 `usage: dict`(`prompt_tokens` / `completion_tokens` 累计)
  - `client` 参数可注入测试替身(duck-typed OpenAI 客户端)
- Produces(测试基建,后续任务通用): `tests/fakes.py` 的 `FakeOpenAI`(注入 `LLMClient` 的底层替身)、`ScriptedLLM`(直接替代 `LLMClient` 的高层替身,按队列返回 schema 实例或抛异常)、`MockSession`(记录 `run(query, **params)` 并按队列回放结果)、`MockDriver`。Task 7/8/9/13/14/15/17 的测试全部消费这三个替身。

- [ ] **Step 1: 写测试替身与失败测试**

`tests/fakes.py`:

```python
from types import SimpleNamespace


def _completion(content: str, pt: int = 10, ct: int = 5):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=pt, completion_tokens=ct))


class FakeOpenAI:
    """LLMClient 的底层替身:按队列返回文本或抛异常,记录全部调用。"""

    def __init__(self, replies: list):
        self._replies = list(replies)
        self.calls: list[dict] = []
        outer = self

        class Completions:
            def create(self, **kw):
                outer.calls.append(kw)
                r = outer._replies.pop(0)
                if isinstance(r, Exception):
                    raise r
                return _completion(r)

        class Embeddings:
            def create(self, **kw):
                outer.calls.append(kw)
                n = len(kw["input"])
                return SimpleNamespace(data=[
                    SimpleNamespace(index=i, embedding=[0.1, 0.2]) for i in range(n)])

        self.chat = SimpleNamespace(completions=Completions())
        self.embeddings = Embeddings()


class ScriptedLLM:
    """高层替身:chat_json 按队列返回 schema 校验后的对象或抛异常。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[tuple] = []
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def chat_json(self, *, model, messages, schema, **kw):
        self.calls.append((model, messages))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return schema.model_validate(r) if isinstance(r, dict) else r

    def embed(self, texts):
        return [[0.0] * 8 for _ in texts]


class MockSession:
    """neo4j Session 替身:记录查询并按队列回放记录列表。"""

    def __init__(self, results: list | None = None):
        self.queries: list[tuple[str, dict]] = []
        self._results = list(results or [])

    def run(self, query, **params):
        self.queries.append((query, params))
        return list(self._results.pop(0)) if self._results else []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class MockDriver:
    def __init__(self, session: MockSession):
        self._session = session

    def session(self, **kw):
        return self._session

    def close(self):
        pass
```

`tests/test_llm.py`:

```python
import pytest
from pydantic import BaseModel

from movie_kg.config import ModelsConfig
from movie_kg.llm import LLMClient, LLMError
from tests.fakes import FakeOpenAI

CFG = ModelsConfig(base_url="https://x", api_key="sk", vision_model="v",
                   text_model="t", embedding_model="e", embedding_dims=8)


class Out(BaseModel):
    answer: str


def test_valid_json_parsed():
    llm = LLMClient(CFG, client=FakeOpenAI(['{"answer": "ok"}']))
    assert llm.chat_json(model="t", messages=[], schema=Out).answer == "ok"


def test_retry_then_success():
    llm = LLMClient(CFG, client=FakeOpenAI(["不是json", '{"answer": "ok"}']))
    assert llm.chat_json(model="t", messages=[], schema=Out).answer == "ok"
    assert len(llm._client.calls) == 2


def test_exhausted_retries_raise():
    llm = LLMClient(CFG, client=FakeOpenAI(["bad", "bad2", "bad3"]))
    with pytest.raises(LLMError):
        llm.chat_json(model="t", messages=[], schema=Out, max_retries=3)


def test_usage_accumulates():
    llm = LLMClient(CFG, client=FakeOpenAI(['{"answer": "1"}', '{"answer": "2"}']))
    llm.chat_json(model="t", messages=[], schema=Out)
    llm.chat_json(model="t", messages=[], schema=Out)
    assert llm.usage == {"prompt_tokens": 20, "completion_tokens": 10}


def test_embed_order_and_count():
    llm = LLMClient(CFG, client=FakeOpenAI([]))
    vecs = llm.embed(["a", "b", "c"])
    assert len(vecs) == 3 and len(vecs[0]) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_llm.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'movie_kg.llm'`

- [ ] **Step 3: 实现 LLMClient**

`src/movie_kg/llm.py`:

```python
from typing import Any

from pydantic import BaseModel, ValidationError


class LLMError(Exception):
    pass


class LLMClient:
    """OpenAI 兼容端点封装:结构化 JSON 输出 + 重试 + 用量统计。"""

    def __init__(self, cfg, client: Any = None):
        from openai import OpenAI
        self.cfg = cfg
        self._client = client or OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def chat_json(self, *, model: str, messages: list[dict], schema: type[BaseModel],
                  temperature: float = 0.1, max_tokens: int = 4096,
                  max_retries: int = 3) -> BaseModel:
        last_err: Exception | None = None
        for _ in range(max_retries):
            resp = self._client.chat.completions.create(
                model=model, messages=messages, temperature=temperature,
                max_tokens=max_tokens, response_format={"type": "json_object"})
            self._add_usage(resp)
            try:
                return schema.model_validate_json(resp.choices[0].message.content or "")
            except ValidationError as e:
                last_err = e
        raise LLMError(f"JSON 校验失败({max_retries} 次重试后): {last_err}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.cfg.embedding_model, input=texts)
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]

    def _add_usage(self, resp) -> None:
        u = getattr(resp, "usage", None)
        if u is not None:
            self.usage["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
            self.usage["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_llm.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/llm.py tests/fakes.py tests/test_llm.py
git commit -m "feat: OpenAI-compatible LLM client with JSON retry and usage tracking"
```

---

### Task 5: 抽取结果模型与 A1/A2 解析(角色对齐 + 确定性 ID)

**Files:**
- Create: `src/movie_kg/models/extraction.py`、`src/movie_kg/extraction/__init__.py`、`src/movie_kg/extraction/parse.py`、`tests/test_parse.py`

**Interfaces:**
- Consumes: `SceneMeta` / `CharacterMeta`(Task 2)
- Produces(`src/movie_kg/models/extraction.py`):

```python
Participant(name, emotion: str|None, role_description: str|None, items: list[str])
ExtractedEvent(event_type: Literal["dialogue","conflict","action","turning_point","discovery"],
               topic, description, participating_characters: list[Participant],
               subevents: list[ExtractedEvent] = [])
A1Output(events: list[ExtractedEvent])
ExtractedRelation(char_a, char_b,
                  relation_type: Literal["friend","enemy","lover","family","mentor","other"],
                  is_new: bool, change_description: str|None, evidence_event_index: int)
A2Output(character_relations: list[ExtractedRelation])
SceneEvent(id, event_type, topic, description, scene_id, movie_id,
           order: int, is_subevent: bool, parent_id: str|None, participants: list[Participant])
ResolvedRelation(char_a, char_b, relation_type, is_new, change_description, start_event_id)
SceneExtraction(scene_id, movie_id, status: Literal["ok","failed"],
                events: list[SceneEvent] = [], relations: list[ResolvedRelation] = [])
```

- Produces(`src/movie_kg/extraction/parse.py`): `levenshtein(a: str, b: str) -> int`、`align_name(name: str, roster: list[CharacterMeta]) -> str`、`parse_a1(raw: dict, meta: SceneMeta) -> list[SceneEvent]`、`parse_a2(raw: dict, main_events: list[SceneEvent], roster: list[CharacterMeta]) -> list[ResolvedRelation]`。Task 7/8 消费。

**ID/序号规则(锁定)**: 事件序号 `order` 为场景内 1 起的单调计数,主事件按输出顺序、每个子事件紧随其父事件计数;`id = f"evt_{movie_id}_{seq:03d}_{order:02d}"`,`seq = int(scene_id.rsplit("_", 1)[1])`;子事件 `is_subevent=True` 且 `parent_id` 指向父事件。`global_order = seq * 1000 + order` 由 Task 10 写图时直接计算赋值。

- [ ] **Step 1: 写失败测试**

`tests/test_parse.py`:

```python
import pytest

from movie_kg.extraction.parse import align_name, levenshtein, parse_a1, parse_a2
from movie_kg.models.scene import CharacterMeta, SceneMeta
from movie_kg.models.extraction import ExtractedEvent, Participant

ROSTER = [CharacterMeta(name="张三", aliases=["老张"]), CharacterMeta(name="李四", aliases=[])]
META = SceneMeta(scene_id="scene_003", movie_id="m1", start_ts="00:00:00",
                 end_ts="00:02:00", characters=ROSTER, dialogues=[], location="x",
                 virtual_time="y")


def test_levenshtein():
    assert levenshtein("张三", "张叁") == 1
    assert levenshtein("abc", "abc") == 0


def test_align_name_variants():
    assert align_name("张三", ROSTER) == "张三"        # 精确
    assert align_name("老张", ROSTER) == "张三"        # 别名
    assert align_name("张叁", ROSTER) == "张三"        # 编辑距离 ≤2
    assert align_name("大老板张三", ROSTER) == "张三"   # 包含
    assert align_name("王五", ROSTER) == "未识别:王五"  # 对不上 -> 保留前缀


def _a1_raw():
    return {"events": [{
        "event_type": "conflict", "topic": "对峙", "description": "张三与李四对峙",
        "participating_characters": [
            {"name": "张三", "emotion": "愤怒", "role_description": "发起者", "items": ["枪"]},
            {"name": "王五", "emotion": None, "role_description": None, "items": []}],
        "subevents": [{
            "event_type": "action", "topic": "拔枪",
            "description": "张三拔枪", "participating_characters": []}]}]}


def test_parse_a1_assigns_ids_and_parents():
    events = parse_a1(_a1_raw(), META)
    assert [e.id for e in events] == ["evt_m1_003_01", "evt_m1_003_02"]
    assert events[0].order == 1 and not events[0].is_subevent
    assert events[1].is_subevent and events[1].parent_id == "evt_m1_003_01"
    assert events[1].order == 2
    assert events[0].participants[0].name == "张三"
    assert events[0].participants[1].name == "未识别:王五"


def test_parse_a1_rejects_bad_event_type():
    raw = _a1_raw()
    raw["events"][0]["event_type"] = "romance"
    with pytest.raises(Exception):
        parse_a1(raw, META)


def test_parse_a2_maps_evidence_index():
    events = parse_a1(_a1_raw(), META)
    raw = {"character_relations": [{
        "char_a": "张三", "char_b": "李四", "relation_type": "enemy",
        "is_new": True, "change_description": None, "evidence_event_index": 0}]}
    rels = parse_a2(raw, events, ROSTER)
    assert rels[0].start_event_id == "evt_m1_003_01"
    assert rels[0].char_a == "张三"


def test_parse_a2_rejects_index_out_of_range():
    events = parse_a1(_a1_raw(), META)
    raw = {"character_relations": [{
        "char_a": "张三", "char_b": "李四", "relation_type": "enemy",
        "is_new": True, "change_description": None, "evidence_event_index": 5}]}
    with pytest.raises(ValueError):
        parse_a2(raw, events, ROSTER)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_parse.py -v`
Expected: FAIL,`ModuleNotFoundError`(movie_kg.models.extraction)

- [ ] **Step 3: 实现模型与解析**

`src/movie_kg/models/extraction.py`:

```python
from typing import Literal

from pydantic import BaseModel


class Participant(BaseModel):
    name: str
    emotion: str | None = None
    role_description: str | None = None
    items: list[str] = []


class ExtractedEvent(BaseModel):
    event_type: Literal["dialogue", "conflict", "action", "turning_point", "discovery"]
    topic: str
    description: str
    participating_characters: list[Participant] = []
    subevents: list["ExtractedEvent"] = []


class A1Output(BaseModel):
    events: list[ExtractedEvent]


class ExtractedRelation(BaseModel):
    char_a: str
    char_b: str
    relation_type: Literal["friend", "enemy", "lover", "family", "mentor", "other"]
    is_new: bool
    change_description: str | None = None
    evidence_event_index: int


class A2Output(BaseModel):
    character_relations: list[ExtractedRelation]


class SceneEvent(BaseModel):
    id: str
    event_type: str
    topic: str
    description: str
    scene_id: str
    movie_id: str
    order: int
    is_subevent: bool = False
    parent_id: str | None = None
    participants: list[Participant] = []


class ResolvedRelation(BaseModel):
    char_a: str
    char_b: str
    relation_type: str
    is_new: bool
    change_description: str | None = None
    start_event_id: str


class SceneExtraction(BaseModel):
    scene_id: str
    movie_id: str
    status: Literal["ok", "failed"]
    events: list[SceneEvent] = []
    relations: list[ResolvedRelation] = []
```

`src/movie_kg/extraction/parse.py`:

```python
from movie_kg.models.extraction import (A1Output, A2Output, ExtractedEvent,
                                        ResolvedRelation, SceneEvent)
from movie_kg.models.scene import CharacterMeta, SceneMeta


def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def align_name(name: str, roster: list[CharacterMeta]) -> str:
    """名单精确/别名/模糊(编辑距离≤2 或包含)对齐;失败保留 '未识别:' 前缀。"""
    table = {c.name: c.name for c in roster}
    for c in roster:
        for alias in c.aliases:
            table[alias] = c.name
    if name in table:
        return table[name]

    def close(a: str, b: str) -> bool:
        return (levenshtein(a, b) <= 2
                or (len(a) > 1 and len(b) > 1 and (a in b or b in a)))

    for c in roster:
        if close(name, c.name) or any(close(name, a) for a in c.aliases):
            return c.name
    return f"未识别:{name}"


def parse_a1(raw: dict, meta: SceneMeta) -> list[SceneEvent]:
    """A1 原始输出 -> 扁平化 SceneEvent 列表(ID 确定性分配,子事件紧随父事件)。"""
    out = A1Output.model_validate(raw)
    seq = int(meta.scene_id.rsplit("_", 1)[1])
    events: list[SceneEvent] = []
    counter = 0

    def make(ev: ExtractedEvent, parent_id: str | None) -> SceneEvent:
        nonlocal counter
        counter += 1
        return SceneEvent(
            id=f"evt_{meta.movie_id}_{seq:03d}_{counter:02d}",
            event_type=ev.event_type, topic=ev.topic, description=ev.description,
            scene_id=meta.scene_id, movie_id=meta.movie_id, order=counter,
            is_subevent=parent_id is not None, parent_id=parent_id,
            participants=[Participant.model_validate(
                p.model_dump(update={"name": align_name(p.name, meta.characters)}))
                for p in ev.participating_characters])

    from movie_kg.models.extraction import Participant
    for ev in out.events:
        main = make(ev, None)
        events.append(main)
        for sub in ev.subevents:
            events.append(make(sub, main.id))
    return events


def parse_a2(raw: dict, main_events: list[SceneEvent],
             roster: list[CharacterMeta]) -> list[ResolvedRelation]:
    """A2 原始输出 -> 关系列表;evidence_event_index(主事件 0 起)映射为 start_event_id。"""
    out = A2Output.model_validate(raw)
    relations: list[ResolvedRelation] = []
    for r in out.character_relations:
        if not (0 <= r.evidence_event_index < len(main_events)):
            raise ValueError(f"evidence_event_index 越界: {r.evidence_event_index}")
        relations.append(ResolvedRelation(
            char_a=align_name(r.char_a, roster), char_b=align_name(r.char_b, roster),
            relation_type=r.relation_type, is_new=r.is_new,
            change_description=r.change_description,
            start_event_id=main_events[r.evidence_event_index].id))
    return relations
```

注: `parse_a1` 中 `Participant` 的 import 提到模块顶部(实现时放文件头部,不要放在函数内)。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_parse.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/models/extraction.py src/movie_kg/extraction/ tests/test_parse.py
git commit -m "feat: extraction models with deterministic IDs and character alignment"
```

---

### Task 6: Checkpoint 管理

**Files:**
- Create: `src/movie_kg/extraction/checkpoints.py`、`tests/test_checkpoints.py`

**Interfaces:**
- Consumes: `SceneExtraction`(Task 5)
- Produces: `CheckpointStore(movie_dir: Path)`,方法:`save(ext: SceneExtraction) -> Path`(原子写 `data/movies/<id>/checkpoints/scene_{seq:03d}.json`)、`load(scene_id: str) -> SceneExtraction | None`、`all_done() -> list[str]`(status=ok 的 scene_id)、`all_failed() -> list[str]`、`has(scene_id: str) -> bool`。Task 8 消费。

- [ ] **Step 1: 写失败测试**

`tests/test_checkpoints.py`:

```python
from movie_kg.extraction.checkpoints import CheckpointStore
from movie_kg.models.extraction import SceneExtraction


def _ext(scene_id="scene_001", status="ok"):
    return SceneExtraction(scene_id=scene_id, movie_id="m1", status=status)


def test_save_load_roundtrip(tmp_path):
    store = CheckpointStore(tmp_path / "m1")
    ext = _ext()
    path = store.save(ext)
    assert path.exists() and path.name == "scene_001.json"
    assert store.load("scene_001") == ext


def test_load_missing_returns_none(tmp_path):
    store = CheckpointStore(tmp_path / "m1")
    assert store.load("scene_009") is None


def test_all_done_filters_failed(tmp_path):
    store = CheckpointStore(tmp_path / "m1")
    store.save(_ext("scene_001", "ok"))
    store.save(_ext("scene_002", "failed"))
    store.save(_ext("scene_003", "ok"))
    assert store.all_done() == ["scene_001", "scene_003"]
    assert store.all_failed() == ["scene_002"]
    assert store.has("scene_002")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_checkpoints.py -v`
Expected: FAIL,`ModuleNotFoundError`

- [ ] **Step 3: 实现 CheckpointStore**

`src/movie_kg/extraction/checkpoints.py`:

```python
import json
import os
from pathlib import Path

from movie_kg.models.extraction import SceneExtraction


class CheckpointStore:
    def __init__(self, movie_dir: str | Path):
        self.dir = Path(movie_dir) / "checkpoints"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, scene_id: str) -> Path:
        return self.dir / f"{scene_id}.json"

    def save(self, ext: SceneExtraction) -> Path:
        p = self.path(ext.scene_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(ext.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, p)
        return p

    def load(self, scene_id: str) -> SceneExtraction | None:
        p = self.path(scene_id)
        if not p.exists():
            return None
        return SceneExtraction.model_validate_json(p.read_text(encoding="utf-8"))

    def _with_status(self, status: str) -> list[str]:
        out = []
        for p in sorted(self.dir.glob("scene_*.json")):
            if self.load(p.stem) is not None and self.load(p.stem).status == status:
                out.append(p.stem)
        return out

    def all_done(self) -> list[str]:
        return self._with_status("ok")

    def all_failed(self) -> list[str]:
        return self._with_status("failed")

    def has(self, scene_id: str) -> bool:
        return self.path(scene_id).exists()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_checkpoints.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/extraction/checkpoints.py tests/test_checkpoints.py
git commit -m "feat: per-scene checkpoint store with atomic writes"
```

---

### Task 7: A1 事件抽取器(Prompt 加载 + 多模态消息)

**Files:**
- Create: `src/movie_kg/prompts.py`、`config/prompts/event_extraction.md`、`src/movie_kg/extraction/event_extractor.py`、`tests/test_prompts.py`、`tests/test_event_extractor.py`

**Interfaces:**
- Consumes: `LLMClient`/`ScriptedLLM`(Task 4)、`Frame`(Task 3)、`SceneData`(Task 2)、`parse_a1`/`SceneEvent`(Task 5)
- Produces: `load_prompt(name: str) -> str`(读 `config/prompts/{name}.md`)、`render(template: str, **tokens) -> str`(替换 `<<TOKEN>>` 占位符)、`extract_scene_events(llm, frames: list[Frame], scene: SceneData) -> list[SceneEvent]`(组多模态消息 → `llm.chat_json(model=cfg.vision_model, schema=A1Output)` → `parse_a1`;`LLMError` 向上抛,由 Task 8 的管线标记 failed)。Task 8/9/13/15 消费 `load_prompt`/`render`。

- [ ] **Step 1: 写 Prompt 文件与失败测试**

`config/prompts/event_extraction.md`(全文):

```text
你是影视剧剧情分析专家。分析以下场景的帧序列与台词,提取剧情事件。

场景先验: 角色名单=<<ROSTER>>, 标注地点=<<LOCATION>>, 标注时间=<<TIME>>
台词(SRT):
<<SRT>>

要求:
- 事件按发生顺序排列;子事件同样输出完整结构,不输出纯文本描述
- 角色名必须从角色名单中选择;无法确定时标记 "未识别:原名"
- event_type ∈ dialogue / conflict / action / turning_point / discovery

输出 JSON:
{"events": [{"event_type": "...", "topic": "...", "description": "...",
  "participating_characters": [{"name": "...", "emotion": "...", "role_description": "...", "items": ["..."]}],
  "subevents": [{"event_type": "...", "topic": "...", "description": "...", "participating_characters": []}]}]}
```

`tests/test_prompts.py`:

```python
from movie_kg.prompts import load_prompt, render


def test_load_prompt_reads_file():
    text = load_prompt("event_extraction")
    assert "剧情分析专家" in text and "<<SRT>>" in text


def test_render_replaces_tokens():
    out = render("A=<<A>>, B=<<B>>", A="1", B="2")
    assert out == "A=1, B=2" and "<<" not in out
```

`tests/test_event_extractor.py`:

```python
import pytest

from movie_kg.extraction.event_extractor import extract_scene_events
from movie_kg.ingestion.frame_sampler import Frame
from movie_kg.llm import LLMError
from movie_kg.models.scene import MovieData
from tests.fixtures import make_movie_dir
from movie_kg.ingestion.data_loader import load_movie

A1 = {"events": [{
    "event_type": "conflict", "topic": "对峙", "description": "张三与李四在仓库对峙",
    "participating_characters": [
        {"name": "张三", "emotion": "愤怒", "role_description": "发起者", "items": []}],
    "subevents": []}]}


def _movie(tmp_path) -> MovieData:
    return load_movie(make_movie_dir(tmp_path))


def test_extract_parses_and_aligns(tmp_path):
    from tests.fakes import ScriptedLLM
    llm = ScriptedLLM(A1)
    movie = _movie(tmp_path)
    frames = [Frame(ts=0.5, jpeg=b"\xff\xd8fake")]
    events = extract_scene_events(llm, frames, movie.scenes[0])
    assert events[0].id == "evt_m1_001_01"
    assert events[0].participants[0].name == "张三"
    model, messages = llm.calls[0]
    assert model  # 使用 vision 模型(由 llm 替身记录)
    user_content = messages[-1]["content"]
    assert any(p.get("type") == "image_url" for p in user_content)


def test_extract_propagates_llm_error(tmp_path):
    from tests.fakes import ScriptedLLM
    llm = ScriptedLLM(LLMError("bad json"))
    movie = _movie(tmp_path)
    with pytest.raises(LLMError):
        extract_scene_events(llm, [Frame(ts=0.0, jpeg=b"x")], movie.scenes[0])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_prompts.py tests/test_event_extractor.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'movie_kg.prompts'`

- [ ] **Step 3: 实现 prompts.py 与 event_extractor.py**

`src/movie_kg/prompts.py`:

```python
from pathlib import Path

PROMPT_DIR = Path("config/prompts")


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


def render(template: str, **tokens: str) -> str:
    out = template
    for key, value in tokens.items():
        out = out.replace(f"<<{key.upper()}>>", str(value))
    return out
```

`src/movie_kg/extraction/event_extractor.py`:

```python
import base64
import json

from movie_kg.extraction.parse import parse_a1
from movie_kg.ingestion.frame_sampler import Frame
from movie_kg.models.extraction import A1Output
from movie_kg.models.scene import SceneData
from movie_kg.prompts import load_prompt, render


def _roster_text(scene: SceneData) -> str:
    return "; ".join(
        f"{c.name}({','.join(c.aliases)})" if c.aliases else c.name
        for c in scene.meta.characters)


def extract_scene_events(llm, frames: list[Frame], scene: SceneData) -> list:
    """A1: 帧 + 台词 + 先验 -> 结构化事件(LLMError 向上抛)。"""
    system = render(load_prompt("event_extraction"),
                    ROSTER=_roster_text(scene),
                    LOCATION=scene.meta.location,
                    TIME=scene.meta.virtual_time,
                    SRT=scene.srt_text.strip() or "(无台词)")
    content: list[dict] = [{"type": "text", "text": "请分析以下帧序列与台词,输出 JSON。"}]
    for f in frames:
        b64 = base64.b64encode(f.jpeg).decode("ascii")
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]
    raw = llm.chat_json(model=getattr(llm.cfg, "vision_model", "vision"),
                        messages=messages, schema=A1Output)
    return parse_a1(json.loads(raw.model_dump_json()), scene.meta)
```

注: `ScriptedLLM` 没有 `cfg` 属性,故用 `getattr(llm, "cfg", None)` 取 vision 模型名 —— 实现为:

```python
    cfg = getattr(llm, "cfg", None)
    model = cfg.vision_model if cfg is not None else "vision"
    raw = llm.chat_json(model=model, messages=messages, schema=A1Output)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_prompts.py tests/test_event_extractor.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/prompts.py src/movie_kg/extraction/event_extractor.py config/prompts/ tests/
git commit -m "feat: A1 scene event extractor with multimodal messages and prompt loading"
```

---

### Task 8: A2 关系抽取器 + Phase A 编排(断点续跑)

**Files:**
- Create: `config/prompts/relation_update.md`、`src/movie_kg/extraction/relation_extractor.py`、`src/movie_kg/extraction/pipeline.py`、`tests/test_relation_extractor.py`、`tests/test_pipeline_a.py`

**Interfaces:**
- Consumes: `ScriptedLLM`、`SceneEvent`/`parse_a2`、`CheckpointStore`、`extract_scene_events`、`sample_frames`、`load_movie`
- Produces: `extract_scene_relations(llm, events: list[SceneEvent], prior_relations: list[dict]) -> list[ResolvedRelation]`(prior_relations 形如 `{"char_a","char_b","relation_type"}`,来自已完成 checkpoint);`run_phase_a(llm, movie: MovieData, store: CheckpointStore, scene_range: tuple[int, int] | None = None, max_frames: int = 16) -> PhaseAResult`,其中 `PhaseAResult` 为 dataclass:`completed: list[str]`、`failed: list[str]`、`skipped: int`、`usage: dict`。Task 11 的 `kg build` 消费。

- [ ] **Step 1: 写 Prompt、测试**

`config/prompts/relation_update.md`(全文):

```text
你是影视剧剧情关系分析专家。根据本场景事件与角色互动,以及两人已有关系,判断是否出现新关系或关系变更。

已有关系表: <<PRIORS>>
本场景事件(带序号): <<EVENTS>>

规则: 全新关系 is_new=true;已有关系发生实质变化 is_new=false 并写 change_description。
输出 JSON:
{"character_relations": [{"char_a": "...", "char_b": "...",
  "relation_type": "friend|enemy|lover|family|mentor|other",
  "is_new": true, "change_description": null, "evidence_event_index": 0}]}
```

`tests/test_relation_extractor.py`:

```python
from movie_kg.extraction.event_extractor import extract_scene_events
from movie_kg.extraction.relation_extractor import extract_scene_relations
from movie_kg.ingestion.frame_sampler import Frame
from movie_kg.llm import LLMError
from tests.fakes import ScriptedLLM
from tests.fixtures import make_movie_dir
from movie_kg.ingestion.data_loader import load_movie

A1 = {"events": [{"event_type": "conflict", "topic": "对峙", "description": "张三与李四对峙",
                  "participating_characters": [
                      {"name": "张三", "emotion": "愤怒", "role_description": "发起者", "items": []},
                      {"name": "李四", "emotion": "紧张", "role_description": "应对", "items": []}],
                  "subevents": []}]}
A2 = {"character_relations": [{"char_a": "张三", "char_b": "李四", "relation_type": "enemy",
                               "is_new": True, "change_description": None,
                               "evidence_event_index": 0}]}


def test_relation_extraction_maps_ids(tmp_path):
    movie = load_movie(make_movie_dir(tmp_path))
    events = extract_scene_events(ScriptedLLM(A1), [Frame(0.0, b"x")], movie.scenes[0])
    rels = extract_scene_relations(
        ScriptedLLM(A2), events, movie.scenes[0].meta.characters,
        prior_relations=[])
    assert rels[0].start_event_id == "evt_m1_001_01"
    assert rels[0].relation_type == "enemy"
```

`tests/test_pipeline_a.py`:

```python
from movie_kg.extraction.checkpoints import CheckpointStore
from movie_kg.extraction.pipeline import run_phase_a
from movie_kg.llm import LLMError
from tests.fakes import ScriptedLLM
from tests.fixtures import make_movie_dir
from movie_kg.ingestion.data_loader import load_movie

A1 = {"events": [{"event_type": "action", "topic": "t", "description": "d",
                  "participating_characters": [{"name": "张三", "emotion": None,
                                                "role_description": None, "items": []}],
                  "subevents": []}]}
A2 = {"character_relations": []}


def test_phase_a_happy_path(tmp_path):
    d = make_movie_dir(tmp_path)
    movie = load_movie(d)
    store = CheckpointStore(d)
    llm = ScriptedLLM(A1, A2, A1, A2)  # scene1 A1,A2; scene2 A1,A2
    result = run_phase_a(llm, movie, store)
    assert result.completed == ["scene_001", "scene_002"]
    assert result.failed == [] and result.skipped == 0
    assert store.load("scene_001").status == "ok"


def test_phase_a_failure_does_not_block(tmp_path):
    d = make_movie_dir(tmp_path)
    movie = load_movie(d)
    store = CheckpointStore(d)
    llm = ScriptedLLM(LLMError("x"), A1, A2)  # scene1 失败;scene2 正常
    result = run_phase_a(llm, movie, store)
    assert result.failed == ["scene_001"]
    assert result.completed == ["scene_002"]
    assert store.load("scene_001").status == "failed"


def test_phase_a_skips_completed_on_rerun(tmp_path):
    d = make_movie_dir(tmp_path)
    movie = load_movie(d)
    store = CheckpointStore(d)
    run_phase_a(ScriptedLLM(A1, A2, A1, A2), movie, store)
    llm2 = ScriptedLLM()  # 不再提供任何响应:若被调用会 IndexError
    result = run_phase_a(llm2, movie, store)
    assert result.skipped == 2 and result.completed == []
    assert len(llm2.calls) == 0


def test_phase_a_scene_range(tmp_path):
    d = make_movie_dir(tmp_path, n_scenes=2)
    movie = load_movie(d)
    store = CheckpointStore(d)
    result = run_phase_a(ScriptedLLM(A1, A2), movie, store, scene_range=(1, 1))
    assert result.completed == ["scene_001"]


def test_phase_a_from_scratch(tmp_path):
    d = make_movie_dir(tmp_path)
    movie = load_movie(d)
    store = CheckpointStore(d)
    run_phase_a(ScriptedLLM(A1, A2, A1, A2), movie, store)
    result = run_phase_a(ScriptedLLM(A1, A2, A1, A2), movie, store, from_scratch=True)
    assert result.skipped == 0 and result.completed == ["scene_001", "scene_002"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_relation_extractor.py tests/test_pipeline_a.py -v`
Expected: FAIL,`ModuleNotFoundError`(relation_extractor / pipeline)

- [ ] **Step 3: 实现 relation_extractor.py 与 pipeline.py**

`src/movie_kg/extraction/relation_extractor.py`:

```python
import json

from movie_kg.extraction.parse import parse_a2
from movie_kg.models.extraction import A2Output, SceneEvent
from movie_kg.models.scene import CharacterMeta
from movie_kg.prompts import load_prompt, render


def _events_text(events: list[SceneEvent]) -> str:
    mains = [e for e in events if not e.is_subevent]
    return "\n".join(
        f"[{i}] {e.topic}: {e.description}("
        + ",".join(p.name for p in e.participants) + ")"
        for i, e in enumerate(mains))


def extract_scene_relations(llm, events: list[SceneEvent],
                            roster: list[CharacterMeta],
                            prior_relations: list[dict]) -> list:
    """A2: 事件摘要 + 已有关系表 -> 本场景关系(新/变更)。"""
    priors = "\n".join(
        f"{p['char_a']} - {p['relation_type']} - {p['char_b']}" for p in prior_relations
    ) or "(暂无)"
    system = render(load_prompt("relation_update"),
                    PRIORS=priors, EVENTS=_events_text(events))
    cfg = getattr(llm, "cfg", None)
    model = cfg.vision_model if cfg is not None else "vision"
    raw = llm.chat_json(model=model, messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": "请输出本场景角色关系 JSON。"}],
        schema=A2Output)
    mains = [e for e in events if not e.is_subevent]
    return parse_a2(json.loads(raw.model_dump_json()), mains, roster)
```

注: A2 只需文本即可,但走同一个 vision 模型保持单一依赖;`roster` 由调用方传 `scene.meta.characters`(Task 8 管线与 Task 11 CLI 均如此)。

`src/movie_kg/extraction/pipeline.py`:

```python
from dataclasses import dataclass, field

from movie_kg.extraction.checkpoints import CheckpointStore
from movie_kg.extraction.event_extractor import extract_scene_events
from movie_kg.extraction.relation_extractor import extract_scene_relations
from movie_kg.ingestion.frame_sampler import sample_frames
from movie_kg.llm import LLMError
from movie_kg.models.extraction import SceneExtraction
from movie_kg.models.scene import MovieData


@dataclass
class PhaseAResult:
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: int = 0
    usage: dict = field(default_factory=lambda: {"prompt_tokens": 0,
                                                 "completion_tokens": 0})


def _prior_relations(store: CheckpointStore) -> list[dict]:
    """从已完成 checkpoint 汇总每对角色最新有效关系。"""
    latest: dict[tuple[str, str], dict] = {}
    for sid in store.all_done():
        ext = store.load(sid)
        if ext is None:
            continue
        for r in ext.relations:
            latest[(r.char_a, r.char_b)] = {"char_a": r.char_a, "char_b": r.char_b,
                                            "relation_type": r.relation_type}
    return list(latest.values())


def run_phase_a(llm, movie: MovieData, store: CheckpointStore,
                scene_range: tuple[int, int] | None = None,
                from_scratch: bool = False, max_frames: int = 16) -> PhaseAResult:
    result = PhaseAResult()
    for scene in movie.scenes:
        seq = int(scene.meta.scene_id.rsplit("_", 1)[1])
        if scene_range and not (scene_range[0] <= seq <= scene_range[1]):
            continue
        if from_scratch:
            store.path(scene.meta.scene_id).unlink(missing_ok=True)
        elif store.has(scene.meta.scene_id):
            result.skipped += 1
            continue
        try:
            frames = sample_frames(scene.video_path, scene.meta.keyframes,
                                   max_frames=max_frames)
            events = extract_scene_events(llm, frames, scene)
            relations = extract_scene_relations(
                llm, events, scene.meta.characters, _prior_relations(store))
            store.save(SceneExtraction(scene_id=scene.meta.scene_id,
                                       movie_id=movie.movie_id, status="ok",
                                       events=events, relations=relations))
            result.completed.append(scene.meta.scene_id)
        except LLMError:
            store.save(SceneExtraction(scene_id=scene.meta.scene_id,
                                       movie_id=movie.movie_id, status="failed"))
            result.failed.append(scene.meta.scene_id)
    result.usage = dict(getattr(llm, "usage", result.usage))
    return result
```

同步修正 Task 8 Step 1 测试中的调用(已在上文更新为传 roster 的形式)。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_relation_extractor.py tests/test_pipeline_a.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add config/prompts/relation_update.md src/movie_kg/extraction/relation_extractor.py src/movie_kg/extraction/pipeline.py tests/
git commit -m "feat: A2 relation extractor and Phase A orchestration with resume"
```

---

### Task 9: 全局 pass(B1 时间线排序 / B2 别名归一 / B3 因果抽取)

**Files:**
- Create: `config/prompts/vt_ordering.md`、`config/prompts/alias_merge.md`、`config/prompts/causal_detection.md`、`src/movie_kg/extraction/global_passes.py`、`tests/test_global_passes.py`

**Interfaces:**
- Consumes: `ScriptedLLM`、`MovieData`、`SceneExtraction`
- Produces(`src/movie_kg/extraction/global_passes.py`):

```python
VtInfo(name: str, scene_ids: list[int])                     # pydantic
OrderedVt(name: str, timeline_index: int, description: str)
NameContext(name: str, aliases: list[str], context: str)
MergeGroup(canonical: str, aliases: list[str], description: str)
AliasPlan(characters: list[MergeGroup], locations: list[MergeGroup])
  # 方法 resolve(kind: "character"|"location", name: str) -> str
EventPair(cause_id: str, cause_topic: str, effect_id: str, effect_topic: str)
CausalLink(cause_id: str, effect_id: str, reason: str)
B1Output(ordered: list[OrderedVtRaw])  # OrderedVtRaw = {"name": str, "description": str}
B2Output / B3Output 见 Prompt

collect_vts(movie: MovieData) -> list[VtInfo]
collect_name_contexts(movie: MovieData, extractions: list[SceneExtraction])
    -> tuple[list[NameContext], list[NameContext]]        # (角色, 地点)
pass_b1_order_vts(llm, vts: list[VtInfo]) -> list[OrderedVt]   # LLM 漏掉的按原序追加
pass_b2_merge_aliases(llm, characters: list[NameContext],
                      locations: list[NameContext]) -> AliasPlan
build_causal_pairs(movie: MovieData, extractions: list[SceneExtraction])
    -> list[EventPair]      # 同场景相邻主事件对 + 跨场景边界对(前场景末主事件→后场景首主事件)
pass_b3_causal(llm, pairs: list[EventPair]) -> list[CausalLink]  # 丢弃 pairs 之外的 id
```

Task 10(writer)消费 `OrderedVt`/`AliasPlan`/`CausalLink`。

- [ ] **Step 1: 写三个 Prompt 与失败测试**

`config/prompts/vt_ordering.md`:

```text
你是剧情时间线分析专家。将下列虚拟时间按剧情先后排成全序,并为每个时间写一句剧情意义描述。

虚拟时间及出现场景: <<VTS>>

输出 JSON:
{"ordered": [{"name": "第一天傍晚", "description": "故事开始的傍晚,码头冲突之夜"}]}
```

`config/prompts/alias_merge.md`:

```text
你是实体名称归一专家。判断下列名称中哪些指同一实体,输出归并组;每组给出规范名、别名列表和一句描述。没有重复的名称单独成组。

角色名称及上下文: <<CHARACTERS>>
地点名称及上下文: <<LOCATIONS>>

输出 JSON:
{"characters": [{"canonical": "张三", "aliases": ["老张"], "description": "码头帮派头目"}],
 "locations": [{"canonical": "码头仓库", "aliases": [], "description": "第一场冲突发生地"}]}
```

`config/prompts/causal_detection.md`:

```text
你是剧情因果分析专家。判断下列相邻事件对中,前者是否直接或间接导致后者。仅输出存在因果关系的事件对及理由;没有则输出空数组。

事件对: <<PAIRS>>

输出 JSON:
{"causal_links": [{"cause_id": "evt_m1_001_01", "effect_id": "evt_m1_001_02",
                   "reason": "对峙升级直接引发拔枪"}]}
```

`tests/test_global_passes.py`:

```python
from movie_kg.extraction.checkpoints import CheckpointStore
from movie_kg.extraction.global_passes import (
    AliasPlan, build_causal_pairs, collect_name_contexts, collect_vts,
    pass_b1_order_vts, pass_b2_merge_aliases, pass_b3_causal)
from movie_kg.extraction.pipeline import run_phase_a
from movie_kg.models.extraction import SceneExtraction
from tests.fakes import ScriptedLLM
from tests.fixtures import make_movie_dir
from movie_kg.ingestion.data_loader import load_movie

A1 = {"events": [
    {"event_type": "conflict", "topic": "对峙", "description": "张三李四对峙",
     "participating_characters": [{"name": "张三", "emotion": None,
                                   "role_description": None, "items": []}],
     "subevents": []},
    {"event_type": "action", "topic": "拔枪", "description": "张三拔枪",
     "participating_characters": [], "subevents": []}]}
A2 = {"character_relations": []}


def _setup(tmp_path):
    d = make_movie_dir(tmp_path)
    movie = load_movie(d)
    store = CheckpointStore(d)
    run_phase_a(ScriptedLLM(A1, A2, A1, A2), movie, store)
    exts = [store.load(s.meta.scene_id) for s in movie.scenes]
    return movie, exts


def test_collect_vts_dedupes(tmp_path):
    movie, _ = _setup(tmp_path)
    vts = collect_vts(movie)
    assert [v.name for v in vts] == ["第一天傍晚", "第二天清晨"]
    assert vts[0].scene_ids == [1]


def test_b1_orders_and_backfills_missing(tmp_path):
    movie, _ = _setup(tmp_path)
    vts = collect_vts(movie)
    # LLM 只排了第二个,第一个应按原序追加到末尾
    ordered = pass_b1_order_vts(ScriptedLLM(
        {"ordered": [{"name": "第二天清晨", "description": "d2"}]}), vts)
    assert ordered[0].name == "第二天清晨" and ordered[0].timeline_index == 0
    assert ordered[1].name == "第一天傍晚" and ordered[1].timeline_index == 1
    assert ordered[1].description != ""


def test_b2_plan_resolve(tmp_path):
    chars, locs = collect_name_contexts(*_setup(tmp_path))
    plan: AliasPlan = pass_b2_merge_aliases(ScriptedLLM(
        {"characters": [{"canonical": "张三", "aliases": ["老张", "张老板"],
                         "description": "码头头目"}],
         "locations": []}), chars, locs)
    assert plan.resolve("character", "张老板") == "张三"
    assert plan.resolve("character", "李四") == "李四"      # 未命中保持原名
    assert plan.resolve("location", "警局办公室") == "警局办公室"


def test_build_causal_pairs_intra_and_boundary(tmp_path):
    movie, exts = _setup(tmp_path)
    pairs = build_causal_pairs(movie, exts)
    ids = [(p.cause_id, p.effect_id) for p in pairs]
    # scene_001 内: 01->02;边界: scene_001 末主事件 -> scene_002 首主事件
    assert ("evt_m1_001_01", "evt_m1_001_02") in ids
    assert ("evt_m1_001_02", "evt_m1_002_01") in ids
    assert len(ids) == 3  # 2 个场景内对 + 1 个边界对


def test_b3_drops_unknown_ids(tmp_path):
    movie, exts = _setup(tmp_path)
    pairs = build_causal_pairs(movie, exts)
    links = pass_b3_causal(ScriptedLLM(
        {"causal_links": [
            {"cause_id": "evt_m1_001_01", "effect_id": "evt_m1_001_02", "reason": "r"},
            {"cause_id": "evt_m1_999_01", "effect_id": "evt_m1_001_02", "reason": "x"}]}),
        pairs)
    assert len(links) == 1 and links[0].reason == "r"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_global_passes.py -v`
Expected: FAIL,`ModuleNotFoundError`

- [ ] **Step 3: 实现 global_passes.py**

```python
import json

from pydantic import BaseModel, Field

from movie_kg.models.extraction import SceneExtraction
from movie_kg.models.scene import MovieData
from movie_kg.prompts import load_prompt, render


class VtInfo(BaseModel):
    name: str
    scene_ids: list[int]


class OrderedVtRaw(BaseModel):
    name: str
    description: str = ""


class B1Output(BaseModel):
    ordered: list[OrderedVtRaw]


class OrderedVt(BaseModel):
    name: str
    timeline_index: int
    description: str


class NameContext(BaseModel):
    name: str
    aliases: list[str] = []
    context: str = ""


class MergeGroup(BaseModel):
    canonical: str
    aliases: list[str] = []
    description: str = ""


class AliasPlan(BaseModel):
    characters: list[MergeGroup] = []
    locations: list[MergeGroup] = []

    def resolve(self, kind: str, name: str) -> str:
        for g in getattr(self, kind):
            if name == g.canonical or name in g.aliases:
                return g.canonical
        return name


class MergeGroupRaw(BaseModel):
    canonical: str
    aliases: list[str] = []
    description: str = ""


class B2Output(BaseModel):
    characters: list[MergeGroupRaw] = []
    locations: list[MergeGroupRaw] = []


class EventPair(BaseModel):
    cause_id: str
    cause_topic: str
    effect_id: str
    effect_topic: str


class CausalLinkRaw(BaseModel):
    cause_id: str
    effect_id: str
    reason: str


class B3Output(BaseModel):
    causal_links: list[CausalLinkRaw]


class CausalLink(BaseModel):
    cause_id: str
    effect_id: str
    reason: str


def _text_model(llm) -> str:
    cfg = getattr(llm, "cfg", None)
    return cfg.text_model if cfg is not None else "text"


def collect_vts(movie: MovieData) -> list[VtInfo]:
    seen: dict[str, VtInfo] = {}
    for scene in movie.scenes:
        seq = int(scene.meta.scene_id.rsplit("_", 1)[1])
        name = scene.meta.virtual_time
        seen.setdefault(name, VtInfo(name=name, scene_ids=[])).scene_ids.append(seq)
    return list(seen.values())


def collect_name_contexts(movie: MovieData, extractions: list[SceneExtraction]):
    chars: dict[str, NameContext] = {}
    for scene in movie.scenes:
        for c in scene.meta.characters:
            ctx = chars.setdefault(c.name, NameContext(name=c.name,
                                                       aliases=list(c.aliases)))
            for a in c.aliases:
                if a not in ctx.aliases:
                    ctx.aliases.append(a)
    for ext in extractions:
        for ev in ext.events:
            for p in ev.participants:
                if p.name.startswith("未识别:"):
                    raw = p.name.split(":", 1)[1]
                    chars.setdefault(raw, NameContext(name=raw))
    locs = {s.meta.location: NameContext(name=s.meta.location,
                                         context=f"出现于 {s.meta.scene_id}")
            for s in movie.scenes}
    for ctx in chars.values():
        if not ctx.context:
            ctx.context = f"参与 {len(movie.scenes)} 个场景的剧情"
    return list(chars.values()), list(locs.values())


def pass_b1_order_vts(llm, vts: list[VtInfo]) -> list[OrderedVt]:
    vts_text = "\n".join(f"- {v.name}(出现于场景 {v.scene_ids})" for v in vts)
    system = render(load_prompt("vt_ordering"), VTS=vts_text)
    raw = llm.chat_json(model=_text_model(llm),
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": "请输出排序 JSON。"}],
                        schema=B1Output)
    ordered_names = [o.name for o in raw.ordered]
    known = {v.name for v in vts}
    ordered_names += [v.name for v in vts if v.name not in ordered_names]
    desc = {o.name: o.description for o in raw.ordered}
    out = []
    for i, name in enumerate(ordered_names):
        if name in known:
            out.append(OrderedVt(name=name, timeline_index=i,
                                 description=desc.get(name, "")))
    return out


def pass_b2_merge_aliases(llm, characters: list[NameContext],
                          locations: list[NameContext]) -> AliasPlan:
    def fmt(items):
        return "\n".join(f"- {i.name}(别名: {', '.join(i.aliases) or '无'}; {i.context})"
                         for i in items) or "(无)"
    system = render(load_prompt("alias_merge"),
                    CHARACTERS=fmt(characters), LOCATIONS=fmt(locations))
    raw = llm.chat_json(model=_text_model(llm),
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": "请输出归并 JSON。"}],
                        schema=B2Output)
    plan = AliasPlan(characters=[MergeGroup(**g.model_dump()) for g in raw.characters],
                     locations=[MergeGroup(**g.model_dump()) for g in raw.locations])
    for nc in characters:
        if nc.name not in {g.canonical for g in plan.characters} and not any(
                nc.name in g.aliases for g in plan.characters):
            plan.characters.append(MergeGroup(canonical=nc.name,
                                              aliases=nc.aliases, description=""))
    for nc in locations:
        if nc.name not in {g.canonical for g in plan.locations} and not any(
                nc.name in g.aliases for g in plan.locations):
            plan.locations.append(MergeGroup(canonical=nc.name, description=""))
    return plan


def _mains(ext: SceneExtraction) -> list:
    return [e for e in ext.events if not e.is_subevent]


def build_causal_pairs(movie: MovieData,
                       extractions: list[SceneExtraction]) -> list[EventPair]:
    by_scene = {ext.scene_id: _mains(ext) for ext in extractions}
    pairs: list[EventPair] = []
    scene_ids = [s.meta.scene_id for s in movie.scenes]
    for sid in scene_ids:
        mains = by_scene.get(sid, [])
        for a, b in zip(mains, mains[1:]):
            pairs.append(EventPair(cause_id=a.id, cause_topic=a.topic,
                                   effect_id=b.id, effect_topic=b.topic))
    for prev_sid, next_sid in zip(scene_ids, scene_ids[1:]):
        prev_mains, next_mains = by_scene.get(prev_sid, []), by_scene.get(next_sid, [])
        if prev_mains and next_mains:
            a, b = prev_mains[-1], next_mains[0]
            pairs.append(EventPair(cause_id=a.id, cause_topic=a.topic,
                                   effect_id=b.id, effect_topic=b.topic))
    return pairs


def pass_b3_causal(llm, pairs: list[EventPair]) -> list[CausalLink]:
    pairs_text = "\n".join(
        f"- {p.cause_id}({p.cause_topic}) -> {p.effect_id}({p.effect_topic})"
        for p in pairs)
    system = render(load_prompt("causal_detection"), PAIRS=pairs_text)
    raw = llm.chat_json(model=_text_model(llm),
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": "请输出因果 JSON。"}],
                        schema=B3Output)
    valid = {(p.cause_id, p.effect_id) for p in pairs}
    return [CausalLink(cause_id=l.cause_id, effect_id=l.effect_id, reason=l.reason)
            for l in raw.causal_links if (l.cause_id, l.effect_id) in valid]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_global_passes.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add config/prompts/vt_ordering.md config/prompts/alias_merge.md config/prompts/causal_detection.md src/movie_kg/extraction/global_passes.py tests/test_global_passes.py
git commit -m "feat: global passes B1-B3 (timeline ordering, alias merge, causal links)"
```

---

### Task 10: 图 schema(DDL)与写入器(C1,含 global_order)

**Files:**
- Create: `src/movie_kg/graph/__init__.py`、`src/movie_kg/graph/schema.py`、`src/movie_kg/graph/writer.py`、`tests/test_schema.py`、`tests/test_writer.py`、`tests/test_writer_integration.py`

**Interfaces:**
- Consumes: `SceneExtraction`、`MovieData`、`OrderedVt`/`AliasPlan`/`CausalLink`(Task 9)、`MockSession`
- Produces: `DDL_STATEMENTS: list[str]`(spec 第 9 章全部索引/约束/向量索引)、`apply_schema(session) -> None`;`GraphWriter(session, alias_plan: AliasPlan | None = None)`,方法:
  - `ensure_movie(movie_id: str, title: str)`
  - `ensure_scene(meta: SceneMeta)`
  - `write_extraction(ext: SceneExtraction, meta: SceneMeta)` — Event/Character/VT/Location 节点(MERGE on id)+ PARTICIPATES_IN / OCCURS_AT / LOCATED_AT / SCENE_OF / BELONGS_TO / SUBEVENT_OF 边;`global_order = seq * 1000 + order` 直接赋值;角色/地点名称先经 `alias_plan.resolve()`
  - `write_precedes_chain(extractions: list[SceneExtraction], metas: list[SceneMeta])` — 主事件链 + 跨场景边界边(`gap` = 后场景 `virtual_time`)
  - `write_relations(relations: list[ResolvedRelation])` — `is_new=true` 建边;`false` 先将旧边 `valid=false, end_event_id` 再建新边
  - `apply_vt_order(ordered: list[OrderedVt])`
  - `write_causal_links(links: list[CausalLink])`
- char/vt/loc 节点 ID:`char_{movie}_{md5(name)[:8]}` 等(spec 附录 A),由模块函数 `char_id(movie_id, name)` / `vt_id(...)` / `loc_id(...)` 计算,Task 14 绑定时复用。

- [ ] **Step 1: 写失败测试**

`tests/test_schema.py`:

```python
from movie_kg.graph.schema import DDL_STATEMENTS, apply_schema
from tests.fakes import MockSession


def test_ddl_statements_complete():
    joined = "\n".join(DDL_STATEMENTS)
    for fragment in ["CREATE INDEX event_id", "CREATE INDEX scene_id",
                     "CREATE CONSTRAINT event_unique", "CREATE CONSTRAINT scene_unique",
                     "CREATE VECTOR INDEX event_vec", "CREATE VECTOR INDEX location_vec",
                     "vector.dimensions", "1024", "cosine"]:
        assert fragment in joined
    # 每类实体都有 movie_id 索引
    for label in ["Event", "Character", "VirtualTime", "Location", "Scene"]:
        assert f"FOR (n:{label}) ON (n.movie_id)" in joined


def test_apply_schema_runs_all():
    session = MockSession()
    apply_schema(session)
    assert len(session.queries) == len(DDL_STATEMENTS)
```

`tests/test_writer.py`:

```python
from movie_kg.extraction.global_passes import AliasPlan, MergeGroup
from movie_kg.graph.writer import GraphWriter, char_id
from movie_kg.models.extraction import Participant, ResolvedRelation, SceneEvent
from movie_kg.models.scene import SceneMeta
from tests.fakes import MockSession

META = SceneMeta(scene_id="scene_001", movie_id="m1", start_ts="00:00:00",
                 end_ts="00:02:00",
                 characters=[{"name": "张三", "aliases": []}],
                 dialogues=[], location="码头仓库", virtual_time="第一天傍晚")
EVENTS = [
    SceneEvent(id="evt_m1_001_01", event_type="conflict", topic="对峙",
               description="张三对峙", scene_id="scene_001", movie_id="m1",
               order=1, participants=[
                   Participant(name="张三", emotion="愤怒",
                               role_description="发起者", items=["枪"])]),
    SceneEvent(id="evt_m1_001_02", event_type="action", topic="拔枪",
               description="张三拔枪", scene_id="scene_001", movie_id="m1",
               order=2, is_subevent=True, parent_id="evt_m1_001_01"),
]


def _writer(session, plan=None):
    w = GraphWriter(session, alias_plan=plan)
    w.ensure_movie("m1", "测试影片")
    w.ensure_scene(META)
    return w


def test_write_extraction_merges_event_with_global_order():
    s = MockSession()
    _writer(s).write_extraction(_ext(), META)
    q = "\n".join(query for query, _ in s.queries)
    assert "MERGE (e:Event {id: $id})" in q
    event_params = [p for query, p in s.queries
                    if "MERGE (e:Event" in query][0]
    assert event_params["id"] == "evt_m1_001_01"
    assert event_params["global_order"] == 1001  # seq*1000 + order
    assert event_params["movie_id"] == "m1"
    assert "PARTICIPATES_IN" in q and "SCENE_OF" in q
    assert "OCCURS_AT" in q and "LOCATED_AT" in q and "BELONGS_TO" in q
    assert "SUBEVENT_OF" in q


def _ext():
    from movie_kg.models.extraction import SceneExtraction
    return SceneExtraction(scene_id="scene_001", movie_id="m1", status="ok",
                           events=EVENTS)


def test_alias_plan_resolves_names():
    s = MockSession()
    plan = AliasPlan(characters=[MergeGroup(canonical="张三", aliases=["张老板"],
                                             description="码头头目")])
    _writer(s, plan).write_extraction(_ext(), META)
    cid = char_id("m1", "张三")
    char_params = [p for query, p in s.queries if "MERGE (c:Character" in query]
    assert char_params and any(p.get("id") == cid for p in char_params)


def test_precedes_chain_with_boundary_gap():
    s = MockSession()
    meta2 = META.model_copy(update={"scene_id": "scene_002", "start_ts": "00:02:00",
                                    "end_ts": "00:04:00", "virtual_time": "第二天清晨"})
    from movie_kg.models.extraction import SceneExtraction
    ext1 = _ext()
    ext2 = SceneExtraction(scene_id="scene_002", movie_id="m1", status="ok", events=[
        SceneEvent(id="evt_m1_002_01", event_type="dialogue", topic="问话",
                   description="问话", scene_id="scene_002", movie_id="m1", order=1)])
    _writer(s).write_precedes_chain([ext1, ext2], [META, meta2])
    precedes = [(q, p) for q, p in s.queries if "PRECEDES" in q]
    ids = [p for q, p in precedes if p.get("gap") is not None]
    # 场景内 gap=None;边界边 gap=第二天清晨
    boundary = [p for q, p in precedes if p.get("gap") == "第二天清晨"]
    assert boundary and boundary[0]["a"] == "evt_m1_001_01"
    assert boundary[0]["b"] == "evt_m1_002_01"


def test_relation_change_invalidates_old_edge():
    s = MockSession()
    w = _writer(s)
    rel_new = ResolvedRelation(char_a="张三", char_b="李四", relation_type="friend",
                               is_new=True, start_event_id="evt_m1_001_01")
    w.write_relations([rel_new], "m1")
    assert any("MERGE (a)-[rel:RELATED_TO" in q for q, _ in s.queries)
    s2_queries_before = len(s.queries)
    rel_change = ResolvedRelation(char_a="张三", char_b="李四", relation_type="enemy",
                                  is_new=False, change_description="决裂",
                                  start_event_id="evt_m1_001_02")
    w.write_relations([rel_change], "m1")
    after = s.queries[s2_queries_before:]
    invalidate = [q for q, _ in after if "valid = false" in q or "valid=false" in q]
    assert invalidate, "变更关系必须先失效旧边"
    assert any("end_event_id" in q for q, _ in after)


def test_apply_vt_order_sets_index_and_description():
    s = MockSession()
    from movie_kg.extraction.global_passes import OrderedVt
    _writer(s).apply_vt_order([OrderedVt(name="第一天傍晚", timeline_index=0,
                                         description="开始之夜")])
    q, p = s.queries[-1]
    assert "timeline_index" in q and "description" in q
    assert p["idx"] == 0 and p["desc"] == "开始之夜"


def test_causal_links_written():
    s = MockSession()
    from movie_kg.extraction.global_passes import CausalLink
    _writer(s).write_causal_links(
        [CausalLink(cause_id="evt_m1_001_01", effect_id="evt_m1_001_02", reason="r")])
    assert any("CAUSES" in q for q, _ in s.queries)
```

`tests/test_writer_integration.py`:

```python
import os

import pytest

from movie_kg.extraction.global_passes import OrderedVt
from movie_kg.graph.schema import apply_schema
from movie_kg.graph.writer import GraphWriter
from tests.test_writer import EVENTS, META, _ext

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("NEO4J_URI"),
                       reason="需要 NEO4J_URI / NEO4J_PASSWORD"),
]


@pytest.fixture
def session():
    from neo4j import GraphDatabase
    cfg_uri = os.environ["NEO4J_URI"]
    driver = GraphDatabase.driver(cfg_uri, auth=(os.environ.get("NEO4J_USER", "neo4j"),
                                                 os.environ.get("NEO4J_PASSWORD", "")))
    s = driver.session()
    s.run("MATCH (n {movie_id: 'm1'}) DETACH DELETE n")
    apply_schema(s)
    yield s
    s.run("MATCH (n {movie_id: 'm1'}) DETACH DELETE n")
    s.close()
    driver.close()


def test_roundtrip_write_and_read(session):
    w = GraphWriter(session)
    w.ensure_movie("m1", "测试影片")
    w.ensure_scene(META)
    w.write_extraction(_ext(), META)
    w.apply_vt_order([OrderedVt(name="第一天傍晚", timeline_index=0, description="d")])
    rec = session.run("MATCH (e:Event {id: 'evt_m1_001_01'}) RETURN e").single()
    assert rec is not None and rec["e"]["global_order"] == 1001
    rec = session.run(
        "MATCH (c:Character)-[r:PARTICIPATES_IN]->(:Event {id: 'evt_m1_001_01'}) "
        "RETURN c.name AS n, r.emotion AS emo").single()
    assert rec["n"] == "张三" and rec["emo"] == "愤怒"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_schema.py tests/test_writer.py -v`
Expected: FAIL,`ModuleNotFoundError`(movie_kg.graph)

- [ ] **Step 3: 实现 schema.py 与 writer.py**

`src/movie_kg/graph/schema.py`:

```python
DDL_STATEMENTS = [
    "CREATE INDEX event_id IF NOT EXISTS FOR (e:Event) ON (e.id)",
    "CREATE INDEX character_id IF NOT EXISTS FOR (c:Character) ON (c.id)",
    "CREATE INDEX character_name IF NOT EXISTS FOR (c:Character) ON (c.name)",
    "CREATE INDEX location_id IF NOT EXISTS FOR (l:Location) ON (l.id)",
    "CREATE INDEX virtualtime_id IF NOT EXISTS FOR (vt:VirtualTime) ON (vt.id)",
    "CREATE INDEX scene_id IF NOT EXISTS FOR (s:Scene) ON (s.id)",
    "CREATE INDEX event_scene_id IF NOT EXISTS FOR (e:Event) ON (e.scene_id)",
    "CREATE INDEX event_movie_id IF NOT EXISTS FOR (n:Event) ON (n.movie_id)",
    "CREATE INDEX character_movie_id IF NOT EXISTS FOR (n:Character) ON (n.movie_id)",
    "CREATE INDEX virtualtime_movie_id IF NOT EXISTS FOR (n:VirtualTime) ON (n.movie_id)",
    "CREATE INDEX location_movie_id IF NOT EXISTS FOR (n:Location) ON (n.movie_id)",
    "CREATE INDEX scene_movie_id IF NOT EXISTS FOR (n:Scene) ON (n.movie_id)",
    "CREATE CONSTRAINT event_unique IF NOT EXISTS FOR (e:Event) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT char_unique IF NOT EXISTS FOR (c:Character) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT loc_unique IF NOT EXISTS FOR (l:Location) REQUIRE l.id IS UNIQUE",
    "CREATE CONSTRAINT vt_unique IF NOT EXISTS FOR (vt:VirtualTime) REQUIRE vt.id IS UNIQUE",
    "CREATE CONSTRAINT scene_unique IF NOT EXISTS FOR (s:Scene) REQUIRE s.id IS UNIQUE",
    "CREATE VECTOR INDEX event_vec IF NOT EXISTS FOR (e:Event) ON (e.embedding) "
    "OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity`: 'cosine'}}",
    "CREATE VECTOR INDEX character_vec IF NOT EXISTS FOR (c:Character) ON (c.embedding) "
    "OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity`: 'cosine'}}",
    "CREATE VECTOR INDEX virtualtime_vec IF NOT EXISTS FOR (vt:VirtualTime) ON (vt.embedding) "
    "OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity`: 'cosine'}}",
    "CREATE VECTOR INDEX location_vec IF NOT EXISTS FOR (l:Location) ON (l.embedding) "
    "OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity`: 'cosine'}}",
]


def apply_schema(session) -> None:
    for stmt in DDL_STATEMENTS:
        session.run(stmt)
```

`src/movie_kg/graph/writer.py`:

```python
import hashlib

from movie_kg.extraction.global_passes import AliasPlan, CausalLink, OrderedVt
from movie_kg.models.extraction import ResolvedRelation, SceneExtraction
from movie_kg.models.scene import SceneMeta


def _md5(name: str) -> str:
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:8]


def char_id(movie_id: str, name: str) -> str:
    return f"char_{movie_id}_{_md5(name)}"


def vt_id(movie_id: str, name: str) -> str:
    return f"vt_{movie_id}_{_md5(name)}"


def loc_id(movie_id: str, name: str) -> str:
    return f"loc_{movie_id}_{_md5(name)}"


class GraphWriter:
    """C1: 按 checkpoint 顺序幂等写图(MERGE on id)。"""

    def __init__(self, session, alias_plan: AliasPlan | None = None):
        self.session = session
        self.plan = alias_plan

    def _canon(self, kind: str, name: str) -> str:
        return self.plan.resolve(kind, name) if self.plan else name

    def ensure_movie(self, movie_id: str, title: str) -> None:
        self.session.run(
            "MERGE (m:Movie {id: $id}) SET m.title = $title", id=movie_id, title=title)

    def ensure_scene(self, meta: SceneMeta) -> None:
        seq = int(meta.scene_id.rsplit("_", 1)[1])
        self.session.run(
            "MERGE (s:Scene {id: $id}) SET s.seq = $seq, s.start_ts = $start, "
            "s.end_ts = $end, s.annotated_location = $loc, s.annotated_time = $vt, "
            "s.movie_id = $movie",
            id=meta.scene_id, seq=seq, start=meta.start_ts, end=meta.end_ts,
            loc=meta.location, vt=meta.virtual_time, movie=meta.movie_id)
        self.session.run(
            "MATCH (s:Scene {id: $sid}), (m:Movie {id: $mid}) "
            "MERGE (s)-[:BELONGS_TO]->(m)", sid=meta.scene_id, mid=meta.movie_id)

    def write_extraction(self, ext: SceneExtraction, meta: SceneMeta) -> None:
        seq = int(meta.scene_id.rsplit("_", 1)[1])
        vt = vt_id(meta.movie_id, meta.virtual_time)
        loc = loc_id(meta.movie_id, self._canon("location", meta.location))
        self.session.run(
            "MERGE (v:VirtualTime {id: $id}) ON CREATE SET v.name = $name, "
            "v.description = '', v.movie_id = $movie", id=vt, name=meta.virtual_time,
            movie=meta.movie_id)
        self.session.run(
            "MERGE (l:Location {id: $id}) ON CREATE SET l.name = $name, "
            "l.aliases = [], l.description = '', l.movie_id = $movie", id=loc,
            name=self._canon("location", meta.location), movie=meta.movie_id)
        for ev in ext.events:
            go = seq * 1000 + ev.order
            self.session.run(
                "MERGE (e:Event {id: $id}) SET e.event_type = $type, e.topic = $topic, "
                "e.description = $desc, e.scene_id = $sid, e.order = $order, "
                "e.global_order = $go, e.is_subevent = $sub, e.movie_id = $movie",
                id=ev.id, type=ev.event_type, topic=ev.topic, desc=ev.description,
                sid=ev.scene_id, order=ev.order, go=go, sub=ev.is_subevent,
                movie=ev.movie_id)
            self.session.run(
                "MATCH (e:Event {id: $eid}), (s:Scene {id: $sid}) "
                "MERGE (e)-[:SCENE_OF]->(s)", eid=ev.id, sid=ev.scene_id)
            self.session.run(
                "MATCH (e:Event {id: $eid}), (m:Movie {id: $mid}) "
                "MERGE (e)-[:BELONGS_TO]->(m)", eid=ev.id, mid=ev.movie_id)
            self.session.run(
                "MATCH (e:Event {id: $eid}), (v:VirtualTime {id: $vid}) "
                "MERGE (e)-[:OCCURS_AT]->(v)", eid=ev.id, vid=vt)
            self.session.run(
                "MATCH (e:Event {id: $eid}), (l:Location {id: $lid}) "
                "MERGE (e)-[:LOCATED_AT]->(l)", eid=ev.id, lid=loc)
            if ev.parent_id:
                self.session.run(
                    "MATCH (s:Event {id: $sid}), (p:Event {id: $pid}) "
                    "MERGE (s)-[:SUBEVENT_OF]->(p)", sid=ev.id, pid=ev.parent_id)
            for p in ev.participants:
                cname = self._canon("character", p.name.split(":", 1)[-1]
                                    if p.name.startswith("未识别:") else p.name)
                cid = char_id(meta.movie_id, cname)
                self.session.run(
                    "MERGE (c:Character {id: $id}) ON CREATE SET c.name = $name, "
                    "c.aliases = [], c.description = '', c.movie_id = $movie",
                    id=cid, name=cname, movie=meta.movie_id)
                self.session.run(
                    "MATCH (c:Character {id: $cid}), (m:Movie {id: $mid}) "
                    "MERGE (c)-[:BELONGS_TO]->(m)", cid=cid, mid=meta.movie_id)
                self.session.run(
                    "MATCH (c:Character {id: $cid}), (e:Event {id: $eid}) "
                    "MERGE (c)-[r:PARTICIPATES_IN]->(e) "
                    "SET r.emotion = $emo, r.role_description = $role, r.items = $items",
                    cid=cid, eid=ev.id, emo=p.emotion, role=p.role_description,
                    items=p.items)

    def write_precedes_chain(self, extractions: list[SceneExtraction],
                             metas: list[SceneMeta]) -> None:
        by_scene = {ext.scene_id: [e for e in ext.events if not e.is_subevent]
                    for ext in extractions}
        vt_by_scene = {m.scene_id: m.virtual_time for m in metas}
        ordered_ids = [m.scene_id for m in metas]
        edges: list[tuple[str, str, str | None]] = []
        for sid in ordered_ids:
            mains = by_scene.get(sid, [])
            for a, b in zip(mains, mains[1:]):
                edges.append((a.id, b.id, None))
        for prev, nxt in zip(ordered_ids, ordered_ids[1:]):
            pm, nm = by_scene.get(prev, []), by_scene.get(nxt, [])
            if pm and nm:
                edges.append((pm[-1].id, nm[0].id, vt_by_scene.get(nxt)))
        for a, b, gap in edges:
            self.session.run(
                "MATCH (a:Event {id: $a}), (b:Event {id: $b}) "
                "MERGE (a)-[r:PRECEDES]->(b) SET r.gap = $gap", a=a, b=b, gap=gap)

    def write_relations(self, relations: list[ResolvedRelation], movie_id: str) -> None:
        for r in relations:
            a = char_id(movie_id, self._canon("character", r.char_a))
            b = char_id(movie_id, self._canon("character", r.char_b))
            if not r.is_new:
                self.session.run(
                    "MATCH (a:Character {id: $a})-[old:RELATED_TO]-(b:Character {id: $b}) "
                    "WHERE old.valid = true "
                    "SET old.valid = false, old.end_event_id = $eid",
                    a=a, b=b, eid=r.start_event_id)
            self.session.run(
                "MATCH (a:Character {id: $a}), (b:Character {id: $b}) "
                "MERGE (a)-[rel:RELATED_TO {start_event_id: $sid}]->(b) "
                "SET rel.relation_type = $rtype, rel.valid = true",
                a=a, b=b, sid=r.start_event_id, rtype=r.relation_type)

    def apply_vt_order(self, ordered: list[OrderedVt]) -> None:
        for o in ordered:
            self.session.run(
                "MATCH (vt:VirtualTime {name: $name}) SET vt.timeline_index = $idx, "
                "vt.description = $desc", name=o.name, idx=o.timeline_index,
                desc=o.description)

    def write_causal_links(self, links: list[CausalLink]) -> None:
        for l in links:
            self.session.run(
                "MATCH (c:Event {id: $cid}), (e:Event {id: $eid}) "
                "MERGE (c)-[r:CAUSES]->(e) SET r.reason = $reason",
                cid=l.cause_id, eid=l.effect_id, reason=l.reason)
```

同步说明: `write_relations` 需要 `movie_id` 参数(`ResolvedRelation` 不携带),Task 11 的 CLI 调用已按 `writer.write_relations(ext.relations, data.movie_id)` 编写,与测试一致。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_schema.py tests/test_writer.py -v`
Expected: 7 passed
(可选,需本地 Neo4j:`NEO4J_URI=bolt://localhost:7687 NEO4J_PASSWORD=... python -m pytest tests/test_writer_integration.py -v`)

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/graph/ tests/test_schema.py tests/test_writer.py tests/test_writer_integration.py
git commit -m "feat: Neo4j schema DDL and idempotent graph writer"
```

---

### Task 11: 向量化(C2)与 `kg build` 全流程命令

**Files:**
- Create: `src/movie_kg/embeddings/__init__.py`、`src/movie_kg/embeddings/embedder.py`、`src/movie_kg/cli.py`、`tests/test_embedder.py`、`tests/test_cli_build.py`

**Interfaces:**
- Consumes: `run_phase_a`、B1-B3 pass、`GraphWriter`、`apply_schema`、`CheckpointStore`、`load_movie`、`ScriptedLLM`、`MockDriver`
- Produces: `describe_event(topic: str, description: str, event_type: str) -> str`、`describe_entity(name: str, description: str, kind: str) -> str`、`embed_all(session, llm, movie_id: str, batch_size: int = 32) -> int`(查询缺 embedding 的 4 类实体 → 组描述文本 → `llm.embed` → `SET n.embedding`);`src/movie_kg/cli.py` 的 `main()`、`build(movie: str, from_scratch: bool = False, scenes: str = None)` 命令(typer),内部工厂 `_make_llm(cfg)` / `_make_driver(cfg)`(测试 monkeypatch 点)。Task 15/16/17 复用 CLI 工厂。

- [ ] **Step 1: 写失败测试**

`tests/test_embedder.py`:

```python
from movie_kg.embeddings.embedder import describe_event, embed_all
from tests.fakes import MockSession, ScriptedLLM

NODES = [{"id": "evt_m1_001_01", "topic": "对峙", "description": "张三对峙",
          "event_type": "conflict", "labels": ["Event"]}]


def test_describe_event_concat():
    text = describe_event(topic="对峙", description="张三对峙", event_type="conflict")
    assert "对峙" in text and "张三对峙" in text and "conflict" in text


def test_embed_all_batches_and_sets():
    session = MockSession(results=[  # 第一次查询返回缺嵌入的节点
        [{"node": n} for n in NODES]])
    llm = ScriptedLLM()
    count = embed_all(session, llm, "m1")
    assert count == 1
    set_queries = [q for q, _ in session.queries if "embedding" in q and "SET" in q]
    assert set_queries, "必须写回 embedding 属性"
    params = [p for q, p in session.queries if "SET" in q and "embedding" in q][0]
    assert params["id"] == "evt_m1_001_01"
    assert isinstance(params["embedding"], list) and len(params["embedding"]) == 8
```

`tests/test_cli_build.py`:

```python
from typer.testing import CliRunner

import movie_kg.cli as cli_mod
from movie_kg.cli import main
from tests.fakes import MockDriver, MockSession, ScriptedLLM
from tests.fixtures import make_movie_dir

A1 = {"events": [{"event_type": "action", "topic": "t", "description": "d",
                  "participating_characters": [{"name": "张三", "emotion": None,
                                                "role_description": None, "items": []}],
                  "subevents": []}]}
A2 = {"character_relations": []}
B1 = {"ordered": [{"name": "第一天傍晚", "description": "d1"},
                  {"name": "第二天清晨", "description": "d2"}]}
B2 = {"characters": [{"canonical": "张三", "aliases": ["老张"], "description": "头目"}],
      "locations": [{"canonical": "码头仓库", "aliases": [], "description": "d"},
                    {"canonical": "警局办公室", "aliases": [], "description": "d"}]}
B3 = {"causal_links": []}

runner = CliRunner()


def test_build_end_to_end(tmp_path, monkeypatch):
    d = make_movie_dir(tmp_path)
    monkeypatch.chdir(tmp_path)
    session = MockSession()
    llm = ScriptedLLM(A1, A2, A1, A2, B1, B2, B3)  # A×2场景×2 + B1/B2/B3
    monkeypatch.setattr(cli_mod, "_make_llm", lambda cfg: llm)
    monkeypatch.setattr(cli_mod, "_make_driver", lambda cfg: MockDriver(session))
    result = runner.invoke(main, ["build", "--movie", "m1"])
    assert result.exit_code == 0, result.output
    # checkpoint 已落盘
    assert (d / "checkpoints" / "scene_001.json").exists()
    # 图写入发生了
    all_queries = "\n".join(q for q, _ in session.queries)
    for frag in ["MERGE (e:Event", "PARTICIPATES_IN", "PRECEDES", "RELATED_TO",
                 "timeline_index", "BELONGS_TO"]:
        assert frag in all_queries
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_embedder.py tests/test_cli_build.py -v`
Expected: FAIL,`ModuleNotFoundError`(embeddings / cli)

- [ ] **Step 3: 实现 embedder.py 与 cli.py**

`src/movie_kg/embeddings/embedder.py`:

```python
DESCRIBE_QUERY = (
    "MATCH (n) WHERE (n:Event OR n:Character OR n:VirtualTime OR n:Location) "
    "AND n.movie_id = $movie AND n.embedding IS NULL RETURN n")


def describe_event(topic: str, description: str, event_type: str) -> str:
    return f"[{event_type}] {topic}:{description}"


def describe_entity(name: str, description: str, kind: str) -> str:
    return f"{kind} {name}:{description}"


def _node_text(node: dict) -> str:
    if "topic" in node:
        return describe_event(node.get("topic", ""), node.get("description", ""),
                              node.get("event_type", ""))
    kind = "虚拟时间" if "timeline_index" in node else "实体"
    return describe_entity(node.get("name", ""), node.get("description", ""), kind)


def embed_all(session, llm, movie_id: str, batch_size: int = 32) -> int:
    """C2: 为缺 embedding 的实体生成向量并写回,返回处理数。"""
    nodes = [rec["node"] for rec in session.run(DESCRIBE_QUERY, movie=movie_id)]
    for i in range(0, len(nodes), batch_size):
        batch = nodes[i:i + batch_size]
        vectors = llm.embed([_node_text(dict(n)) for n in batch])
        for node, vec in zip(batch, vectors):
            session.run("MATCH (n {id: $id}) SET n.embedding = $embedding",
                        id=node["id"], embedding=vec)
    return len(nodes)
```

`src/movie_kg/cli.py`:

```python
import typer

from movie_kg.config import load_models_config, load_neo4j_config

app = typer.Typer(help="movie-plot-kg: 剧情知识图谱构建 / 问答 / 评估")
eval_app = typer.Typer(help="评估")
app.add_typer(eval_app, name="eval")


def _make_llm(cfg):
    from movie_kg.llm import LLMClient
    return LLMClient(cfg)


def _make_driver(cfg):
    from neo4j import GraphDatabase
    return GraphDatabase.driver(cfg.uri, auth=(cfg.user, cfg.password))


@app.command()
def build(movie: str, from_scratch: bool = typer.Option(False, "--from-scratch"),
          scenes: str = typer.Option(None, "--scenes", help="如 1-5,成本校准试跑")):
    """Phase A(抽取+checkpoint)-> B(全局 pass)-> C(写图+向量化)。"""
    from pathlib import Path
    from movie_kg.embeddings.embedder import embed_all
    from movie_kg.extraction.checkpoints import CheckpointStore
    from movie_kg.extraction.global_passes import (
        build_causal_pairs, collect_name_contexts, collect_vts,
        pass_b1_order_vts, pass_b2_merge_aliases, pass_b3_causal)
    from movie_kg.extraction.pipeline import run_phase_a
    from movie_kg.graph.schema import apply_schema
    from movie_kg.graph.writer import GraphWriter
    from movie_kg.ingestion.data_loader import load_movie

    movie_dir = Path("data/movies") / movie
    data = load_movie(movie_dir)
    store = CheckpointStore(movie_dir)
    llm = _make_llm(load_models_config())
    scene_range = None
    if scenes:
        lo, hi = scenes.split("-")
        scene_range = (int(lo), int(hi))
    a = run_phase_a(llm, data, store, scene_range=scene_range,
                    from_scratch=from_scratch)
    typer.echo(f"Phase A: 完成 {len(a.completed)}, 失败 {len(a.failed)}, "
               f"跳过 {a.skipped}, tokens={a.usage}")
    if a.failed:
        typer.echo(f"失败场景: {a.failed}")
    extractions = [e for e in (store.load(s.meta.scene_id) for s in data.scenes)
                   if e is not None]
    if not extractions:
        typer.echo("无可用抽取结果,退出")
        raise typer.Exit(1)
    typer.echo("Phase B: 时间线 / 别名 / 因果 ...")
    ordered_vts = pass_b1_order_vts(llm, collect_vts(data))
    chars, locs = collect_name_contexts(data, extractions)
    alias_plan = pass_b2_merge_aliases(llm, chars, locs)
    causal = pass_b3_causal(llm, build_causal_pairs(data, extractions))
    typer.echo("Phase C: 写图 + 向量化 ...")
    driver = _make_driver(load_neo4j_config())
    with driver.session() as session:
        apply_schema(session)
        writer = GraphWriter(session, alias_plan=alias_plan)
        writer.ensure_movie(data.movie_id, data.title)
        for scene in data.scenes:
            ext = store.load(scene.meta.scene_id)
            if ext is None:
                continue
            writer.ensure_scene(scene.meta)
            writer.write_extraction(ext, scene.meta)
        writer.write_precedes_chain(extractions, [s.meta for s in data.scenes])
        for ext in extractions:
            writer.write_relations(ext.relations, data.movie_id)
        writer.apply_vt_order(ordered_vts)
        writer.write_causal_links(causal)
        n = embed_all(session, llm, data.movie_id)
    driver.close()
    typer.echo(f"完成: 嵌入 {n} 个实体。图谱就绪。")


def main():
    app()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_embedder.py tests/test_cli_build.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/embeddings/ src/movie_kg/cli.py tests/test_embedder.py tests/test_cli_build.py
git commit -m "feat: entity embedding and full kg build command (Phase A-B-C)"
```

---

### Task 12: 图查询模板库(7 个,参数校验防注入)

**Files:**
- Create: `src/movie_kg/graph/query_templates.py`、`tests/test_query_templates.py`

**Interfaces:**
- Consumes: `MockSession`
- Produces: `TemplateParamError(Exception)`、`TEMPLATES: dict[str, TemplateSpec]`、`TemplateSpec(template_id: str, required: dict[str, Callable], build: Callable[[dict], tuple[str, dict]])`、`validate_params(spec: TemplateSpec, params: dict) -> dict`、`execute_template(session, template_id: str, params: dict) -> list[dict]`。参数校验器:`_valid_id(v)`(正则 `^[a-z]+(?:_[A-Za-z0-9]+)+$`)、`_valid_depth(v)`(int 且 1-5)、`_valid_text(v)`(str,≤32 字符,不含 `' " \` ;`)。Task 14 消费。

- [ ] **Step 1: 写失败测试**

`tests/test_query_templates.py`:

```python
import pytest

from movie_kg.graph.query_templates import (
    TEMPLATES, TemplateParamError, execute_template, validate_params)
from tests.fakes import MockSession


def test_seven_templates_registered():
    assert set(TEMPLATES) == {
        "relation_between_chars", "events_after", "causes_of", "events_of_char",
        "events_at_place", "events_at_time", "subevents_of"}


def test_build_relation_query():
    cypher, params = validate_params_and_build(
        "relation_between_chars", {"a": "char_m1_a1b2c3d4", "b": "char_m1_e5f67890"})
    assert "$a" in cypher and "RELATED_TO*1..2" in cypher and "x.valid" in cypher
    assert params["a"] == "char_m1_a1b2c3d4"


def validate_params_and_build(tid, params):
    spec = TEMPLATES[tid]
    validated = validate_params(spec, params)
    return spec.build(validated)


def test_events_after_interpolates_depth():
    cypher, _ = validate_params_and_build(
        "events_after", {"id": "evt_m1_001_01", "depth": 3})
    assert "PRECEDES*1..3" in cypher and "$id" in cypher


@pytest.mark.parametrize("bad", [
    {"a": "Zhang'} RETURN n //", "b": "char_m1_e5f67890"},          # 注入
    {"a": "char_m1_a1b2c3d4"},                                      # 缺参数
    {"a": "no_underscore", "b": "char_m1_e5f67890"},                # 不合 ID 正则
])
def test_bad_ids_rejected(bad):
    with pytest.raises(TemplateParamError):
        validate_params_and_build("relation_between_chars", bad)


@pytest.mark.parametrize("bad_depth", ["3 OR 1=1", 6, 0, 2.5, None])
def test_bad_depth_rejected(bad_depth):
    with pytest.raises(TemplateParamError):
        validate_params_and_build("events_after",
                                  {"id": "evt_m1_001_01", "depth": bad_depth})


def test_unknown_template_raises():
    with pytest.raises(TemplateParamError):
        execute_template(MockSession(), "drop_database", {})


def test_execute_returns_records():
    session = MockSession(results=[[{"e": {"id": "evt_m1_001_02"}}]])
    rows = execute_template(session, "events_after",
                            {"id": "evt_m1_001_01", "depth": 3})
    assert rows[0]["e"]["id"] == "evt_m1_001_02"


def test_emotion_text_validated():
    cypher, params = validate_params_and_build(
        "events_of_char", {"id": "char_m1_a1b2c3d4", "emotion": "愤怒"})
    assert "$emotion" in cypher and params["emotion"] == "愤怒"
    with pytest.raises(TemplateParamError):
        validate_params_and_build(
            "events_of_char", {"id": "char_m1_a1b2c3d4",
                               "emotion": "x' RETURN //"})
    # emotion 可为 null(不过滤)
    _, p = validate_params_and_build(
        "events_of_char", {"id": "char_m1_a1b2c3d4", "emotion": None})
    assert p["emotion"] is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_query_templates.py -v`
Expected: FAIL,`ModuleNotFoundError`

- [ ] **Step 3: 实现 query_templates.py**

```python
import re
from dataclasses import dataclass, field
from typing import Any, Callable

ID_RE = re.compile(r"^[a-z]+(?:_[A-Za-z0-9]+)+$")


class TemplateParamError(Exception):
    pass


def _valid_id(v: str) -> str:
    if not isinstance(v, str) or not ID_RE.match(v):
        raise TemplateParamError(f"非法实体 ID: {v!r}")
    return v


def _valid_depth(v) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not (1 <= v <= 5):
        raise TemplateParamError(f"深度必须是 1-5 的整数: {v!r}")
    return v


def _valid_text(v) -> str:
    if (not isinstance(v, str) or not v or len(v) > 32
            or any(ch in v for ch in "'\"`;")):
        raise TemplateParamError(f"非法文本参数: {v!r}")
    return v


def _valid_optional_text(v):
    return None if v is None else _valid_text(v)


@dataclass
class TemplateSpec:
    template_id: str
    required: dict[str, Callable[[Any], Any]] = field(default_factory=dict)
    build: Callable[[dict], tuple[str, dict]] = None


def _relation_between_chars(p: dict) -> tuple[str, dict]:
    return ("MATCH (a:Character)-[r:RELATED_TO*1..2]-(b:Character) "
            "WHERE a.id = $a AND b.id = $b AND all(x IN r WHERE x.valid) "
            "RETURN a, r, b", {"a": p["a"], "b": p["b"]})


def _events_after(p: dict) -> tuple[str, dict]:
    return (f"MATCH (e1:Event)-[:PRECEDES*1..{p['depth']}]->(e2:Event) "
            "WHERE e1.id = $id RETURN e2 ORDER BY e2.global_order",
            {"id": p["id"]})


def _causes_of(p: dict) -> tuple[str, dict]:
    return (f"MATCH path=(c:Event)-[:CAUSES*1..{p['depth']}]->(e:Event) "
            "WHERE e.id = $id RETURN path", {"id": p["id"]})


def _events_of_char(p: dict) -> tuple[str, dict]:
    return ("MATCH (c:Character)-[r:PARTICIPATES_IN]->(e:Event) "
            "WHERE c.id = $id AND ($emotion IS NULL OR r.emotion = $emotion) "
            "RETURN e, r ORDER BY e.global_order",
            {"id": p["id"], "emotion": p.get("emotion")})


def _events_at_place(p: dict) -> tuple[str, dict]:
    return ("MATCH (e:Event)-[:LOCATED_AT]->(l:Location) WHERE l.id = $id "
            "RETURN e ORDER BY e.global_order", {"id": p["id"]})


def _events_at_time(p: dict) -> tuple[str, dict]:
    return ("MATCH (e:Event)-[:OCCURS_AT]->(vt:VirtualTime) WHERE vt.id = $id "
            "RETURN e ORDER BY e.global_order", {"id": p["id"]})


def _subevents_of(p: dict) -> tuple[str, dict]:
    return ("MATCH (s:Event)-[:SUBEVENT_OF]->(p:Event) WHERE p.id = $id "
            "RETURN s", {"id": p["id"]})


TEMPLATES: dict[str, TemplateSpec] = {
    "relation_between_chars": TemplateSpec(
        "relation_between_chars", {"a": _valid_id, "b": _valid_id},
        _relation_between_chars),
    "events_after": TemplateSpec(
        "events_after", {"id": _valid_id, "depth": _valid_depth}, _events_after),
    "causes_of": TemplateSpec(
        "causes_of", {"id": _valid_id, "depth": _valid_depth}, _causes_of),
    "events_of_char": TemplateSpec(
        "events_of_char", {"id": _valid_id, "emotion": _valid_optional_text},
        _events_of_char),
    "events_at_place": TemplateSpec(
        "events_at_place", {"id": _valid_id}, _events_at_place),
    "events_at_time": TemplateSpec(
        "events_at_time", {"id": _valid_id}, _events_at_time),
    "subevents_of": TemplateSpec(
        "subevents_of", {"id": _valid_id}, _subevents_of),
}


def validate_params(spec: TemplateSpec, params: dict) -> dict:
    missing = set(spec.required) - set(params)
    if missing:
        raise TemplateParamError(f"{spec.template_id} 缺少参数: {sorted(missing)}")
    return {k: validator(params[k]) for k, validator in spec.required.items()}


def execute_template(session, template_id: str, params: dict) -> list[dict]:
    spec = TEMPLATES.get(template_id)
    if spec is None:
        raise TemplateParamError(f"未知模板: {template_id}")
    validated = validate_params(spec, params)
    cypher, out_params = spec.build(validated)
    return session.run(cypher, **out_params)
```


- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_query_templates.py -v`
Expected: 全部 passed(含参数化注入用例)

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/graph/query_templates.py tests/test_query_templates.py
git commit -m "feat: seven parameterized graph query templates with injection-safe validation"
```

---

### Task 13: QA 检索规划器(LLM-1)

**Files:**
- Create: `config/prompts/qa_planning.md`、`src/movie_kg/qa/__init__.py`、`src/movie_kg/qa/planner.py`、`tests/test_planner.py`

**Interfaces:**
- Consumes: `ScriptedLLM`、`TEMPLATES`(Task 12,校验 template_id 合法性)
- Produces: `TargetEntity(type: Literal["Character","Event","VirtualTime","Location"], name_hint: str)`、`GraphQueryPlan(template_id: str, params: dict)`、`RetrievalPlan(intent: Literal["factual","causal","relational","temporal","synthesis"], rewritten_query: str, target_entities: list[TargetEntity], graph_queries: list[GraphQueryPlan])`、`plan_retrieval(llm, question: str) -> RetrievalPlan`(丢弃未知 template_id 与超出的第 3+ 个 graph_queries,不影响其余输出)。Task 14/17 消费。

- [ ] **Step 1: 写 Prompt 与失败测试**

`config/prompts/qa_planning.md`:

```text
你是剧情知识图谱的检索规划器。根据用户问题输出检索计划。

可用图查询模板(参数中需要实体 ID 的字段填名称提示,与 target_entities 的 name_hint 保持一致):
- relation_between_chars(a, b): 两人关系
- events_after(id, depth): 某事件之后的事件
- causes_of(id, depth): 导致某事件的因果链
- events_of_char(id, emotion): 某角色的事件(可按情绪过滤,emotion 可为 null)
- events_at_place(id): 某地点的事件
- events_at_time(id): 某虚拟时间的事件
- subevents_of(id): 某事件的子事件

规则: graph_queries 最多 2 个,不需要图查询时为空数组;depth 取 1-5。

用户问题: <<QUESTION>>

输出 JSON:
{"intent": "factual|causal|relational|temporal|synthesis",
 "rewritten_query": "用于向量检索的查询文本",
 "target_entities": [{"type": "Character", "name_hint": "张三"}],
 "graph_queries": [{"template_id": "causes_of", "params": {"event_hint": "决裂", "depth": 3}}]}
```

`tests/test_planner.py`:

```python
from movie_kg.qa.planner import plan_retrieval
from tests.fakes import ScriptedLLM

PLAN = {"intent": "causal", "rewritten_query": "导致决裂的事件链",
        "target_entities": [{"type": "Character", "name_hint": "张三"}],
        "graph_queries": [{"template_id": "causes_of",
                           "params": {"event_hint": "决裂", "depth": 3}}]}


def test_plan_happy_path():
    plan = plan_retrieval(ScriptedLLM(PLAN), "导致张三和李四决裂的事件链?")
    assert plan.intent == "causal"
    assert plan.rewritten_query == "导致决裂的事件链"
    assert plan.graph_queries[0].template_id == "causes_of"


def test_plan_drops_unknown_template():
    raw = dict(PLAN, graph_queries=[
        {"template_id": "drop_all", "params": {}},
        {"template_id": "causes_of", "params": {"event_hint": "决裂", "depth": 3}}])
    plan = plan_retrieval(ScriptedLLM(raw), "q")
    assert [g.template_id for g in plan.graph_queries] == ["causes_of"]


def test_plan_caps_graph_queries_at_two():
    raw = dict(PLAN, graph_queries=[
        {"template_id": "events_after", "params": {"event_hint": "a", "depth": 2}},
        {"template_id": "causes_of", "params": {"event_hint": "b", "depth": 3}},
        {"template_id": "subevents_of", "params": {"event_hint": "c"}}])
    plan = plan_retrieval(ScriptedLLM(raw), "q")
    assert len(plan.graph_queries) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_planner.py -v`
Expected: FAIL,`ModuleNotFoundError`

- [ ] **Step 3: 实现 planner.py**

```python
from typing import Literal

from pydantic import BaseModel

from movie_kg.graph.query_templates import TEMPLATES
from movie_kg.prompts import load_prompt, render


class TargetEntity(BaseModel):
    type: Literal["Character", "Event", "VirtualTime", "Location"]
    name_hint: str


class GraphQueryPlan(BaseModel):
    template_id: str
    params: dict


class PlannerOutput(BaseModel):
    intent: Literal["factual", "causal", "relational", "temporal", "synthesis"]
    rewritten_query: str
    target_entities: list[TargetEntity] = []
    graph_queries: list[GraphQueryPlan] = []


class RetrievalPlan(BaseModel):
    intent: str
    rewritten_query: str
    target_entities: list[TargetEntity]
    graph_queries: list[GraphQueryPlan]


def plan_retrieval(llm, question: str) -> RetrievalPlan:
    cfg = getattr(llm, "cfg", None)
    model = cfg.text_model if cfg is not None else "text"
    system = render(load_prompt("qa_planning"), QUESTION=question)
    raw = llm.chat_json(model=model,
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": "请输出检索计划 JSON。"}],
                        schema=PlannerOutput)
    valid_queries = [g for g in raw.graph_queries if g.template_id in TEMPLATES][:2]
    return RetrievalPlan(intent=raw.intent, rewritten_query=raw.rewritten_query,
                         target_entities=raw.target_entities,
                         graph_queries=valid_queries)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_planner.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add config/prompts/qa_planning.md src/movie_kg/qa/ tests/test_planner.py
git commit -m "feat: QA retrieval planner (LLM-1) with template whitelist and 2-query cap"
```

---

### Task 14: 检索器(向量召回 + 实体绑定 + 模板执行)

**Files:**
- Create: `src/movie_kg/qa/retriever.py`、`tests/test_retriever.py`

**Interfaces:**
- Consumes: `RetrievalPlan`(Task 13)、`execute_template`/`TEMPLATES`(Task 12)、`MockSession`、`ScriptedLLM`
- Produces: `Evidence(source: Literal["vector","graph"], node_id: str, text: str, score: float | None = None, extra: dict = {})`;`vector_recall(session, llm, query: str, top_k: int = 5) -> list[Evidence]`(4 个向量索引各 Top-K 合并);`bind_entities(session, targets: list[TargetEntity], vector_hits: list[Evidence]) -> dict[str, str]`(name_hint → 节点 ID;精确 name/aliases 匹配,失败回落向量召回中同类型最近命中);`retrieve(session, llm, plan: RetrievalPlan, top_k: int = 5) -> list[Evidence]`(向量 + 绑定 + ≤2 模板,按 node_id 去重;模板参数值为字符串且命中绑定表时替换为绑定的 ID)。Task 15/17 消费。

- [ ] **Step 1: 写失败测试**

`tests/test_retriever.py`:

```python
from movie_kg.qa.planner import GraphQueryPlan, RetrievalPlan, TargetEntity
from movie_kg.qa.retriever import bind_entities, retrieve, vector_recall
from tests.fakes import MockSession, ScriptedLLM

VEC_HITS = [
    {"node": {"id": "evt_m1_001_01", "topic": "对峙", "description": "d",
              "event_type": "conflict"}, "score": 0.9},
]


def test_vector_recall_queries_all_four_indexes():
    session = MockSession(results=[VEC_HITS] * 4)
    hits = vector_recall(session, ScriptedLLM(), "张三发怒")
    assert len(hits) == 4
    indexes = [p["index"] for q, p in session.queries
               if "queryNodes" in q]
    assert set(indexes) == {"event_vec", "character_vec", "virtualtime_vec",
                            "location_vec"}


def test_bind_entities_exact_then_alias_then_fallback():
    # 1) 精确 name 命中
    session = MockSession(results=[[{"id": "char_m1_aaa"}]])
    bound = bind_entities(session, [TargetEntity(type="Character", name_hint="张三")],
                          vector_hits=[])
    assert bound["张三"] == "char_m1_aaa"
    # 2) aliases 命中(name 与 aliases 在同一次查询中匹配)
    session = MockSession(results=[[{"id": "char_m1_bbb"}]])
    bound = bind_entities(session, [TargetEntity(type="Character", name_hint="老张")],
                          vector_hits=[])
    assert bound["老张"] == "char_m1_bbb"
    # 3) 回落: 向量命中中同类型最近者
    from movie_kg.qa.retriever import Evidence
    hits = [Evidence(source="vector", node_id="char_m1_ccc", text="x", score=0.8,
                     extra={"label": "Character"})]
    session = MockSession(results=[[]])  # 图中无精确匹配
    bound = bind_entities(session, [TargetEntity(type="Character", name_hint="老板")],
                          vector_hits=hits)
    assert bound["老板"] == "char_m1_ccc"


def test_retrieve_executes_bound_templates_and_dedupes():
    plan = RetrievalPlan(
        intent="relational", rewritten_query="张三和李四的关系",
        target_entities=[TargetEntity(type="Character", name_hint="张三"),
                         TargetEntity(type="Character", name_hint="李四")],
        graph_queries=[GraphQueryPlan(
            template_id="relation_between_chars",
            params={"a": "张三", "b": "李四"})])
    # 队列: 4 次向量召回 + 2 次绑定查询 + 1 次模板执行
    session = MockSession(results=[
        VEC_HITS, [], [], [],                       # 向量召回(4 索引)
        [{"id": "char_m1_zhang"}], [{"id": "char_m1_li"}],  # 绑定
        [{"a": {"id": "char_m1_zhang", "name": "张三"},
          "r": [{"relation_type": "enemy"}],
          "b": {"id": "char_m1_li", "name": "李四"}}],      # 模板结果
    ])
    evidence = retrieve(session, ScriptedLLM(), plan)
    template_calls = [(q, p) for q, p in session.queries if "RELATED_TO" in q]
    assert template_calls, "必须执行图模板"
    assert template_calls[-1][1]["a"] == "char_m1_zhang"  # name_hint 已替换为 ID
    # 去重: 向量命中 evt_m1_001_01 + 图结果两个角色 = 3 条证据
    assert len(evidence) == 3


def test_retrieve_vector_only_when_no_graph_queries():
    plan = RetrievalPlan(intent="factual", rewritten_query="q",
                         target_entities=[], graph_queries=[])
    session = MockSession(results=[VEC_HITS, [], [], []])
    evidence = retrieve(session, ScriptedLLM(), plan)
    assert len(evidence) == 1 and evidence[0].source == "vector"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_retriever.py -v`
Expected: FAIL,`ModuleNotFoundError`

- [ ] **Step 3: 实现 retriever.py**

```python
from typing import Literal

from pydantic import BaseModel

from movie_kg.graph.query_templates import execute_template
from movie_kg.qa.planner import RetrievalPlan, TargetEntity

INDEXES = [("event_vec", "Event"), ("character_vec", "Character"),
           ("virtualtime_vec", "VirtualTime"), ("location_vec", "Location")]


class Evidence(BaseModel):
    source: Literal["vector", "graph"]
    node_id: str
    text: str
    score: float | None = None
    extra: dict = {}


def _node_text(node: dict) -> str:
    if "topic" in node:
        return f"[{node.get('event_type', '')}] {node.get('topic', '')}:{node.get('description', '')}"
    return f"{node.get('name', '')}:{node.get('description', '')}"


def vector_recall(session, llm, query: str, top_k: int = 5) -> list[Evidence]:
    vec = llm.embed([query])[0]
    out: list[Evidence] = []
    for index, label in INDEXES:
        for rec in session.run(
                "CALL db.index.vector.queryNodes($index, $k, $vec) YIELD node, score",
                index=index, k=top_k, vec=vec):
            node = dict(rec["node"])
            out.append(Evidence(source="vector", node_id=node.get("id", ""),
                                text=_node_text(node), score=rec.get("score"),
                                extra={"label": label}))
    return out


def bind_entities(session, targets: list[TargetEntity],
                  vector_hits: list[Evidence]) -> dict[str, str]:
    """name_hint -> 节点 ID: name/aliases 精确匹配,失败回落向量最近同类命中。"""
    bound: dict[str, str] = {}
    for t in targets:
        found = None
        recs = session.run(
            f"MATCH (n:{t.type}) WHERE n.name = $h OR $h IN n.aliases "
            "RETURN n.id AS id LIMIT 1", h=t.name_hint)
        # type 经 TargetEntity Literal 白名单校验,拼接入 Cypher 安全
        for rec in recs:
            found = rec["id"]
            break
        if found is None:
            for hit in vector_hits:
                if hit.extra.get("label") == t.type:
                    found = hit.node_id
                    break
        if found is not None:
            bound[t.name_hint] = found
    return bound


def retrieve(session, llm, plan: RetrievalPlan, top_k: int = 5) -> list[Evidence]:
    hits = vector_recall(session, llm, plan.rewritten_query, top_k)
    evidence: list[Evidence] = list(hits)
    bound = bind_entities(session, plan.target_entities, hits)
    for gq in plan.graph_queries[:2]:
        params = {k: (bound.get(v, v) if isinstance(v, str) else v)
                  for k, v in gq.params.items()}
        try:
            rows = execute_template(session, gq.template_id, params)
        except Exception:
            continue  # 模板参数不合法(如绑定失败)时跳过,不中断问答
        for row in rows:
            for key, value in row.items():
                if isinstance(value, dict) and "id" in value:
                    evidence.append(Evidence(source="graph", node_id=value["id"],
                                             text=_node_text(value),
                                             extra={"template": gq.template_id}))
                elif key == "path" and value:
                    for node in value.nodes if hasattr(value, "nodes") else []:
                        evidence.append(Evidence(
                            source="graph", node_id=node["id"],
                            text=_node_text(dict(node)),
                            extra={"template": gq.template_id}))
    seen: set[str] = set()
    deduped: list[Evidence] = []
    for e in evidence:
        if e.node_id and e.node_id not in seen:
            seen.add(e.node_id)
            deduped.append(e)
    return deduped
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_retriever.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/qa/retriever.py tests/test_retriever.py
git commit -m "feat: QA retriever with vector recall, entity binding, template dispatch"
```

---

### Task 15: 回答器(LLM-2)与 `kg ask` 命令

**Files:**
- Create: `config/prompts/qa_answering.md`、`src/movie_kg/qa/answerer.py`、`tests/test_answerer.py`、`tests/test_cli_ask.py`(Modify: `src/movie_kg/cli.py` 增加 ask 命令)

**Interfaces:**
- Consumes: `Evidence`(Task 14)、CLI 工厂(Task 11)
- Produces: `AnswerResult(answer: str, cited_ids: list[str])`、`answer(llm, question: str, evidence: list[Evidence]) -> AnswerResult`;CLI 命令 `ask(question: str)`(检索规划 → retrieve → 回答,恰 2 次 LLM 调用)。Task 17 消费 `answer()`。

- [ ] **Step 1: 写 Prompt 与失败测试**

`config/prompts/qa_answering.md`:

```text
你是剧情问答助手。仅基于给定证据回答问题;证据不足时明确说明缺什么;因果/时序问题按逻辑顺序组织;回答末尾以 "引用: id1, id2" 形式列出所用实体 ID。

证据:
<<EVIDENCE>>

问题: <<QUESTION>>

输出 JSON:
{"answer": "...", "cited_ids": ["evt_m1_001_01"]}
```

`tests/test_answerer.py`:

```python
from movie_kg.qa.answerer import answer
from movie_kg.qa.retriever import Evidence
from tests.fakes import ScriptedLLM

EV = [Evidence(source="vector", node_id="evt_m1_001_01",
               text="[conflict] 对峙:张三与李四对峙", score=0.9)]


def test_answer_returns_citations():
    llm = ScriptedLLM({"answer": "两人在码头仓库对峙,随后关系转为敌对。",
                       "cited_ids": ["evt_m1_001_01"]})
    result = answer(llm, "张三和李四怎么了?", EV)
    assert "对峙" in result.answer
    assert result.cited_ids == ["evt_m1_001_01"]
    # prompt 中包含证据行与问题
    _, messages = llm.calls[0]
    assert "[evt_m1_001_01]" in messages[0]["content"]
    assert "张三和李四怎么了" in messages[0]["content"]
```

`tests/test_cli_ask.py`:

```python
from typer.testing import CliRunner

import movie_kg.cli as cli_mod
from movie_kg.cli import main
from tests.fakes import MockDriver, MockSession, ScriptedLLM

PLAN = {"intent": "factual", "rewritten_query": "张三发怒",
        "target_entities": [{"type": "Character", "name_hint": "张三"}],
        "graph_queries": []}
ANSWER = {"answer": "张三在码头仓库对峙时发怒。",
          "cited_ids": ["evt_m1_001_01"]}

runner = CliRunner()


def test_ask_two_llm_calls(tmp_path, monkeypatch):
    session = MockSession(results=[[], [], [], []])  # 向量召回 4 索引 + 绑定查询
    llm = ScriptedLLM(PLAN, ANSWER)
    monkeypatch.setattr(cli_mod, "_make_llm", lambda cfg: llm)
    monkeypatch.setattr(cli_mod, "_make_driver", lambda cfg: MockDriver(session))
    result = runner.invoke(main, ["ask", "张三在哪里发怒了?"])
    assert result.exit_code == 0, result.output
    assert "码头仓库" in result.output
    assert len(llm.calls) == 2, "每问题必须恰 2 次 LLM 调用"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_answerer.py tests/test_cli_ask.py -v`
Expected: FAIL,`ModuleNotFoundError`(answerer)

- [ ] **Step 3: 实现 answerer.py 与 ask 命令**

`src/movie_kg/qa/answerer.py`:

```python
from pydantic import BaseModel

from movie_kg.prompts import load_prompt, render
from movie_kg.qa.retriever import Evidence


class AnswerOutput(BaseModel):
    answer: str
    cited_ids: list[str] = []


class AnswerResult(BaseModel):
    answer: str
    cited_ids: list[str] = []


def answer(llm, question: str, evidence: list[Evidence]) -> AnswerResult:
    cfg = getattr(llm, "cfg", None)
    model = cfg.text_model if cfg is not None else "text"
    lines = "\n".join(f"[{e.node_id}] {e.text}" for e in evidence) or "(无证据)"
    system = render(load_prompt("qa_answering"), EVIDENCE=lines, QUESTION=question)
    raw = llm.chat_json(model=model,
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": "请输出回答 JSON。"}],
                        schema=AnswerOutput)
    return AnswerResult(answer=raw.answer, cited_ids=raw.cited_ids)
```

`src/movie_kg/cli.py` 追加(在 `build` 命令后):

```python
@app.command()
def ask(question: str):
    """单轮问答: 规划 -> 检索 -> 回答(固定 2 次 LLM 调用)。"""
    from movie_kg.qa.answerer import answer
    from movie_kg.qa.planner import plan_retrieval
    from movie_kg.qa.retriever import retrieve

    llm = _make_llm(load_models_config())
    plan = plan_retrieval(llm, question)
    driver = _make_driver(load_neo4j_config())
    with driver.session() as session:
        evidence = retrieve(session, llm, plan)
    driver.close()
    result = answer(llm, question, evidence)
    typer.echo(result.answer)
    typer.echo(f"引用: {', '.join(result.cited_ids) if result.cited_ids else '(无)'}")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_answerer.py tests/test_cli_ask.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add config/prompts/qa_answering.md src/movie_kg/qa/answerer.py src/movie_kg/cli.py tests/test_answerer.py tests/test_cli_ask.py
git commit -m "feat: answerer (LLM-2) and kg ask command with citation output"
```

---

### Task 16: 构建端评估(角色 P/R/F1 + 关系抽样 + 报告)

**Files:**
- Create: `src/movie_kg/eval/__init__.py`、`src/movie_kg/eval/report.py`、`src/movie_kg/eval/build_eval.py`、`eval/relation_samples.example.csv`、`tests/test_report.py`、`tests/test_build_eval.py`(Modify: `src/movie_kg/cli.py` 注册 `eval build` 子命令)

**Interfaces:**
- Consumes: `MovieData`、`SceneExtraction`、`CheckpointStore`、`MockSession`
- Produces: `md_table(headers: list[str], rows: list[list]) -> str`;`ScenePRF(scene_id: str, gold: list[str], pred: list[str], precision: float, recall: float, f1: float)`、`character_prf(movie: MovieData, extractions: list[SceneExtraction]) -> list[ScenePRF]`(pred = 对齐后参与者名称,排除 `未识别:` 前缀)、`aggregate_prf(rows: list[ScenePRF]) -> dict`、`RELATION_TYPES = ["PRECEDES","CAUSES","SUBEVENT_OF","PARTICIPATES_IN","RELATED_TO"]`、`sample_relations(session, n_per_type: int = 30, seed: int = 42) -> list[dict]`(输出 CSV 行:type/start/end/props/label 空 列)、`score_relation_samples(rows: list[dict]) -> dict[str, float]`、`write_build_report(path, prf_rows, agg, relation_scores: dict | None)`;CLI:`kg eval build --movie m1`。Task 17/18 消费 `md_table`。

- [ ] **Step 1: 写失败测试**

`tests/test_report.py`:

```python
from movie_kg.eval.report import md_table


def test_md_table_renders():
    out = md_table(["指标", "值"], [["F1", 0.9], ["P", 1.0]])
    assert "| 指标 | 值 |" in out
    assert "| --- | --- |" in out
    assert "| F1 | 0.9 |" in out
```

`tests/test_build_eval.py`:

```python
from movie_kg.eval.build_eval import (
    aggregate_prf, character_prf, sample_relations, score_relation_samples)
from movie_kg.models.extraction import Participant, SceneEvent, SceneExtraction
from movie_kg.models.scene import CharacterMeta, SceneMeta
from tests.fakes import MockSession

META = SceneMeta(scene_id="scene_001", movie_id="m1", start_ts="00:00:00",
                 end_ts="00:02:00",
                 characters=[CharacterMeta(name="张三", aliases=[]),
                             CharacterMeta(name="李四", aliases=[])],
                 dialogues=[], location="x", virtual_time="y")


def _ext(participants):
    return SceneExtraction(
        scene_id="scene_001", movie_id="m1", status="ok",
        events=[SceneEvent(id="evt_m1_001_01", event_type="action", topic="t",
                           description="d", scene_id="scene_001", movie_id="m1",
                           order=1, participants=participants)])


def test_character_prf_excludes_unaligned():
    ext = _ext([Participant(name="张三"), Participant(name="未识别:王五")])
    rows = character_prf(_movie(), [ext])
    row = rows[0]
    assert set(row.gold) == {"张三", "李四"}
    assert set(row.pred) == {"张三"}   # 未识别不进 pred
    assert row.precision == 1.0 and row.recall == 0.5


def _movie():
    from movie_kg.models.scene import MovieData, SceneData
    return MovieData(movie_id="m1", title="t",
                     scenes=[SceneData(meta=META, srt_text="", video_path="v.mp4")])


def test_aggregate_prf():
    rows = character_prf(_movie(), [_ext([Participant(name="张三")])])
    agg = aggregate_prf(rows)
    assert agg["scenes"] == 1 and "f1" in agg


def test_sample_relations_queries_each_type():
    session = MockSession(results=[[{"start": "a", "end": "b", "props": {}}]] * 5)
    rows = sample_relations(session, n_per_type=5)
    assert len(rows) == 25
    assert {r["type"] for r in rows} == {
        "PRECEDES", "CAUSES", "SUBEVENT_OF", "PARTICIPATES_IN", "RELATED_TO"}


def test_score_relation_samples():
    rows = [{"type": "CAUSES", "label": "correct"},
            {"type": "CAUSES", "label": "incorrect"},
            {"type": "PRECEDES", "label": "correct"}]
    scores = score_relation_samples(rows)
    assert scores["CAUSES"] == 0.5 and scores["PRECEDES"] == 1.0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_report.py tests/test_build_eval.py -v`
Expected: FAIL,`ModuleNotFoundError`

- [ ] **Step 3: 实现 report.py、build_eval.py 与 CLI 子命令**

`src/movie_kg/eval/report.py`:

```python
def md_table(headers: list[str], rows: list[list]) -> str:
    head = "| " + " | ".join(str(h) for h in headers) + " |"
    sep = "|" + "|".join("---" for _ in headers) + "|"
    body = "\n".join("| " + " | ".join(str(c) for c in row) + " |" for row in rows)
    return f"{head}\n{sep}\n{body}\n"
```

`src/movie_kg/eval/build_eval.py`:

```python
import random

from pydantic import BaseModel

from movie_kg.eval.report import md_table
from movie_kg.models.extraction import SceneExtraction
from movie_kg.models.scene import MovieData

RELATION_TYPES = ["PRECEDES", "CAUSES", "SUBEVENT_OF", "PARTICIPATES_IN",
                  "RELATED_TO"]


class ScenePRF(BaseModel):
    scene_id: str
    gold: list[str]
    pred: list[str]
    precision: float
    recall: float
    f1: float


def character_prf(movie: MovieData,
                  extractions: list[SceneExtraction]) -> list[ScenePRF]:
    by_scene = {e.scene_id: e for e in extractions}
    rows: list[ScenePRF] = []
    for scene in movie.scenes:
        ext = by_scene.get(scene.meta.scene_id)
        gold = {c.name for c in scene.meta.characters}
        pred = set()
        if ext is not None:
            for ev in ext.events:
                for p in ev.participants:
                    if not p.name.startswith("未识别:"):
                        pred.add(p.name)
        precision = len(gold & pred) / len(pred) if pred else 0.0
        recall = len(gold & pred) / len(gold) if gold else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if precision + recall else 0.0)
        rows.append(ScenePRF(scene_id=scene.meta.scene_id, gold=sorted(gold),
                             pred=sorted(pred), precision=round(precision, 4),
                             recall=round(recall, 4), f1=round(f1, 4)))
    return rows


def aggregate_prf(rows: list[ScenePRF]) -> dict:
    if not rows:
        return {"scenes": 0}
    n = len(rows)
    return {"scenes": n,
            "precision": round(sum(r.precision for r in rows) / n, 4),
            "recall": round(sum(r.recall for r in rows) / n, 4),
            "f1": round(sum(r.f1 for r in rows) / n, 4)}


def sample_relations(session, n_per_type: int = 30, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    rows: list[dict] = []
    for rtype in RELATION_TYPES:
        if not isinstance(n_per_type, int) or n_per_type < 0:
            raise ValueError("n_per_type 必须为非负整数")
        recs = session.run(
            f"MATCH (a)-[r:{rtype}]->(b) RETURN a.id AS start, b.id AS end, "
            "properties(r) AS props")
        for rec in rng.sample(recs, min(n_per_type, len(recs))):
            rows.append({"type": rtype, "start": rec["start"], "end": rec["end"],
                         "props": rec["props"], "label": ""})
    return rows


def score_relation_samples(rows: list[dict]) -> dict[str, float]:
    by_type: dict[str, list[str]] = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r.get("label", ""))
    return {t: round(sum(1 for lb in ls if lb == "correct") / len(ls), 4)
            for t, ls in by_type.items() if ls}


def write_build_report(path, prf_rows: list[ScenePRF], agg: dict,
                       relation_scores: dict | None = None) -> None:
    lines = ["# 构建端评估报告\n", "## 角色识别(逐场景)\n",
             md_table(["场景", "gold", "pred", "P", "R", "F1"],
                      [[r.scene_id, ",".join(r.gold), ",".join(r.pred),
                        r.precision, r.recall, r.f1] for r in prf_rows]),
             "\n## 汇总\n",
             md_table(["指标", "值"], [[k, v] for k, v in agg.items()])]
    if relation_scores:
        lines += ["\n## 关系抽样正确率(人工标注)\n",
                  md_table(["关系类型", "accuracy"],
                           [[t, s] for t, s in relation_scores.items()])]
    lines.append("\n## 失败场景\n")
    lines.append("(见构建日志;无则略)\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
```

`eval/relation_samples.example.csv`(示例,说明人评填法):

```csv
type,start,end,props,label
CAUSES,evt_m1_001_01,evt_m1_001_02,"{'reason': '对峙升级'}",correct
```

`src/movie_kg/cli.py` 追加:

```python
@eval_app.command("build")
def eval_build(movie: str = typer.Option(..., "--movie")):
    """构建端评估: 角色识别 P/R/F1 + 关系抽样清单。"""
    from pathlib import Path
    import csv

    from movie_kg.eval.build_eval import (aggregate_prf, character_prf,
                                          sample_relations, write_build_report)
    from movie_kg.extraction.checkpoints import CheckpointStore
    from movie_kg.ingestion.data_loader import load_movie

    movie_dir = Path("data/movies") / movie
    data = load_movie(movie_dir)
    store = CheckpointStore(movie_dir)
    extractions = [e for e in (store.load(s.meta.scene_id) for s in data.scenes)
                   if e is not None]
    rows = character_prf(data, extractions)
    agg = aggregate_prf(rows)
    Path("eval").mkdir(exist_ok=True)
    samples = []
    driver = None
    try:
        driver = _make_driver(load_neo4j_config())
        with driver.session() as session:
            samples = sample_relations(session)
    finally:
        if driver is not None:
            driver.close()
    with open("eval/relation_samples.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["type", "start", "end", "props", "label"])
        w.writeheader()
        w.writerows(samples)
    write_build_report("eval/build_report.md", rows, agg)
    typer.echo(f"角色识别 F1={agg.get('f1')};抽样清单 -> eval/relation_samples.csv"
               f"(人工填 label 列后重新运行可算正确率)")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_report.py tests/test_build_eval.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/movie_kg/eval/ eval/relation_samples.example.csv src/movie_kg/cli.py tests/test_report.py tests/test_build_eval.py
git commit -m "feat: build-side evaluation (character PRF, relation sampling, report)"
```

---

### Task 17: 问答端评估(QA 集 + LLM judge + 消融)

**Files:**
- Create: `config/prompts/judge.md`、`src/movie_kg/eval/qa_eval.py`、`eval/qa_set.example.yaml`、`tests/test_qa_eval.py`(Modify: `src/movie_kg/cli.py` 注册 `eval qa` 子命令)

**Interfaces:**
- Consumes: `plan_retrieval`(Task 13)、`retrieve`(Task 14)、`answer`(Task 15)、`md_table`(Task 16)、CLI 工厂
- Produces: `QAItem(id, question, gold_answer, gold_entity_ids: list[str], intent: str)`、`load_qa_set(path) -> list[QAItem]`;`QAResult(item_id, intent, answer, cited_ids, evidence_ids, hit_rate: float | None, judged_correct: bool | None = None, judge_reason: str = "")`;`run_qa(session, llm, items: list[QAItem], mode: Literal["full","vector-only"]) -> list[QAResult]`;`judge_answers(llm, items, results) -> list[QAResult]`;`summarize(results) -> dict`(overall accuracy、per-intent accuracy、mean hit_rate);`write_qa_report(path, results, summary)`;CLI:`kg eval qa --qa-set eval/qa_set.yaml --mode full|vector-only`。

- [ ] **Step 1: 写 Prompt、示例集与失败测试**

`config/prompts/judge.md`:

```text
你是评分员。根据问题、标准答案和系统回答判断系统回答是否正确。
仅当系统回答包含标准答案的核心信息且无重大错误时判 correct;信息缺失、错误或答非所问判 incorrect,并给出一句话理由。

问题: <<QUESTION>>
标准答案: <<GOLD>>
系统回答: <<SYSTEM>>

输出 JSON:
{"correct": true, "reason": "一句话理由"}
```

`eval/qa_set.example.yaml`:

```yaml
items:
  - id: q001
    question: 张三在哪里发怒了?
    gold_answer: 张三在码头仓库与李四对峙时发怒。
    gold_entity_ids: [evt_m1_001_01]
    intent: factual
  - id: q002
    question: 导致张三和李四决裂的事件链是什么?
    gold_answer: 码头对峙升级,张三拔枪,两人关系转为敌对。
    gold_entity_ids: [evt_m1_001_01, evt_m1_001_02]
    intent: causal
```

`tests/test_qa_eval.py`:

```python
from movie_kg.eval.qa_eval import (
    judge_answers, load_qa_set, run_qa, summarize, write_qa_report)
from tests.fakes import MockSession, ScriptedLLM

PLAN = {"intent": "factual", "rewritten_query": "张三发怒",
        "target_entities": [], "graph_queries": []}
ANSWER = {"answer": "码头仓库。", "cited_ids": ["evt_m1_001_01"]}
JUDGE_OK = {"correct": True, "reason": "地点正确"}
JUDGE_BAD = {"correct": False, "reason": "答非所问"}


def test_load_qa_set(tmp_path):
    import textwrap
    p = tmp_path / "qa.yaml"
    p.write_text(textwrap.dedent("""\
        items:
          - id: q001
            question: q?
            gold_answer: a.
            gold_entity_ids: [evt_m1_001_01]
            intent: factual
        """), encoding="utf-8")
    items = load_qa_set(p)
    assert items[0].id == "q001" and items[0].intent == "factual"


def test_run_qa_full_vs_vector_only():
    items = load_qa_set(_make_set())
    session = MockSession(results=[[], [], [], []])
    llm = ScriptedLLM(PLAN, ANSWER)
    results = run_qa(session, llm, items, mode="vector-only")
    assert results[0].answer == "码头仓库。"
    # vector-only 模式下不应出现模板查询
    assert not any("RELATED_TO" in q or "PRECEDES" in q for q, _ in session.queries)


def _make_set(tmp=None):
    import tempfile
    from pathlib import Path
    d = Path(tmp) if tmp else Path(tempfile.mkdtemp())
    p = d / "qa.yaml"
    p.write_text("""items:
  - id: q001
    question: 张三在哪里发怒了?
    gold_answer: 张三在码头仓库发怒。
    gold_entity_ids: [evt_m1_001_01]
    intent: factual
""", encoding="utf-8")
    return p


def test_hit_rate_computed():
    items = load_qa_set(_make_set())
    session = MockSession(results=[
        [{"node": {"id": "evt_m1_001_01", "topic": "对峙", "description": "d",
                   "event_type": "conflict"}, "score": 0.9}, ], [], [], []])
    llm = ScriptedLLM(PLAN, ANSWER)
    results = run_qa(session, llm, items, mode="full")
    assert results[0].hit_rate == 1.0
    assert "evt_m1_001_01" in results[0].evidence_ids


def test_judge_and_summarize():
    items = load_qa_set(_make_set())
    session = MockSession(results=[[], [], [], []])
    results = run_qa(session, ScriptedLLM(PLAN, ANSWER), items, mode="full")
    judged = judge_answers(ScriptedLLM(JUDGE_OK), items, results)
    assert judged[0].judged_correct is True
    s = summarize(judged)
    assert s["overall_accuracy"] == 1.0
    assert s["per_intent"]["factual"]["accuracy"] == 1.0


def test_report_written(tmp_path):
    items = load_qa_set(_make_set())
    session = MockSession(results=[[], [], [], []])
    results = run_qa(session, ScriptedLLM(PLAN, ANSWER), items, mode="full")
    judged = judge_answers(ScriptedLLM(JUDGE_BAD), items, results)
    out = tmp_path / "qa_report.md"
    write_qa_report(out, judged, summarize(judged))
    text = out.read_text(encoding="utf-8")
    assert "factual" in text and "0.0" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_qa_eval.py -v`
Expected: FAIL,`ModuleNotFoundError`

- [ ] **Step 3: 实现 qa_eval.py 与 CLI 子命令**

`src/movie_kg/eval/qa_eval.py`:

```python
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


def write_qa_report(path: str | Path, results: list[QAResult], summary: dict) -> None:
    lines = ["# 问答端评估报告\n", "## 汇总\n",
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
```

`src/movie_kg/cli.py` 追加:

```python
@eval_app.command("qa")
def eval_qa(qa_set: str = typer.Option("eval/qa_set.yaml", "--qa-set"),
            mode: str = typer.Option("full", "--mode",
                                     help="full | vector-only(消融)")):
    """问答端评估: 跑 QA 集 + LLM judge + 汇总(消融用 --mode vector-only)。"""
    from movie_kg.eval.qa_eval import (judge_answers, load_qa_set, run_qa,
                                       summarize, write_qa_report)

    items = load_qa_set(qa_set)
    llm = _make_llm(load_models_config())
    driver = _make_driver(load_neo4j_config())
    with driver.session() as session:
        results = run_qa(session, llm, items, mode=mode)
    driver.close()
    judged = judge_answers(llm, items, results)
    summary = summarize(judged)
    write_qa_report("eval/qa_report.md", judged, summary)
    typer.echo(f"accuracy={summary['overall_accuracy']} "
               f"hit_rate={summary['mean_hit_rate']} -> eval/qa_report.md")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_qa_eval.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add config/prompts/judge.md src/movie_kg/eval/qa_eval.py eval/qa_set.example.yaml src/movie_kg/cli.py tests/test_qa_eval.py
git commit -m "feat: QA-side evaluation with LLM judge and vector-only ablation"
```

---

### Task 18: 端到端冒烟测试与 README

**Files:**
- Create: `tests/test_e2e.py`、`README.md`

**Interfaces:**
- Consumes: 全部前序任务(CLI build/ask/eval、ScriptedLLM、MockDriver、make_movie_dir)

- [ ] **Step 1: 写端到端失败测试**

`tests/test_e2e.py`:

```python
"""端到端冒烟: build -> ask -> eval build,全程离线(ScriptedLLM + MockDriver)。"""
from typer.testing import CliRunner

import movie_kg.cli as cli_mod
from movie_kg.cli import main
from tests.fakes import MockDriver, MockSession, ScriptedLLM
from tests.fixtures import make_movie_dir

A1 = {"events": [{"event_type": "conflict", "topic": "对峙", "description": "张三与李四对峙",
                  "participating_characters": [
                      {"name": "张三", "emotion": "愤怒", "role_description": "发起者",
                       "items": []},
                      {"name": "李四", "emotion": "紧张", "role_description": "应对",
                       "items": []}],
                  "subevents": [
                      {"event_type": "action", "topic": "拔枪", "description": "张三拔枪",
                       "participating_characters": []}]}]}
A2 = {"character_relations": [{"char_a": "张三", "char_b": "李四",
                               "relation_type": "enemy", "is_new": True,
                               "change_description": None, "evidence_event_index": 0}]}
B1 = {"ordered": [{"name": "第一天傍晚", "description": "d1"},
                  {"name": "第二天清晨", "description": "d2"}]}
B2 = {"characters": [{"canonical": "张三", "aliases": ["老张"], "description": "码头头目"},
                     {"canonical": "李四", "aliases": [], "description": "商人"}],
      "locations": [{"canonical": "码头仓库", "aliases": [], "description": "d"},
                    {"canonical": "警局办公室", "aliases": [], "description": "d"}]}
B3 = {"causal_links": [{"cause_id": "evt_m1_001_01", "effect_id": "evt_m1_001_02",
                        "reason": "对峙升级引发拔枪"}]}
PLAN = {"intent": "causal", "rewritten_query": "导致敌对的事件链",
        "target_entities": [{"type": "Character", "name_hint": "张三"}],
        "graph_queries": [{"template_id": "causes_of",
                           "params": {"event_hint": "对峙", "depth": 3}}]}
ANSWER = {"answer": "对峙升级引发拔枪,两人转为敌对。",
          "cited_ids": ["evt_m1_001_01", "evt_m1_001_02"]}

runner = CliRunner()


def test_full_pipeline_smoke(tmp_path, monkeypatch):
    d = make_movie_dir(tmp_path)
    monkeypatch.chdir(tmp_path)
    session = MockSession()
    llm = ScriptedLLM(A1, A2, A1, A2, B1, B2, B3)  # build 消耗 7 次
    monkeypatch.setattr(cli_mod, "_make_llm", lambda cfg: llm)
    monkeypatch.setattr(cli_mod, "_make_driver", lambda cfg: MockDriver(session))
    r = runner.invoke(main, ["build", "--movie", "m1"])
    assert r.exit_code == 0, r.output
    assert (d / "checkpoints" / "scene_002.json").exists()

    llm2 = ScriptedLLM(PLAN, ANSWER)  # ask 恰 2 次
    monkeypatch.setattr(cli_mod, "_make_llm", lambda cfg: llm2)
    r = runner.invoke(main, ["ask", "导致张三和李四敌对的事件链?"])
    assert r.exit_code == 0, r.output
    assert "拔枪" in r.output
    assert len(llm2.calls) == 2

    r = runner.invoke(main, ["eval", "build", "--movie", "m1"])
    assert r.exit_code == 0, r.output
    from pathlib import Path
    assert Path("eval/build_report.md").exists()
    report = Path("eval/build_report.md").read_text(encoding="utf-8")
    assert "F1" in report and "scene_001" in report
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_e2e.py -v`
Expected: FAIL(eval 子命令或断言未满足)

- [ ] **Step 3: 写 README.md**

```markdown
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

- [ ] **Step 4: 运行全部测试确认通过**

Run: `python -m pytest -v`
Expected: 全部 passed(e2e + 全部单元测试),integration 默认跳过

- [ ] **Step 5: Commit**

```bash
git add tests/test_e2e.py README.md
git commit -m "feat: end-to-end smoke test and README"
```

---

## 计划自检结论

- **Spec 覆盖**: spec §3(输入/校验)→ Task 2/3;§4(模式)→ Task 5/9/10;§5(构建)→ Task 7/8/9/10/11;§6(存储检索)→ Task 10/12/14;§7(问答)→ Task 12/13/14/15;§8(评估)→ Task 16/17;§9-11(选型/结构/计划)→ Task 1 及各任务文件布局。验收标准 1(断点续跑)、3(2 次调用)、4(成本校准)有测试;2/3(阈值)由 eval 命令产出报告衡量。
- **类型一致性**: `Frame`/`ScriptedLLM`/`MockSession`/`MockDriver`/`make_movie_dir`(tests/fakes.py、tests/fixtures.py)、`SceneEvent`/`ResolvedRelation`/`SceneExtraction`(models/extraction.py)、`AliasPlan.resolve`、`char_id/vt_id/loc_id`、`RetrievalPlan`/`Evidence`/`AnswerResult`、`QAItem`/`QAResult`、`md_table` 在各任务间签名一致。
- **无占位符**: 所有步骤含可执行代码与期望输出。



