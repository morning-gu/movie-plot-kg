# v0.3.0 输入简化与标注增富 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落实 DESIGN.md v0.3.0:去掉 SRT 输入(台词内嵌元数据)、采用 shots/结构化 keyframes 的新元数据 schema、统一影片绝对时间戳、A1 全面 grounding(图文交错),并打通 evidence_frame_ids 证据帧引用与 Scene.description 入图。

**Architecture:** 改动收敛在输入与抽取侧:models/scene.py 定义新 schema,loader 去 SRT 并做窗口/重叠/全局 ID/时长校验,采样器做绝对→相对换算与均匀截断,A1 prompt 扩容先验并图文交错;输出侧 ExtractedEvent/SceneEvent 增加 evidence_frame_ids(parse 过滤无效引用),writer 写入 Event.evidence_frame_ids 与 Scene.description。图谱标签/关系/索引、Phase B、QA、评估指标全部不动。

**Tech Stack:** Python 3.11+、pydantic 2.14.1、PyAV 19.0.1、typer(均为既有依赖,不新增包)。

**Spec:** docs/DESIGN.md(v0.3.0,§3 输入数据规范 / §4.1.2-4.1.3 / §5.2 / 附录 C)

## Global Constraints

- 元数据时间戳**一律影片绝对时间** `HH:MM:SS`;`parse_ts(ts) − parse_ts(scene.start_ts)` 的换算只发生在 `sample_frames` 内部,loader/prompt/断言侧永远用绝对时间(DESIGN §3.2)
- `shot_id` / `frame_id` 由输入数据保证**全局唯一**(整部电影),loader 跨场景查重(DESIGN §3.2)
- 采样 `max_frames` 默认 16:标注帧优先(去重 tiebreak 保留标注帧)、不足时均匀补足、超上限时在标注列表内**均匀截断(含首尾)**(DESIGN §3.1)
- 片段时长 ≈ `end − start`,容差 **±2s**(重编码精确切割,DESIGN §3.1)
- 台词渲染格式:`{ts} {char}({emotion}/{action}): {text}`,无情绪/动作时省略括号段
- evidence_frame_ids 仅保留本场景出现过的 frame_id;无效引用剔除 + `logger`(movie_kg.extraction.parse)告警,**不废弃场景**(DESIGN §5.2)
- PyAV 19:seek 单位是 stream time_base(`int(seconds / stream.time_base)`);container.duration 是微秒
- pydantic v2 BaseModel 默认忽略未知字段——旧 checkpoint(无 evidence_frame_ids)兼容依赖此行为
- 测试全程离线(tests/fakes.py 替身);不得新增网络/真实 LLM 依赖;集成测试仅 env-gated
- `srt.py` 删除后 `from movie_kg.ingestion.srt import ...` 不得残留(grep 验证)
- 提交遵循 conventional commits;每个 Task 一个提交

## Review Focus

spec 未明说、但最容易咬到使用者的五类输入,及钉住它们的测试:

1. **绝对/相对时间戳混用**(keyframe.ts 标了片段内相对时间,早于场景 start)→ 必须 collect-all 报"关键帧越界",而非静默 clamp 到片段末帧 → Task 2 `test_keyframe_ts_outside_window`
2. **片段时长与场景窗口偏差 >2s**(切错片/写错窗口)→ 校验报"偏差超过" → Task 2 `test_duration_mismatch_reports_error`
3. **frame_id 跨场景重复**(以为场景内唯一就行)→ 校验报"frame_id 重复" → Task 2 `test_duplicate_frame_id_across_scenes`
4. **标注帧数 > max_frames**(采样退化成"取前 N",尾部剧情帧全丢)→ 均匀截断且首尾保留 → Task 2 `test_even_truncation_keeps_both_ends`
5. **LLM 幻觉引用不存在的 frame_id** → 剔除 + 告警,场景不废 → Task 3 `test_parse_a1_filters_invalid_evidence`

---

### Task 1: timefmt 时间工具模块(parse_ts 迁移 + fmt_ts)

**Files:**
- Create: `src/movie_kg/ingestion/timefmt.py`
- Test: `tests/test_timefmt.py`

**Interfaces:**
- Consumes: 无(自包含;`srt.py` 暂留,Task 2 删除)
- Produces: `movie_kg.ingestion.timefmt.parse_ts(ts: str) -> float`(行为与现 srt.py 完全一致)、`fmt_ts(seconds: float) -> str`(整秒,`"HH:MM:SS"`,Task 4 的帧配文用它展示绝对时间)

- [ ] **Step 1: 写失败测试**

`tests/test_timefmt.py`(新建):

```python
from movie_kg.ingestion.timefmt import fmt_ts, parse_ts


def test_parse_ts_variants():
    assert parse_ts("00:03:12") == 192.0
    assert parse_ts("00:03:12,500") == 192.5
    assert parse_ts("00:03:12.5") == 192.5
    assert parse_ts("01:00:00") == 3600.0


def test_fmt_ts():
    assert fmt_ts(192.0) == "00:03:12"
    assert fmt_ts(210.4) == "00:03:30"   # 截断到整秒
    assert fmt_ts(3600.0) == "01:00:00"
    assert fmt_ts(parse_ts("00:03:30")) == "00:03:30"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_timefmt.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'movie_kg.ingestion.timefmt'`

- [ ] **Step 3: 实现**

`src/movie_kg/ingestion/timefmt.py`(新建;`parse_ts` 逐字节照抄 srt.py,仅去掉 SrtEntry 依赖):

```python
import re

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


def fmt_ts(seconds: float) -> str:
    """秒 -> 'HH:MM:SS'(截断到整秒,用于 prompt 展示绝对时间)。"""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    mi, s = divmod(rem, 60)
    return f"{h:02d}:{mi:02d}:{s:02d}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_timefmt.py -v`
Expected: PASS(2 项)

- [ ] **Step 5: 全量回归 + 提交**

Run: `python -m pytest`
Expected: 全绿(srt.py 未动,原 94+1 skipped 不变,新增 2)

```bash
git add src/movie_kg/ingestion/timefmt.py tests/test_timefmt.py
git commit -m "feat: add ingestion/timefmt (parse_ts migration + fmt_ts)"
```

---

### Task 2: 输入契约重构 —— 新元数据模型、loader 去 SRT 与新校验、采样器升级

**Files:**
- Modify: `src/movie_kg/models/scene.py`(整文件重写)
- Modify: `src/movie_kg/ingestion/data_loader.py`(整文件重写)
- Modify: `src/movie_kg/ingestion/frame_sampler.py`(整文件重写)
- Modify: `src/movie_kg/extraction/pipeline.py:6,50-51`(import 与调用点)
- Modify: `src/movie_kg/extraction/event_extractor.py`(台词源切换,完整 grounding 留 Task 4)
- Modify: `config/prompts/event_extraction.md:4-5`(SRT 占位符 → DIALOGUES)
- Modify: `tests/fixtures.py`(make_movie_dir 新 schema)
- Delete: `src/movie_kg/ingestion/srt.py`、`tests/test_srt.py`
- Test: `tests/test_data_loader.py`(重写)、`tests/test_frame_sampler.py`(重写)、`tests/test_prompts.py`、`tests/test_build_eval.py:35`、`tests/test_event_extractor.py`

**Interfaces:**
- Consumes: Task 1 的 `parse_ts`
- Produces(Task 3/4 依赖):
  - `KeyframeMeta(frame_id: str, ts: str, frame_type: str|None = None, description: str|None = None)`
  - `ShotMeta(shot_id: str, start_ts: str, end_ts: str, shot_type: str|None = None, description: str|None = None, keyframes: list[KeyframeMeta] = [])`
  - `SceneMeta` 增加 `description: str = ""`、`props: list[str] = []`、`shots: list[ShotMeta] = []`,删除旧 `keyframes: list[str]`;方法 `flat_keyframes() -> list[KeyframeMeta]`(按 shot 顺序展平)
  - `SceneData(meta, video_path)`(`srt_text` 字段删除)
  - `Frame(ts: float, jpeg: bytes, keyframe: KeyframeMeta | None = None)`(`ts` 为影片绝对秒;补足帧 `keyframe=None`)
  - `sample_frames(video_path, keyframes: list[KeyframeMeta], scene_start: float, max_frames: int = 16) -> list[Frame]`
  - fixtures `make_movie_dir`:scene_001 带 `frame_001` 标注帧与 description/props,scene_002 无 shots(测兜底采样);不再写 subtitles/

**说明**:这是一次契约整体切换,模型/loader/采样器/抽取器必须同任务落地才有绿色中间态;测试先行整批变红,再整批实现。

- [ ] **Step 1: 更新 fixtures.py**

`tests/fixtures.py` 中 `make_movie_dir` 整体替换为(保留 `make_video` 不变;删除 `import shutil`):

```python
def make_movie_dir(tmp_path: Path, n_scenes: int = 2) -> Path:
    """构造完整的单部电影输入目录(视频/元数据),返回目录路径。"""
    import json

    movie_dir = tmp_path / "data" / "movies" / "m1"
    for sub in ("video", "scenes", "checkpoints"):
        (movie_dir / sub).mkdir(parents=True, exist_ok=True)
    (movie_dir / "movie.json").write_text(
        json.dumps({"movie_id": "m1", "title": "测试影片"}), encoding="utf-8")
    windows = [("00:00:00", "00:00:02"), ("00:00:02", "00:00:04"),
               ("00:00:04", "00:00:06")]
    base = {
        "characters": [{"name": "张三", "aliases": ["老张"]},
                       {"name": "李四", "aliases": []}],
        "location": "码头仓库", "virtual_time": "第一天傍晚",
        "description": "张三与李四在仓库对峙。",
        "props": ["枪"],
    }
    for i in range(n_scenes):
        seq = i + 1
        sid = f"scene_{seq:03d}"
        start, end = windows[i]
        dlg_ts = f"00:00:{2 * i + 1:02d}"  # 每场景窗口中点附近
        dialogues = [
            {"char": "张三", "text": "你到底想怎样?", "ts": dlg_ts,
             "emotion": "愤怒", "action": "拍桌"},
            {"char": "李四", "text": "我只是来谈生意的。", "ts": dlg_ts},
        ]
        m = dict(base, dialogues=dialogues, scene_id=sid, movie_id="m1",
                 start_ts=start, end_ts=end,
                 location="码头仓库" if i == 0 else "警局办公室",
                 virtual_time="第一天傍晚" if i == 0 else "第二天清晨")
        if i == 0:  # 仅 scene_001 带镜头/标注帧;scene_002 测无标注兜底
            m["shots"] = [
                {"shot_id": "shot_001", "start_ts": "00:00:00", "end_ts": "00:00:01",
                 "shot_type": "中景", "description": "两人隔桌对坐",
                 "keyframes": [
                     {"frame_id": "frame_001", "ts": "00:00:01", "frame_type": "特写",
                      "description": "张三的手按在枪上"}]},
                {"shot_id": "shot_002", "start_ts": "00:00:01", "end_ts": "00:00:02",
                 "shot_type": "全景", "description": "仓库全貌",
                 "keyframes": []},
            ]
        (movie_dir / "scenes" / f"{sid}.json").write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8")
        make_video(movie_dir / "video" / f"{sid}.mp4", seconds=2)
    return movie_dir
```

- [ ] **Step 2: 重写 test_data_loader.py**

整文件替换为:

```python
import json

import pytest

from movie_kg.ingestion.data_loader import InputValidationError, load_movie
from tests.fixtures import make_movie_dir, make_video


def _edit_scene(movie_dir, sid, mutate):
    p = movie_dir / "scenes" / f"{sid}.json"
    m = json.loads(p.read_text(encoding="utf-8"))
    mutate(m)
    p.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")


def test_load_movie_happy_path(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    movie = load_movie(movie_dir)
    assert movie.movie_id == "m1" and movie.title == "测试影片"
    assert [s.meta.scene_id for s in movie.scenes] == ["scene_001", "scene_002"]
    s1 = movie.scenes[0]
    assert s1.meta.characters[0].name == "张三"
    assert s1.video_path.name == "scene_001.mp4"
    assert [k.frame_id for k in s1.meta.flat_keyframes()] == ["frame_001"]
    assert s1.meta.shots[0].shot_type == "中景"
    assert s1.meta.description == "张三与李四在仓库对峙。"


def test_missing_video_reports_error(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    (movie_dir / "video" / "scene_001.mp4").unlink()
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("scene_001.mp4" in msg for msg in e.value.errors)


def test_dialogue_char_not_in_roster(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    _edit_scene(movie_dir, "scene_001",
                lambda m: m["dialogues"][0].update(char="王五"))
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("王五" in msg for msg in e.value.errors)


def test_dialogue_ts_outside_window(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    _edit_scene(movie_dir, "scene_001",
                lambda m: m["dialogues"][0].update(ts="00:09:00"))
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("台词时间越界" in msg for msg in e.value.errors)


def test_keyframe_ts_outside_window(tmp_path):
    # 绝对/相对时间戳混用(标了窗口外时间)必须报错,而非静默 clamp 到片段末帧
    movie_dir = make_movie_dir(tmp_path)
    _edit_scene(movie_dir, "scene_001",
                lambda m: m["shots"][0]["keyframes"][0].update(ts="00:00:05"))
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("关键帧越界" in msg for msg in e.value.errors)


def test_shot_overlap_reports_error(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    _edit_scene(movie_dir, "scene_001",
                lambda m: m["shots"][1].update(start_ts="00:00:00"))
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("镜头重叠" in msg for msg in e.value.errors)


def test_duplicate_frame_id_across_scenes(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    _edit_scene(movie_dir, "scene_002", lambda m: m.update(shots=[
        {"shot_id": "shot_009", "start_ts": "00:00:02", "end_ts": "00:00:04",
         "keyframes": [{"frame_id": "frame_001", "ts": "00:00:03"}]}]))
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("frame_id 重复" in msg for msg in e.value.errors)


def test_duration_mismatch_reports_error(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    make_video(movie_dir / "video" / "scene_001.mp4", seconds=8)  # 窗口只有 2s
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("偏差超过" in msg for msg in e.value.errors)


def test_unopenable_video_reports_error(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    (movie_dir / "video" / "scene_001.mp4").write_bytes(b"not a video")
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("无法打开" in msg for msg in e.value.errors)
```

- [ ] **Step 3: 重写 test_frame_sampler.py**

整文件替换为:

```python
from movie_kg.ingestion.frame_sampler import sample_frames
from movie_kg.models.scene import KeyframeMeta
from tests.fixtures import make_video


def kf(fid, ts, desc="d"):
    return KeyframeMeta(frame_id=fid, ts=ts, description=desc)


def test_uniform_sampling_caps_at_16(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=4)
    frames = sample_frames(video, [], scene_start=0.0)
    assert len(frames) == 16
    assert all(f.jpeg[:2] == b"\xff\xd8" for f in frames)  # JPEG magic
    assert frames == sorted(frames, key=lambda f: f.ts)
    assert all(f.keyframe is None for f in frames)          # 补足帧无标注


def test_absolute_ts_converted_via_scene_start(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=4)
    frames = sample_frames(video, [kf("f1", "00:10:01")],
                           scene_start=600.0, max_frames=4)
    ann = [f for f in frames if f.keyframe is not None]
    assert ann and abs(ann[0].ts - 601.0) < 0.5   # 绝对时间保留,偏移正确换算
    assert all(f.ts >= 600.0 for f in frames)


def test_even_truncation_keeps_both_ends(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=20)
    kfs = [kf(f"f{i:02d}", f"00:00:{i:02d}") for i in range(1, 21)]  # 20 个标注帧
    frames = sample_frames(video, kfs, scene_start=0.0, max_frames=6)
    ann = [f for f in frames if f.keyframe is not None]
    assert len(frames) == 6 and len(ann) == 6   # 截断后不再均匀补足
    assert ann[0].keyframe.frame_id == "f01"    # 首帧保留
    assert ann[-1].keyframe.frame_id == "f20"   # 尾帧保留(而非取前 6)


def test_fill_frames_yield_to_annotated(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=4)
    # max_frames=4 时补足网格 step=1.0s,与标注帧 00:00:01 重合 → 标注帧胜出
    frames = sample_frames(video, [kf("f1", "00:00:01")], scene_start=0.0,
                           max_frames=4)
    ann = [f for f in frames if f.keyframe is not None]
    assert ann and ann[0].keyframe.frame_id == "f1"
    rounded = [round(f.ts, 1) for f in frames]
    assert len(set(rounded)) == len(rounded)     # 无同点重复帧


def test_small_budget(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video, seconds=4)
    frames = sample_frames(video, [kf("f1", "00:00:01")], scene_start=0.0,
                           max_frames=4)
    assert 3 <= len(frames) <= 4
```

- [ ] **Step 4: 三处小改(test_prompts / test_build_eval / test_event_extractor)**

`tests/test_prompts.py` 第 6 行:

```python
    assert "剧情分析专家" in text and "<<DIALOGUES>>" in text
```

`tests/test_build_eval.py` `_movie()` 内(去掉 `srt_text=""`):

```python
    return MovieData(movie_id="m1", title="t",
                     scenes=[SceneData(meta=META, video_path="v.mp4")])
```

`tests/test_event_extractor.py` 的 `test_extract_parses_and_aligns` 末尾追加(system 提示词里应有带情绪/动作标注的台词渲染):

```python
    system = messages[0]["content"]
    assert "00:00:01 张三(愤怒/拍桌): 你到底想怎样?" in system
    assert "我只是来谈生意的" in system
```

- [ ] **Step 5: 删除 tests/test_srt.py**

```bash
git rm tests/test_srt.py
```

- [ ] **Step 6: 跑测试确认整批变红**

Run: `python -m pytest tests/test_data_loader.py tests/test_frame_sampler.py tests/test_prompts.py tests/test_event_extractor.py -v`
Expected: 大面积 FAIL——`AttributeError: flat_keyframes`、旧 loader 报"缺少字幕"(fixtures 已无 subtitles/)、`sample_frames() got an unexpected keyword argument 'scene_start'`、`<<DIALOGUES>>` 不在模板中、`SceneData` 无 `srt_text`。这是契约切换的预期红。

- [ ] **Step 7: 实现 models/scene.py(整文件替换)**

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


class KeyframeMeta(BaseModel):
    frame_id: str
    ts: str
    frame_type: str | None = None
    description: str | None = None


class ShotMeta(BaseModel):
    shot_id: str
    start_ts: str
    end_ts: str
    shot_type: str | None = None
    description: str | None = None
    keyframes: list[KeyframeMeta] = []


class SceneMeta(BaseModel):
    scene_id: str
    movie_id: str
    start_ts: str
    end_ts: str
    characters: list[CharacterMeta]
    dialogues: list[Dialogue]
    location: str
    virtual_time: str
    description: str = ""
    props: list[str] = []
    shots: list[ShotMeta] = []

    def flat_keyframes(self) -> list[KeyframeMeta]:
        """按 shot 顺序展平全部标注帧(采样与证据校验的基准列表)。"""
        return [k for sh in self.shots for k in sh.keyframes]


class SceneData(BaseModel):
    meta: SceneMeta
    video_path: Path

    model_config = {"arbitrary_types_allowed": True}


class MovieData(BaseModel):
    movie_id: str
    title: str
    scenes: list[SceneData]
```

(SrtEntry 删除;`keyframes: list[str]` 字段删除。)

- [ ] **Step 8: 实现 data_loader.py(整文件替换)**

```python
import json
from pathlib import Path

import av
import av.error

from movie_kg.ingestion.timefmt import parse_ts
from movie_kg.models.scene import MovieData, SceneData, SceneMeta

_TOL = 2.0  # 片段时长与场景窗口的容差(秒,DESIGN §3.1)


class InputValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _probe_duration(video: Path) -> float:
    with av.open(str(video)) as container:
        return (container.duration or 0) / 1_000_000.0


def load_movie(movie_dir: str | Path) -> MovieData:
    movie_dir = Path(movie_dir)
    errors: list[str] = []
    movie_id = movie_dir.name
    info = json.loads((movie_dir / "movie.json").read_text(encoding="utf-8"))
    scene_files = sorted((movie_dir / "scenes").glob("scene_*.json"))
    scenes: list[SceneData] = []
    prev_end = -1.0
    seen_shots: set[str] = set()
    seen_frames: set[str] = set()
    for sf in scene_files:
        meta = SceneMeta.model_validate_json(sf.read_text(encoding="utf-8"))
        if meta.movie_id != movie_id:
            errors.append(f"{sf.name}: movie_id={meta.movie_id} 与目录 {movie_id} 不一致")
        video = movie_dir / "video" / f"{meta.scene_id}.mp4"
        if not video.exists():
            errors.append(f"{meta.scene_id}: 缺少视频 {video.name}")
        start, end = parse_ts(meta.start_ts), parse_ts(meta.end_ts)
        if start >= end:
            errors.append(f"{meta.scene_id}: 起止时间非法({meta.start_ts} >= {meta.end_ts})")
        if start < prev_end:
            errors.append(f"{meta.scene_id}: 场景窗口与上一场景重叠")
        prev_end = end
        roster = ({c.name for c in meta.characters}
                  | {a for c in meta.characters for a in c.aliases})
        for d in meta.dialogues:
            if d.char not in roster:
                errors.append(f"{meta.scene_id}: 台词角色 {d.char} 不在角色名单中")
            if not (start <= parse_ts(d.ts) <= end):
                errors.append(f"{meta.scene_id}: 台词时间越界({d.ts})")
        for sh in meta.shots:
            if sh.shot_id in seen_shots:
                errors.append(f"{meta.scene_id}: shot_id 重复({sh.shot_id})")
            seen_shots.add(sh.shot_id)
            s, e = parse_ts(sh.start_ts), parse_ts(sh.end_ts)
            if not (start <= s <= end and start <= e <= end):
                errors.append(
                    f"{meta.scene_id}: 镜头越界({sh.shot_id}: {sh.start_ts}-{sh.end_ts})")
            for k in sh.keyframes:
                if k.frame_id in seen_frames:
                    errors.append(f"{meta.scene_id}: frame_id 重复({k.frame_id})")
                seen_frames.add(k.frame_id)
                if not (start <= parse_ts(k.ts) <= end):
                    errors.append(f"{meta.scene_id}: 关键帧越界({k.frame_id}: {k.ts})")
        intervals = sorted((parse_ts(sh.start_ts), parse_ts(sh.end_ts), sh.shot_id)
                           for sh in meta.shots)
        for (_, e0, _), (s1, _, id1) in zip(intervals, intervals[1:]):
            if s1 < e0:
                errors.append(f"{meta.scene_id}: 镜头重叠({id1})")
        if video.exists():
            try:
                dur = _probe_duration(video)
            except av.error.FFmpegError:
                errors.append(f"{meta.scene_id}: 视频无法打开({video.name})")
            else:
                if abs(dur - (end - start)) > _TOL:
                    errors.append(
                        f"{meta.scene_id}: 片段时长 {dur:.1f}s 与场景窗口 "
                        f"{end - start:.1f}s 偏差超过 {_TOL:.0f}s")
        scenes.append(SceneData(meta=meta, video_path=video))
    if errors:
        raise InputValidationError(errors)
    return MovieData(movie_id=movie_id, title=info.get("title", movie_id), scenes=scenes)
```

(与旧版一致:malformed 顶层时间戳(如 start_ts 非法)沿用既有行为——parse_ts 抛 ValueError,不进 collect-all。)

- [ ] **Step 9: 实现 frame_sampler.py(整文件替换)**

```python
import io
from dataclasses import dataclass
from pathlib import Path

import av

from movie_kg.ingestion.timefmt import parse_ts
from movie_kg.models.scene import KeyframeMeta


@dataclass
class Frame:
    ts: float                             # 影片绝对时间(秒)
    jpeg: bytes
    keyframe: KeyframeMeta | None = None  # 标注帧带元数据;均匀补足帧为 None


def _even_subset(n: int, k: int) -> list[int]:
    """从 n 个元素中取均匀分布的 k 个下标(含首尾)。"""
    if k >= n:
        return list(range(n))
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def sample_frames(video_path: Path, keyframes: list[KeyframeMeta],
                  scene_start: float, max_frames: int = 16) -> list[Frame]:
    """标注关键帧优先(影片绝对时间经 scene_start 换算为片段内偏移),
    不足时均匀补足,超上限时在标注列表内均匀截断;总数 ≤ max_frames。"""
    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        duration = (container.duration or 0) / 1_000_000.0
        annotated = keyframes if len(keyframes) <= max_frames \
            else [keyframes[i] for i in _even_subset(len(keyframes), max_frames)]
        targets: list[tuple[float, KeyframeMeta | None]] = [
            (min(max(parse_ts(k.ts) - scene_start, 0.0), duration), k)
            for k in annotated]
        fill = max_frames - len(targets)
        if fill > 0 and duration > 0:
            step = duration / (fill + 1)
            targets += [(step * (i + 1), None) for i in range(fill)]
        targets.sort(key=lambda t: (t[0], t[1] is None))  # 同点标注帧排前
        picked: list[tuple[float, KeyframeMeta | None]] = []
        for t in targets:
            if not picked or t[0] - picked[-1][0] > 0.1:
                picked.append(t)
            elif t[1] is not None and picked[-1][1] is None:
                picked[-1] = t  # 相邻重合时补足帧让位给标注帧
        frames: list[Frame] = []
        for rel, k in picked[:max_frames]:
            container.seek(int(rel / stream.time_base), stream=stream)
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
                frames.append(Frame(ts=scene_start + rel, jpeg=buf.getvalue(),
                                    keyframe=k))
        return frames
```

- [ ] **Step 10: pipeline 调用点 + event_extractor 台词源 + prompt 占位符 + 删 srt.py**

`src/movie_kg/extraction/pipeline.py`:import 区加 `from movie_kg.ingestion.timefmt import parse_ts`,第 50-51 行改为:

```python
            frames = sample_frames(scene.video_path, scene.meta.flat_keyframes(),
                                   scene_start=parse_ts(scene.meta.start_ts),
                                   max_frames=max_frames)
```

`src/movie_kg/extraction/event_extractor.py`:新增 `_dialogue_text`(放在 `_roster_text` 后),`render` 调用把 `SRT=scene.srt_text.strip() or "(无台词)"` 换成 `DIALOGUES=_dialogue_text(scene)`,docstring 中"SRT"字样改"元数据台词":

```python
def _dialogue_text(scene: SceneData) -> str:
    lines = []
    for d in scene.meta.dialogues:
        cue = f"({d.emotion}/{d.action})" if d.emotion or d.action else ""
        lines.append(f"{d.ts} {d.char}{cue}: {d.text}")
    return "\n".join(lines) if lines else "(无台词)"
```

`config/prompts/event_extraction.md` 第 4-5 行(`台词(SRT):` + `<<SRT>>`)改为:

```text
台词:
<<DIALOGUES>>
```

删除模块:

```bash
git rm src/movie_kg/ingestion/srt.py
```

- [ ] **Step 11: 跑测试确认通过**

Run: `python -m pytest tests/test_data_loader.py tests/test_frame_sampler.py tests/test_prompts.py tests/test_event_extractor.py tests/test_timefmt.py -v`
Expected: PASS

- [ ] **Step 12: 全量回归 + srt 残留检查**

Run: `python -m pytest`
Expected: 全绿(test_srt.py 已删,时间戳用例由 test_timefmt.py 接管;e2e/cli/pipeline_a 走新 fixture 自动适配)。

Run: `grep -rn "ingestion.srt\|srt_text\|parse_srt" src tests config`
Expected: 无输出。

- [ ] **Step 13: Commit**

```bash
git add -A
git commit -m "feat: rework input contract (drop SRT, shots/keyframes metadata, absolute timestamps, sampler upgrade)"
```

---

### Task 3: 证据帧链路 —— evidence_frame_ids 与 Scene.description 入图

**Files:**
- Modify: `src/movie_kg/models/extraction.py:13-18,38-48`
- Modify: `src/movie_kg/extraction/parse.py:44-72`
- Modify: `src/movie_kg/graph/writer.py:38-48,62-70`
- Test: `tests/test_parse.py`(追加)、`tests/test_writer.py`(META 加 description + 两个新用例)、`tests/test_checkpoints.py`(追加旧格式兼容用例)

**Interfaces:**
- Consumes: Task 2 的 `SceneMeta.flat_keyframes()`(有效 frame_id 集合来源)
- Produces: `ExtractedEvent.evidence_frame_ids: list[str] = []`、`SceneEvent.evidence_frame_ids: list[str] = []`(parse 后仅含本场景有效 ID);`GraphWriter.ensure_scene` 写 `desc` 参数、`write_extraction` 的 Event MERGE 写 `evf` 参数。旧 checkpoint(无该字段)经 pydantic 默认值兼容加载为 `[]`。

- [ ] **Step 1: 追加 parse 测试**

`tests/test_parse.py` 末尾追加:

```python
SHOT_META = dict(scene_id="scene_003", movie_id="m1", start_ts="00:00:00",
                 end_ts="00:02:00", characters=ROSTER, dialogues=[], location="x",
                 virtual_time="y",
                 shots=[{"shot_id": "s1", "start_ts": "00:00:00", "end_ts": "00:02:00",
                         "keyframes": [{"frame_id": "frame_001", "ts": "00:00:01"},
                                       {"frame_id": "frame_002", "ts": "00:00:02"}]}])


def test_parse_a1_filters_invalid_evidence(caplog):
    meta = SceneMeta.model_validate(SHOT_META)
    raw = _a1_raw()
    raw["events"][0]["evidence_frame_ids"] = ["frame_001", "frame_999"]
    raw["events"][0]["subevents"][0]["evidence_frame_ids"] = ["frame_002"]
    with caplog.at_level(logging.WARNING, logger="movie_kg.extraction.parse"):
        events = parse_a1(raw, meta)
    assert events[0].evidence_frame_ids == ["frame_001"]
    assert events[1].evidence_frame_ids == ["frame_002"]
    assert "frame_999" in caplog.text


def test_parse_a1_without_evidence_defaults_empty():
    events = parse_a1(_a1_raw(), META)
    assert all(e.evidence_frame_ids == [] for e in events)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_parse.py -v`
Expected: 新用例 FAIL(`SceneEvent` 无 `evidence_frame_ids` 属性 / ExtractedEvent 拒绝未知字段后的过滤逻辑不存在——实际是 AttributeError)

- [ ] **Step 3: 实现 models/extraction.py 与 parse.py**

`models/extraction.py`:`ExtractedEvent` 在 `description` 后加一行、`SceneEvent` 在 `participants` 前加一行:

```python
class ExtractedEvent(BaseModel):
    event_type: Literal["dialogue", "conflict", "action", "turning_point", "discovery"]
    topic: str
    description: str
    evidence_frame_ids: list[str] = []
    participating_characters: list[Participant] = []
    subevents: list["ExtractedEvent"] = []
```

```python
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
    evidence_frame_ids: list[str] = []
    participants: list[Participant] = []
```

`parse.py` 的 `parse_a1`:循环前计算有效集合,`make()` 内过滤并告警:

```python
def parse_a1(raw: dict, meta: SceneMeta) -> list[SceneEvent]:
    """A1 原始输出 -> 扁平化 SceneEvent 列表(ID 确定性分配,子事件紧随父事件)。"""
    out = A1Output.model_validate(raw)
    seq = int(meta.scene_id.rsplit("_", 1)[1])
    valid_ids = {k.frame_id for k in meta.flat_keyframes()}
    events: list[SceneEvent] = []
    counter = 0

    def make(ev: ExtractedEvent, parent_id: str | None) -> SceneEvent:
        nonlocal counter
        counter += 1
        invalid = [fid for fid in ev.evidence_frame_ids if fid not in valid_ids]
        if invalid:
            logger.warning("场景 %s: 事件「%s」证据帧引用无效,已剔除: %s",
                           meta.scene_id, ev.topic, ", ".join(invalid))
        evidence = [fid for fid in ev.evidence_frame_ids if fid in valid_ids]
        return SceneEvent(
            id=f"evt_{meta.movie_id}_{seq:03d}_{counter:02d}",
            event_type=ev.event_type, topic=ev.topic, description=ev.description,
            scene_id=meta.scene_id, movie_id=meta.movie_id, order=counter,
            is_subevent=parent_id is not None, parent_id=parent_id,
            evidence_frame_ids=evidence,
            participants=[Participant.model_validate(
                p.model_copy(update={"name": align_name(p.name, meta.characters)}))
                for p in ev.participating_characters])
```

(`for ev in out.events:` 及之后不变。)

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_parse.py -v`
Expected: PASS(含既有 6 项 + 新 2 项)

- [ ] **Step 5: 追加 writer 与 checkpoint 测试**

`tests/test_writer.py`:META 构造加 `description="码头对峙"`(在 `virtual_time="第一天傍晚"` 后加一行 `description="码头对峙",`),文件末尾追加:

```python
def test_scene_description_written():
    s = MockSession()
    _writer(s)  # _writer 内部调用 ensure_scene(META)
    q, p = next((q, p) for q, p in s.queries if "MERGE (s:Scene" in q)
    assert "s.description = $desc" in q and p["desc"] == "码头对峙"


def test_event_evidence_written():
    s = MockSession()
    from movie_kg.models.extraction import SceneExtraction
    ev = EVENTS[0].model_copy(update={"evidence_frame_ids": ["frame_001"]})
    ext = SceneExtraction(scene_id="scene_001", movie_id="m1", status="ok",
                          events=[ev])
    _writer(s).write_extraction(ext, META)
    q, p = next((q, p) for q, p in s.queries if "MERGE (e:Event" in q)
    assert "e.evidence_frame_ids = $evf" in q and p["evf"] == ["frame_001"]
```

`tests/test_checkpoints.py` 末尾追加(旧格式 checkpoint 兼容——pydantic 默认值兜底):

```python
def test_load_old_checkpoint_without_evidence(tmp_path):
    import json
    from movie_kg.extraction.checkpoints import CheckpointStore
    store = CheckpointStore(tmp_path)
    old = {"scene_id": "scene_001", "movie_id": "m1", "status": "ok",
           "events": [{"id": "evt_m1_001_01", "event_type": "action", "topic": "t",
                       "description": "d", "scene_id": "scene_001", "movie_id": "m1",
                       "order": 1}], "relations": []}
    store.path("scene_001").write_text(json.dumps(old), encoding="utf-8")
    ext = store.load("scene_001")
    assert ext is not None and ext.status == "ok"
    assert ext.events[0].evidence_frame_ids == []
```

- [ ] **Step 6: 跑测试确认失败**

Run: `python -m pytest tests/test_writer.py tests/test_checkpoints.py -v`
Expected: 新 writer 用例 FAIL(`s.description = $desc` / `e.evidence_frame_ids = $evf` 不在查询中);checkpoint 用例可能已 PASS(pydantic 默认值),若 PASS 属正常——它钉的是兼容行为。

- [ ] **Step 7: 实现 writer.py**

`ensure_scene` 替换为:

```python
    def ensure_scene(self, meta: SceneMeta) -> None:
        seq = int(meta.scene_id.rsplit("_", 1)[1])
        self.session.run(
            "MERGE (s:Scene {id: $id}) SET s.seq = $seq, s.start_ts = $start, "
            "s.end_ts = $end, s.annotated_location = $loc, s.annotated_time = $vt, "
            "s.description = $desc, s.movie_id = $movie",
            id=meta.scene_id, seq=seq, start=meta.start_ts, end=meta.end_ts,
            loc=meta.location, vt=meta.virtual_time, desc=meta.description,
            movie=meta.movie_id)
        self.session.run(
            "MATCH (s:Scene {id: $sid}), (m:Movie {id: $mid}) "
            "MERGE (s)-[:BELONGS_TO]->(m)", sid=meta.scene_id, mid=meta.movie_id)
```

`write_extraction` 中 Event 的 MERGE 语句与参数替换为:

```python
            self.session.run(
                "MERGE (e:Event {id: $id}) SET e.event_type = $type, e.topic = $topic, "
                "e.description = $desc, e.scene_id = $sid, e.order = $order, "
                "e.global_order = $global_order, e.is_subevent = $sub, "
                "e.evidence_frame_ids = $evf, e.movie_id = $movie_id",
                id=ev.id, type=ev.event_type, topic=ev.topic, desc=ev.description,
                sid=ev.scene_id, order=ev.order, global_order=go, sub=ev.is_subevent,
                evf=ev.evidence_frame_ids, movie_id=ev.movie_id)
```

- [ ] **Step 8: 跑测试确认通过 + 全量回归**

Run: `python -m pytest tests/test_writer.py tests/test_checkpoints.py -v` → PASS
Run: `python -m pytest` → 全绿(e2e 的 A1 fixture 无 evidence 字段,走默认 `[]`)

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: evidence_frame_ids through A1 output, parse filtering and graph write; Scene.description"
```

---

### Task 4: A1 全面 grounding —— prompt 模板完整版与图文交错消息体

**Files:**
- Modify: `config/prompts/event_extraction.md`(整文件替换)
- Modify: `src/movie_kg/extraction/event_extractor.py`(整文件替换)
- Test: `tests/test_event_extractor.py`(追加 2 个用例)、`tests/test_prompts.py`(追加断言)

**Interfaces:**
- Consumes: Task 2 的 `Frame.keyframe`、`SceneMeta.description/props/shots`;Task 1 的 `fmt_ts`
- Produces: 最终版 prompt 占位符集合 `ROSTER / LOCATION / TIME / SCENE_DESC / PROPS / DIALOGUES / SHOTS`(与 DESIGN §5.2 模板一致);消息体结构 = `[引导文本] + (帧配文 text, image_url)*`(按时间戳升序交错);`_frame_caption(Frame) -> str`、`_shots_text/_scene_desc/_props_text` 渲染函数

- [ ] **Step 1: 追加测试**

`tests/test_event_extractor.py` 顶部 import 区改:

```python
from movie_kg.models.scene import KeyframeMeta, MovieData
```

文件末尾追加:

```python
def test_prompt_contains_grounding_priors(tmp_path):
    from tests.fakes import ScriptedLLM
    llm = ScriptedLLM(A1)
    movie = _movie(tmp_path)
    frames = [Frame(ts=1.0, jpeg=b"\xff\xd8fake",
                     keyframe=KeyframeMeta(frame_id="frame_001", ts="00:00:01",
                                           frame_type="特写",
                                           description="按在枪上"))]
    extract_scene_events(llm, frames, movie.scenes[0])
    system = llm.calls[0][1][0]["content"]
    assert "场景描述: 张三与李四在仓库对峙。" in system
    assert "道具: 枪" in system
    assert "shot_001 [00:00:00–00:00:01] 中景: 两人隔桌对坐" in system
    assert "00:00:01 张三(愤怒/拍桌): 你到底想怎样?" in system
    user = llm.calls[0][1][-1]["content"]
    texts = [p["text"] for p in user if p.get("type") == "text"]
    assert texts[0] == "请分析以下帧序列与台词,输出 JSON。"
    assert "帧 frame_001 (00:00:01 特写): 按在枪上" in texts
    types = [p["type"] for p in user]
    assert types.index("text") < types.index("image_url")  # 图文交错


def test_fill_frame_marked(tmp_path):
    from tests.fakes import ScriptedLLM
    llm = ScriptedLLM(A1)
    movie = _movie(tmp_path)
    extract_scene_events(llm, [Frame(ts=0.5, jpeg=b"x")], movie.scenes[0])
    texts = [p["text"] for p in llm.calls[0][1][-1]["content"]
             if p.get("type") == "text"]
    assert "[补充帧 00:00:00]" in texts
```

`tests/test_prompts.py` 的 `test_load_prompt_reads_file` 追加一行断言:

```python
    assert "<<SHOTS>>" in text and "<<SCENE_DESC>>" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_event_extractor.py tests/test_prompts.py -v`
Expected: 新用例 FAIL(无 SCENE_DESC/SHOTS 占位符、无帧配文、无补充帧标记)

- [ ] **Step 3: 替换 prompt 模板**

`config/prompts/event_extraction.md` 整文件替换为(与 DESIGN §5.2 一致):

```markdown
你是影视剧剧情分析专家。分析以下场景的帧序列与台词,提取剧情事件。

场景先验: 角色名单=<<ROSTER>>, 标注地点=<<LOCATION>>, 标注时间=<<TIME>>
场景描述: <<SCENE_DESC>>
道具: <<PROPS>>
台词:
<<DIALOGUES>>
镜头时间轴:
<<SHOTS>>

要求:
- 事件按发生顺序排列;子事件同样输出完整结构,不输出纯文本描述
- 子事件不再嵌套子事件,其 subevents 保持为空数组
- 角色名必须从角色名单中选择;无法确定时标记 "未识别:原名"
- event_type ∈ dialogue / conflict / action / turning_point / discovery
- 标注描述(场景/镜头/关键帧)仅为辅助线索,判断以画面与台词为准
- 每个事件与子事件给出 evidence_frame_ids: 支撑该事件的关键帧 ID,只能引用本场景出现过的 frame_id;无法确定时给空数组

输出 JSON:
{"events": [{"event_type": "...", "topic": "...", "description": "...",
  "evidence_frame_ids": ["..."],
  "participating_characters": [{"name": "...", "emotion": "...", "role_description": "...", "items": ["..."]}],
  "subevents": [{"event_type": "...", "topic": "...", "description": "...",
   "evidence_frame_ids": [], "participating_characters": []}]}]}
```

- [ ] **Step 4: 替换 event_extractor.py**

```python
import base64
import json

from movie_kg.extraction.parse import parse_a1
from movie_kg.ingestion.frame_sampler import Frame
from movie_kg.ingestion.timefmt import fmt_ts
from movie_kg.models.extraction import A1Output
from movie_kg.models.scene import SceneData
from movie_kg.prompts import load_prompt, render


def _roster_text(scene: SceneData) -> str:
    return "; ".join(
        f"{c.name}({','.join(c.aliases)})" if c.aliases else c.name
        for c in scene.meta.characters)


def _dialogue_text(scene: SceneData) -> str:
    lines = []
    for d in scene.meta.dialogues:
        cue = f"({d.emotion}/{d.action})" if d.emotion or d.action else ""
        lines.append(f"{d.ts} {d.char}{cue}: {d.text}")
    return "\n".join(lines) if lines else "(无台词)"


def _scene_desc(scene: SceneData) -> str:
    return scene.meta.description or "(无)"


def _props_text(scene: SceneData) -> str:
    return ", ".join(scene.meta.props) if scene.meta.props else "(无)"


def _shots_text(scene: SceneData) -> str:
    lines = []
    for sh in scene.meta.shots:
        t = f" {sh.shot_type}" if sh.shot_type else ""
        d = f": {sh.description}" if sh.description else ""
        lines.append(f"{sh.shot_id} [{sh.start_ts}–{sh.end_ts}]{t}{d}")
    return "\n".join(lines) if lines else "(无镜头标注)"


def _frame_caption(f: Frame) -> str:
    if f.keyframe is None:
        return f"[补充帧 {fmt_ts(f.ts)}]"
    k = f.keyframe
    t = f" {k.frame_type}" if k.frame_type else ""
    d = f": {k.description}" if k.description else ""
    return f"帧 {k.frame_id} ({fmt_ts(f.ts)}{t}){d}"


def extract_scene_events(llm, frames: list[Frame], scene: SceneData) -> list:
    """A1: 帧(图文交错)+ 元数据台词 + 先验 -> 结构化事件(LLMError 向上抛)。"""
    system = render(load_prompt("event_extraction"),
                    ROSTER=_roster_text(scene),
                    LOCATION=scene.meta.location,
                    TIME=scene.meta.virtual_time,
                    SCENE_DESC=_scene_desc(scene),
                    PROPS=_props_text(scene),
                    DIALOGUES=_dialogue_text(scene),
                    SHOTS=_shots_text(scene))
    content: list[dict] = [{"type": "text", "text": "请分析以下帧序列与台词,输出 JSON。"}]
    for f in frames:
        b64 = base64.b64encode(f.jpeg).decode("ascii")
        content.append({"type": "text", "text": _frame_caption(f)})
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]
    cfg = getattr(llm, "cfg", None)
    model = cfg.vision_model if cfg is not None else "vision"
    raw = llm.chat_json(model=model, messages=messages, schema=A1Output)
    return parse_a1(json.loads(raw.model_dump_json()), scene.meta)
```

- [ ] **Step 5: 跑测试确认通过 + 全量回归**

Run: `python -m pytest tests/test_event_extractor.py tests/test_prompts.py -v` → PASS
Run: `python -m pytest` → 全绿(Task 2 的台词断言在新模板下仍成立:`<<DIALOGUES>>` 渲染逻辑未变)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: full A1 grounding prompt (scene desc/props/shot timeline, interleaved frame captions)"
```

---

## 执行备注(给 controller/评审)

- Task 2 是契约整体切换:红→绿跨越 models/loader/sampler/extractor/prompt 五处,属预期;单测文件不得分批提交,一个提交收口。
- `test_e2e.py` / `test_cli_build.py` / `test_pipeline_a.py` / `test_writer_integration.py` 不在本计划 Modify 列表——它们经 fixtures 自动适配,若变红说明 fixture 升级有遗漏,按失败信息回修 Task 2 的 fixture/实现,不得在测试里特判。
- 集成测试(`-m integration`)需真实 Neo4j,离线套件不含;若本地有 NEO4J_URI 环境,Task 2/3 完成后各跑一次。
- 合并回 main 后建议对真实数据 `kg build --from-scratch` 全量重建(prompt 与输出 schema 均已变化)。
