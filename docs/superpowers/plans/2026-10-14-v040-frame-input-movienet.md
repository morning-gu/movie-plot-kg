# v0.4.0 帧目录输入与 MovieNet 免片源接入 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 视频输入彻底替换为场景关键帧目录,并新增 MovieNet 离线转换器(fetch/convert),使 MovieNet 试点 3 部(Hackers / The Rock / Reservoir Dogs)免片源跑通 `kg build` 全链路。

**Architecture:** 管线侧只动 ingestion 层(`SceneData.frames_dir`、loader 帧文件校验、`select_frames` 帧选择),A1 之后全链路与 checkpoint 结构不变;MovieNet 侧为独立子包 `ingestion/movienet/`(fetch 取数 + subs/align 文本对齐 + convert 九 stage 转换),convert 产物必须通过 loader 自校验。

**Tech Stack:** Python 3.11+;zip64/tarfile/zlib/difflib/urllib 全部标准库,**零新增第三方依赖**;Pillow 生成测试 JPEG;移除 `av`(PyAV)。

**Spec:** `docs/DESIGN.md` v0.4.0(commit `ac2cdb7`)——§3.1 帧目录、§3.2 顶层 keyframes、§3.3 校验差异、§3.4 转换器九 stage 与精确性声明。本计划是它的实现论证;冲突时以 DESIGN 为准。

## Global Constraints

- Python 3.11+;不引入任何新第三方依赖(取数用 urllib,zip/tar/inflate/对齐用标准库)
- `pyproject.toml` 移除 `av`(Task 1);版本号 `0.4.0`(Task 5)
- 元数据 schema 变更仅限两处:`SceneMeta.keyframes`(顶层,默认 `[]`)与 `SceneData.frames_dir`;其余字段与 v0.3.0 完全一致
- 时间戳一律影片绝对时间 HH:MM:SS(`timefmt.fmt_ts` 整秒截断);帧文件命名 `frames/{frame_id}.jpg`
- MovieNet frame_id 格式:`s{shot:04d}i{img}`(如 `s0277i2`),片内唯一即全局唯一
- 台词两级未匹配(剧本没对上 / 说话人不在 roster)**必须分别计数**进 convert_report
- convert 产物必须通过 `load_movie` 全套校验(stage 9 自校验,失败即报错退出)
- 说话人归 roster 用**全片 roster**(cast 名单并集),解析成功的说话人并入所在场景 characters(cast 标注可能漏人,且保证 loader 的 char 对齐校验)
- 测试全部离线可跑(fetch 的真实 HTTP 不进 CI,试点时人工验收);主仓基线 109 passed + 1 skipped(`b3c3aba`)

## 执行注意(SDD)

- worktree 自建 venv:`python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"`——pyproject 无 pythonpath 配置,复用主仓 venv 会把 `import movie_kg` 解析到主仓的可编辑安装(错误代码)
- 每个 Task 内步骤使用 `python -m pytest`(worktree venv 激活态)
- 试点执行(fetch/convert 3 部 + `kg build --scenes 1-5`)在全部任务完成、终审通过后由控制者执行,不派发子任务

## Review Focus

规范未显式覆盖、最可能咬人的五类输入(每条已钉进所属任务的测试):

1. **顶层 keyframes 与 shot 内嵌 frame_id 跨源重复**——双源拼接后全局查重必须覆盖顶层(Task 1 `test_toplevel_keyframe_duplicate_with_shot_internal`)
2. **帧文件存在但 0 字节**——必须报错而非静默通过(Task 1 `test_empty_frame_file_reports_error`)
3. **SRT cue 起点恰在场景边界**——归起点所在(后一)场景,不重复计入两端(Task 4 `test_dialogue_cue_at_scene_boundary`)
4. **story 镜头插值 keyframe.ts 超出场景窗口**——clamp 保证 §3.3 窗口校验通过(Task 4 `test_keyframe_ts_clamped_into_window`)
5. **剧本对齐单调性**——重复台词不回跳到剧本更早位置误配(Task 3 `test_alignment_monotonic_no_backtrack`)

---

### Task 1: 管线输入改造——帧目录替代视频

**Files:**
- Modify: `src/movie_kg/models/scene.py`
- Modify: `src/movie_kg/ingestion/data_loader.py`(整文件重写)
- Modify: `src/movie_kg/ingestion/frame_sampler.py`(整文件重写)
- Modify: `src/movie_kg/extraction/pipeline.py`(调用点)
- Modify: `pyproject.toml`(删 av)
- Modify: `tests/fixtures.py`(整文件重写)
- Rewrite: `tests/test_data_loader.py`、`tests/test_frame_sampler.py`
- Modify: `tests/test_build_eval.py:35`(SceneData 构造)

**Interfaces:**
- Produces: `SceneData = {meta: SceneMeta, frames_dir: Path}`;`SceneMeta.keyframes: list[KeyframeMeta] = []` 且 `flat_keyframes() = shots 展开 + 顶层拼接`;`select_frames(items: list[tuple[Path, KeyframeMeta]], max_frames: int = 16) -> list[Frame]`;fixtures `make_frame(path, color)` 与帧版 `make_movie_dir`(scene_002 带顶层 `frame_101`)
- `Frame(ts, jpeg, keyframe)` 结构不变(后续 Task 4 / event_extractor 依赖);`extract_scene_events` 零改动(手工构造 `keyframe=None` 帧的既有测试继续有效)

- [ ] **Step 1: 写失败测试(tests/test_frame_sampler.py 整文件替换)**

```python
from movie_kg.ingestion.frame_sampler import select_frames
from movie_kg.models.scene import KeyframeMeta
from tests.fixtures import make_frame


def kf(fid, ts, desc="d"):
    return KeyframeMeta(frame_id=fid, ts=ts, description=desc)


def _items(tmp_path, kfs):
    items = []
    for k in kfs:
        p = tmp_path / f"{k.frame_id}.jpg"
        make_frame(p)
        items.append((p, k))
    return items


def test_returns_all_when_under_budget(tmp_path):
    items = _items(tmp_path, [kf("f1", "00:00:01"), kf("f2", "00:00:02")])
    frames = select_frames(items, max_frames=16)
    assert len(frames) == 2                       # 无补帧:目录里有多少给多少
    assert all(f.keyframe is not None for f in frames)


def test_even_truncation_keeps_both_ends(tmp_path):
    kfs = [kf(f"f{i:02d}", f"00:00:{i:02d}") for i in range(1, 21)]  # 20 帧
    frames = select_frames(_items(tmp_path, kfs), max_frames=6)
    assert len(frames) == 6
    assert frames[0].keyframe.frame_id == "f01"   # 首帧保留
    assert frames[-1].keyframe.frame_id == "f20"  # 尾帧保留(而非取前 6)


def test_output_sorted_by_ts(tmp_path):
    kfs = [kf("f3", "00:00:30"), kf("f1", "00:00:10"), kf("f2", "00:00:20")]
    frames = select_frames(_items(tmp_path, kfs), max_frames=16)
    assert [f.keyframe.frame_id for f in frames] == ["f1", "f2", "f3"]


def test_frame_bytes_and_absolute_ts_loaded(tmp_path):
    items = _items(tmp_path, [kf("f1", "00:10:01")])
    frames = select_frames(items, max_frames=16)
    assert frames[0].jpeg[:2] == b"\xff\xd8"      # JPEG magic
    assert abs(frames[0].ts - 601.0) < 1e-9        # 影片绝对秒,无偏移换算
```

- [ ] **Step 2: 写失败测试(tests/test_data_loader.py 整文件替换)**

```python
import json

import pytest

from movie_kg.ingestion.data_loader import InputValidationError, load_movie
from tests.fixtures import make_movie_dir


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
    assert s1.frames_dir == movie_dir / "frames"
    assert [k.frame_id for k in s1.meta.flat_keyframes()] == ["frame_001"]
    assert s1.meta.shots[0].shot_type == "中景"
    assert s1.meta.description == "张三与李四在仓库对峙。"
    s2 = movie.scenes[1]
    assert [k.frame_id for k in s2.meta.flat_keyframes()] == ["frame_101"]  # 顶层帧


def test_missing_frame_file_reports_error(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    (movie_dir / "frames" / "frame_001.jpg").unlink()
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("缺少帧文件" in msg for msg in e.value.errors)


def test_empty_frame_file_reports_error(tmp_path):
    movie_dir = make_movie_dir(tmp_path)
    (movie_dir / "frames" / "frame_101.jpg").write_bytes(b"")
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("帧文件为空" in msg for msg in e.value.errors)


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
    movie_dir = make_movie_dir(tmp_path)
    _edit_scene(movie_dir, "scene_001",
                lambda m: m["shots"][0]["keyframes"][0].update(ts="00:00:05"))
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("关键帧越界" in msg for msg in e.value.errors)


def test_toplevel_keyframe_ts_outside_window(tmp_path):
    # 顶层帧与 shot 内嵌帧同规则:越界必须报错(Review Focus #1 同源校验)
    movie_dir = make_movie_dir(tmp_path)
    _edit_scene(movie_dir, "scene_002",
                lambda m: m["keyframes"][0].update(ts="00:09:00"))
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


def test_toplevel_keyframe_duplicate_with_shot_internal(tmp_path):
    # Review Focus #1:顶层与 shot 内嵌双源拼接后,frame_id 查重必须覆盖顶层
    movie_dir = make_movie_dir(tmp_path)
    _edit_scene(movie_dir, "scene_001",
                lambda m: m.update(keyframes=[
                    {"frame_id": "frame_001", "ts": "00:00:01"}]))
    with pytest.raises(InputValidationError) as e:
        load_movie(movie_dir)
    assert any("frame_id 重复" in msg for msg in e.value.errors)
```

- [ ] **Step 3: 运行确认失败**

Run: `python -m pytest tests/test_frame_sampler.py tests/test_data_loader.py -v`
Expected: FAIL(ImportError: 无法导入 select_frames / make_frame;SceneData 无 frames_dir)

- [ ] **Step 4: 实现 models/scene.py(Edit)**

`ShotMeta` 之后、`SceneMeta` 内加顶层字段并改 `flat_keyframes`:

```python
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
    keyframes: list[KeyframeMeta] = []  # v0.4.0: 顶层(无镜头归属)帧,MovieNet 模式

    def flat_keyframes(self) -> list[KeyframeMeta]:
        """shot 内嵌帧按序展开,再拼接顶层帧(采样与证据校验的基准列表)。"""
        return [k for sh in self.shots for k in sh.keyframes] + list(self.keyframes)
```

`SceneData` 整体替换:

```python
class SceneData(BaseModel):
    meta: SceneMeta
    frames_dir: Path
```

- [ ] **Step 5: 实现 data_loader.py(整文件替换)**

```python
import json
from pathlib import Path

from movie_kg.ingestion.timefmt import parse_ts
from movie_kg.models.scene import KeyframeMeta, MovieData, SceneData, SceneMeta


class InputValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _check_keyframe(k: KeyframeMeta, scene_id: str, start: float, end: float,
                    seen_frames: set[str], errors: list[str]) -> None:
    """顶层与 shot 内嵌关键帧共用的查重 + 窗口校验(DESIGN §3.2/§3.3)。"""
    if k.frame_id in seen_frames:
        errors.append(f"{scene_id}: frame_id 重复({k.frame_id})")
    seen_frames.add(k.frame_id)
    if not (start <= parse_ts(k.ts) <= end):
        errors.append(f"{scene_id}: 关键帧越界({k.frame_id}: {k.ts})")


def load_movie(movie_dir: str | Path) -> MovieData:
    movie_dir = Path(movie_dir)
    errors: list[str] = []
    movie_id = movie_dir.name
    info = json.loads((movie_dir / "movie.json").read_text(encoding="utf-8"))
    scene_files = sorted((movie_dir / "scenes").glob("scene_*.json"))
    frames_dir = movie_dir / "frames"
    scenes: list[SceneData] = []
    prev_end = -1.0
    seen_shots: set[str] = set()
    seen_frames: set[str] = set()
    for sf in scene_files:
        meta = SceneMeta.model_validate_json(sf.read_text(encoding="utf-8"))
        if meta.movie_id != movie_id:
            errors.append(f"{sf.name}: movie_id={meta.movie_id} 与目录 {movie_id} 不一致")
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
                _check_keyframe(k, meta.scene_id, start, end, seen_frames, errors)
        for k in meta.keyframes:  # v0.4.0 顶层帧,同规则
            _check_keyframe(k, meta.scene_id, start, end, seen_frames, errors)
        intervals = sorted((parse_ts(sh.start_ts), parse_ts(sh.end_ts), sh.shot_id)
                           for sh in meta.shots)
        for (_, e0, _), (s1, _, id1) in zip(intervals, intervals[1:]):
            if s1 < e0:
                errors.append(f"{meta.scene_id}: 镜头重叠({id1})")
        for k in meta.flat_keyframes():
            fp = frames_dir / f"{k.frame_id}.jpg"
            if not fp.exists():
                errors.append(f"{meta.scene_id}: 缺少帧文件({fp.name})")
            elif fp.stat().st_size == 0:
                errors.append(f"{meta.scene_id}: 帧文件为空({fp.name})")
        scenes.append(SceneData(meta=meta, frames_dir=frames_dir))
    if errors:
        raise InputValidationError(errors)
    return MovieData(movie_id=movie_id, title=info.get("title", movie_id), scenes=scenes)
```

- [ ] **Step 6: 实现 frame_sampler.py(整文件替换)**

```python
from dataclasses import dataclass
from pathlib import Path

from movie_kg.ingestion.timefmt import parse_ts
from movie_kg.models.scene import KeyframeMeta


@dataclass
class Frame:
    ts: float                             # 影片绝对时间(秒)
    jpeg: bytes
    keyframe: KeyframeMeta | None = None  # 目录输入下恒为标注帧(None 保留给手工构造)


def _even_subset(n: int, k: int) -> list[int]:
    """从 n 个元素中取均匀分布的 k 个下标(含首尾)。"""
    if k >= n:
        return list(range(n))
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def select_frames(items: list[tuple[Path, KeyframeMeta]],
                  max_frames: int = 16) -> list[Frame]:
    """从关键帧文件列表选择 ≤max_frames 帧:按 ts 排序,超上限均匀截断(保留首尾)。
    v0.4.0 无视频采样与补帧——帧文件数即该场景视觉信息上界(DESIGN §3.1)。"""
    ordered = sorted(items, key=lambda it: parse_ts(it[1].ts))
    if len(ordered) > max_frames:
        ordered = [ordered[i] for i in _even_subset(len(ordered), max_frames)]
    return [Frame(ts=parse_ts(k.ts), jpeg=path.read_bytes(), keyframe=k)
            for path, k in ordered]
```

- [ ] **Step 7: 改 pipeline.py 调用点(两处 Edit)**

import 区——旧:

```python
from movie_kg.ingestion.frame_sampler import sample_frames
from movie_kg.ingestion.timefmt import parse_ts
```

新(`parse_ts` 不再使用,删除):

```python
from movie_kg.ingestion.frame_sampler import select_frames
```

调用点——旧:

```python
            frames = sample_frames(scene.video_path, scene.meta.flat_keyframes(),
                                   scene_start=parse_ts(scene.meta.start_ts),
                                   max_frames=max_frames)
```

新:

```python
            items = [(scene.frames_dir / f"{k.frame_id}.jpg", k)
                     for k in scene.meta.flat_keyframes()]
            frames = select_frames(items, max_frames=max_frames)
```

- [ ] **Step 8: 重写 tests/fixtures.py(整文件替换)**

```python
import json
from pathlib import Path

from PIL import Image


def make_frame(path: Path, color: tuple = (200, 30, 30)) -> None:
    """写一个 4x4 纯色 JPEG(v0.4.0 视觉输入为帧文件,无视频合成)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), color).save(path, format="JPEG")


def make_movie_dir(tmp_path: Path, n_scenes: int = 2) -> Path:
    """构造完整的单部电影输入目录(帧目录/元数据),返回目录路径。"""
    movie_dir = tmp_path / "data" / "movies" / "m1"
    for sub in ("frames", "scenes", "checkpoints"):
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
        if i == 0:  # scene_001 带镜头/shot 内嵌标注帧
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
        else:       # 其余场景用顶层 keyframes(v0.4.0 双源之一)
            m["keyframes"] = [{"frame_id": "frame_101", "ts": dlg_ts}]
        (movie_dir / "scenes" / f"{sid}.json").write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8")
    make_frame(movie_dir / "frames" / "frame_001.jpg")
    make_frame(movie_dir / "frames" / "frame_101.jpg", color=(30, 30, 200))
    return movie_dir
```

- [ ] **Step 9: 修 test_build_eval.py:35**

`SceneData(meta=META, video_path="v.mp4")` → `SceneData(meta=META, frames_dir=Path("frames"))`(确认文件头已 `from pathlib import Path`,缺则补)。

- [ ] **Step 10: pyproject.toml 删 av**

dependencies 中删除 `"av>=12.0.0",` 一行。

- [ ] **Step 11: 运行目标测试**

Run: `python -m pytest tests/test_frame_sampler.py tests/test_data_loader.py tests/test_build_eval.py -v`
Expected: 全部 PASS

- [ ] **Step 12: 全量回归**

Run: `python -m pytest`
Expected: 全绿(视频专用的 duration_mismatch / unopenable_video / uniform_sampling / fill_yield 等用例已随重写移除,其余 109 项语义保留)

- [ ] **Step 13: Commit**

```bash
git add -A
git commit -m "feat: 帧目录替代视频输入(顶层 keyframes、select_frames、移除 PyAV)"
```

---

### Task 2: MovieNet 取数(fetch)

**Files:**
- Create: `src/movie_kg/ingestion/movienet/__init__.py`(空文件)
- Create: `src/movie_kg/ingestion/movienet/fetch.py`
- Modify: `src/movie_kg/cli.py`(注册 movienet 命令组 + fetch 命令)
- Test: `tests/test_movienet_fetch.py`

**Interfaces:**
- Consumes: 无(标准库自足)
- Produces(Task 4 依赖): `fetch_base(src: Path) -> Path`;`extract_movie_files(pkg: Path, movie: str, src: Path) -> None`(缺 script 跳过并打印告警;缺 annotation/subtitle/meta 抛 RuntimeError);`read_zip_index(cache: Path) -> dict[str, dict]`;`parse_central_dir(buf: bytes) -> dict[str, dict]`(纯函数);`fetch_keyframes(movie: str, src: Path) -> Path`
- 源文件布局约定(Task 4 消费): `<src>/annotation/{tt}.json`、`<src>/subtitle/{tt}.srt`、`<src>/script/{tt}.script`、`<src>/meta/{tt}.json`、`<src>/keyframes/{tt}.tar`、缓存 `<src>/kf_zip_index.json`

- [ ] **Step 1: 写失败测试(tests/test_movienet_fetch.py)**

```python
import io
import json
import struct
import tarfile
import zipfile

import pytest

from movie_kg.ingestion.movienet.fetch import (
    extract_movie_files, parse_central_dir)


def _cd_entry(name: bytes, extra: bytes) -> bytes:
    return (struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50,
                        45, 45, 0, 8, 0, 0, 0,
                        0, 0)
            + struct.pack("<HHH", len(name), len(extra), 0)
            + struct.pack("<HI", 0, 0xFFFFFFFF)
            + name + extra)


def test_parse_central_dir_reads_zip64_extra():
    # 大文件的尺寸/偏移在 zip64 extra 中,基础字段是 0xFFFFFFFF 占位符
    extra = struct.pack("<HHQQQ", 1, 24, 1000, 1100, 0xFFFFFFF0)
    buf = (_cd_entry(b"240P/tt0000001.tar", extra)
           + _cd_entry(b"240P/tt0000002.tar", b"") + b"PK\x05\x06" + b"\0" * 18)
    idx = parse_central_dir(buf)
    assert set(idx) == {"240P/tt0000001.tar", "240P/tt0000002.tar"}
    e = idx["240P/tt0000001.tar"]
    assert e == {"usize": 1000, "csize": 1100, "lho": 0xFFFFFFF0}


def _zbytes(mapping: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in mapping.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_base_pkg(path, with_meta=True):
    zips = {
        "files/annotation.v1.zip": _zbytes(
            {"annotation/annotation/tt0000001.json":
             json.dumps({"imdb_id": "tt0000001"})}),
        "files/subtitle1K.v1.zip": _zbytes(
            {"subtitle/subtitle/tt0000001.srt":
             "1\n00:00:00,500 --> 00:00:02,000\nhi\n"}),
        "files/script1K.v1.zip": _zbytes(
            {"script/script/tt0000001.script": "INT. OFFICE\n\n   ALICE\n hi\n"}),
        "files/poster4M.meta.v1.json": b"{}",
    }
    if with_meta:
        zips["files/meta.v1.zip"] = _zbytes(
            {"meta/tt0000001.json": json.dumps({"title": "Mini"})})
    with tarfile.open(path, "w:gz") as tf:
        for member, data in zips.items():
            ti = tarfile.TarInfo(member)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))


def test_extract_movie_files_selective(tmp_path):
    pkg = tmp_path / "MovieNet.tar.gz"
    _make_base_pkg(pkg)
    src = tmp_path / "src"
    extract_movie_files(pkg, "tt0000001", src)
    assert json.loads((src / "annotation" / "tt0000001.json").read_text())[
        "imdb_id"] == "tt0000001"
    assert (src / "subtitle" / "tt0000001.srt").exists()
    assert (src / "script" / "tt0000001.script").exists()
    assert json.loads((src / "meta" / "tt0000001.json").read_text())[
        "title"] == "Mini"
    assert not list(src.rglob("poster*"))            # poster4M 不落地


def test_extract_movie_files_requires_core_zips(tmp_path):
    # annotation/subtitle/meta 缺一即报错;script 缺失仅告警(479/815 部有剧本)
    pkg = tmp_path / "MovieNet.tar.gz"
    _make_base_pkg(pkg, with_meta=False)
    with pytest.raises(RuntimeError):
        extract_movie_files(pkg, "tt0000001", tmp_path / "src")


def test_extract_movie_files_skips_existing(tmp_path):
    pkg = tmp_path / "MovieNet.tar.gz"
    _make_base_pkg(pkg)
    src = tmp_path / "src"
    extract_movie_files(pkg, "tt0000001", src)
    (src / "annotation" / "tt0000001.json").write_text('{"imdb_id": "kept"}')
    extract_movie_files(pkg, "tt0000001", src)   # 幂等:已存在不覆盖
    assert "kept" in (src / "annotation" / "tt0000001.json").read_text()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_movienet_fetch.py -v`
Expected: FAIL(ModuleNotFoundError: movie_kg.ingestion.movienet)

- [ ] **Step 3: 实现 fetch.py**

```python
"""MovieNet 取数: OpenDataLab 匿名直链 + HTTP Range 按片下载(DESIGN §3.4)。

网络层(urlopen/Range/重试)不进单测,试点时人工验收;
解析层(parse_central_dir / extract_movie_files)为纯函数,离线可测。
"""
import io
import json
import struct
import tarfile
import time
import urllib.request
import zipfile
import zlib
from pathlib import Path

BASE_URL = "https://openxlab.org.cn/datasets/resolve/12/main"
PKG_NAME = "MovieNet.tar.gz"
PKG_SHA256 = "3fb250aa665b8cda46dd1f5291fa637f9a9083e860a0f191946e6d45c4b3e76e"
KF_ZIP_NAME = "movie1K.keyframes.240p.v1.zip"
RETRIES = 6
PAUSE = 3.0


def _open(url: str, headers: dict | None = None, timeout: int = 60):
    req = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout)


def _resolve(path: str) -> str:
    """resolve 端点 302 → 签名直链(匿名可用)。"""
    last: Exception | None = None
    for _ in range(RETRIES):
        try:
            with _open(f"{BASE_URL}/{path}") as resp:
                return resp.url
        except Exception as e:  # noqa: BLE001 - 网络抖动统一重试
            last = e
            time.sleep(PAUSE)
    raise RuntimeError(f"解析下载地址失败({path}): {last}")


def _range_get(url: str, start: int, end: int) -> bytes:
    last: Exception | None = None
    for _ in range(RETRIES):
        try:
            with _open(url, headers={"Range": f"bytes={start}-{end}"}) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(PAUSE)
    raise RuntimeError(f"Range 下载失败({start}-{end}): {last}")


def _remote_size(url: str) -> int:
    """1 字节 Range 探测总长(Content-Range: bytes 0-0/TOTAL)。"""
    with _open(url, headers={"Range": "bytes=0-0"}) as resp:
        cr = resp.headers.get("Content-Range", "")
        return int(cr.rsplit("/", 1)[1])


def download_file(path: str, target: Path, chunk_mb: int = 8) -> None:
    """断点续传下载 repo 文件(按 Content-Range 校验总长)。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    url = _resolve(path)
    expected = _remote_size(url)
    got = target.stat().st_size if target.exists() else 0
    if got > expected:
        raise RuntimeError(f"{target} 比远端大({got}>{expected}),请删除后重试")
    while got < expected:
        end = min(got + (chunk_mb << 20), expected) - 1
        data = _range_get(url, got, end)
        if not data:
            url = _resolve(path)  # 直链可能过期
            continue
        with open(target, "ab") as f:
            f.write(data)
        got += len(data)


def fetch_base(src: Path) -> Path:
    """下载基础包(887MB,一次性),sha256 校验。"""
    import hashlib
    target = src / PKG_NAME
    if target.exists() and target.stat().st_size > 0:
        return target
    download_file(f"raw/{PKG_NAME}", target)
    h = hashlib.sha256(target.read_bytes()).hexdigest()
    if h != PKG_SHA256:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"{PKG_NAME} sha256 不符(远端可能已更新): {h}")
    return target


def parse_central_dir(buf: bytes) -> dict[str, dict]:
    """zip 中央目录 → {tar 名: {usize, csize, lho}}(含 zip64 extra 解析)。"""
    entries: dict[str, dict] = {}
    pos = 0
    while pos < len(buf) - 46 and buf[pos:pos + 4] == b"PK\x01\x02":
        nlen, elen, clen = struct.unpack("<HHH", buf[pos + 28:pos + 34])
        csize = struct.unpack("<I", buf[pos + 20:pos + 24])[0]
        usize = struct.unpack("<I", buf[pos + 24:pos + 28])[0]
        lho = struct.unpack("<I", buf[pos + 42:pos + 46])[0]
        name = buf[pos + 46:pos + 46 + nlen].decode("utf-8")
        extra = buf[pos + 46 + nlen:pos + 46 + nlen + elen]
        e = 0
        while e + 4 <= len(extra):
            hid, hsz = struct.unpack("<HH", extra[e:e + 4])
            if hid == 1:  # zip64: 按占位符顺序 usize → csize → lho
                f = e + 4
                if usize == 0xFFFFFFFF:
                    usize = struct.unpack("<Q", extra[f:f + 8])[0]
                    f += 8
                if csize == 0xFFFFFFFF:
                    csize = struct.unpack("<Q", extra[f:f + 8])[0]
                    f += 8
                if lho == 0xFFFFFFFF:
                    lho = struct.unpack("<Q", extra[f:f + 8])[0]
            e += 4 + hsz
        if name.endswith(".tar"):
            entries[name] = {"usize": usize, "csize": csize, "lho": lho}
        pos += 46 + nlen + elen + clen
    return entries


def read_zip_index(cache: Path) -> dict[str, dict]:
    """读关键帧 zip 的中央目录(110KB)并缓存到本地。"""
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    cache.parent.mkdir(parents=True, exist_ok=True)
    url = _resolve(f"raw/{KF_ZIP_NAME}")
    size = _remote_size(url)
    tail = _range_get(url, size - 2048, size - 1)
    i = tail.rfind(b"PK\x06\x07")  # ZIP64 EOCD locator
    if i < 0:
        raise RuntimeError("关键帧包缺少 ZIP64 EOCD locator")
    eocd_off = struct.unpack("<Q", tail[i + 8:i + 16])[0]
    head = _range_get(url, eocd_off, eocd_off + 55)
    if head[:4] != b"PK\x06\x06":
        raise RuntimeError("关键帧包缺少 ZIP64 EOCD")
    cd_off = struct.unpack("<Q", head[48:56])[0]
    cd_size = struct.unpack("<Q", head[40:48])[0]
    entries = parse_central_dir(_range_get(url, cd_off, cd_off + cd_size - 1))
    if not entries:
        raise RuntimeError("关键帧包索引为空")
    cache.write_text(json.dumps(entries), encoding="utf-8")
    return entries


def fetch_keyframes(movie: str, src: Path) -> Path:
    """按片下载关键帧 tar(Range 拉取 deflate 流 → 流式解压,原子落盘)。"""
    target = src / "keyframes" / f"{movie}.tar"
    if target.exists() and target.stat().st_size > 0:
        return target
    index = read_zip_index(src / "kf_zip_index.json")
    key = f"240P/{movie}.tar"
    if key not in index:
        raise RuntimeError(f"关键帧包中无 {movie}")
    e = index[key]
    url = _resolve(f"raw/{KF_ZIP_NAME}")
    hdr = _range_get(url, e["lho"], e["lho"] + 29)
    if hdr[:4] != b"PK\x03\x04":
        raise RuntimeError(f"{movie}: zip 本地文件头异常")
    nlen, elen = struct.unpack("<HH", hdr[26:30])
    data_off = e["lho"] + 30 + nlen + elen

    # 先写临时文件,完成并校验大小后原子改名——中断不会留下"看似完整"的半成品
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    got = 0
    d = zlib.decompressobj(-15)
    with open(tmp, "wb") as out:
        while got < e["csize"]:
            end = min(got + (8 << 20), e["csize"]) - 1
            chunk = _range_get(url, data_off + got, data_off + end)
            if not chunk:
                url = _resolve(f"raw/{KF_ZIP_NAME}")
                continue
            out.write(d.decompress(chunk))
            got += len(chunk)
        out.write(d.flush())
    if tmp.stat().st_size != e["usize"]:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"{movie}: 解压后大小 {tmp.stat().st_size if tmp.exists() else 0}"
            f" != {e['usize']}")
    tmp.replace(target)
    return target


_ZIP_MEMBERS = {  # 基础包内 zip → (解出的目录, 文件名格式)
    "files/annotation.v1.zip": ("annotation", "{m}.json"),
    "files/subtitle1K.v1.zip": ("subtitle", "{m}.srt"),
    "files/script1K.v1.zip": ("script", "{m}.script"),
    "files/meta.v1.zip": ("meta", "{m}.json"),
}
_REQUIRED = {"annotation", "subtitle", "meta"}


def extract_movie_files(pkg: Path, movie: str, src: Path) -> None:
    """从基础包选择性抽取该片四类源文件(跳过 poster4M;已存在不覆盖)。"""
    wanted = {member: (d, fmt.format(m=movie)) for member, (d, fmt)
              in _ZIP_MEMBERS.items()}
    found: set[str] = set()
    with tarfile.open(pkg, "r:gz") as tf:
        for member in tf:  # 顺序扫描一次
            if member.name not in wanted:
                continue
            dest_dir, dest_name = wanted[member.name]
            dest = src / dest_dir / dest_name
            found.add(dest_dir)
            if dest.exists():
                continue
            fobj = tf.extractfile(member)
            if fobj is None:
                continue
            with zipfile.ZipFile(io.BytesIO(fobj.read())) as zf:
                inner = next((n for n in zf.namelist() if n.endswith(dest_name)),
                             None)
                if inner is None:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(inner))
    missing = _REQUIRED - found
    if missing:
        raise RuntimeError(f"基础包缺少 {sorted(missing)} 的源 zip")
    if "script" not in found:
        print(f"警告: 基础包中无 {movie} 的剧本,台词将全部走「剧本未匹配」丢弃路径")
```

注:`fetch_keyframes` 下载中断时 `_range_get` 抛错、`.part` 残留下次重开(整段重来);pilot 网络抖动由 6 次重试兜底。若试点发现大 tar(>300MB)中断频繁,再升级为压缩流断点续传(记录已下载压缩字节数,从 `.part` 续拼)。

- [ ] **Step 4: 注册 CLI(Edit cli.py)**

`eval_app` 注册之后加:

```python
movienet_app = typer.Typer(help="MovieNet 数据接入(fetch / convert,DESIGN 3.4)")
app.add_typer(movienet_app, name="movienet")
```

文件末尾 `main = app` 之前加:

```python
@movienet_app.command()
def fetch(movie: str = typer.Option(..., "--movie", help="IMDb tt 号,如 tt0113243"),
          src: str = typer.Option("data/raw/movienet", "--src")):
    """下载基础包(一次性)+ 该片关键帧 tar,并抽取四类源文件。"""
    from pathlib import Path

    from movie_kg.ingestion.movienet import fetch as mn
    src_path = Path(src)
    pkg = mn.fetch_base(src_path)
    typer.echo(f"基础包就绪: {pkg}")
    mn.extract_movie_files(pkg, movie, src_path)
    kf = mn.fetch_keyframes(movie, src_path)
    typer.echo(f"关键帧就绪: {kf}")
```

- [ ] **Step 5: 运行测试**

Run: `python -m pytest tests/test_movienet_fetch.py -v`
Expected: 4 项 PASS

- [ ] **Step 6: 全量回归 + Commit**

Run: `python -m pytest`
Expected: 全绿

```bash
git add -A
git commit -m "feat: MovieNet 取数(fetch: 基础包 + 逐片关键帧 tar,Range 下载与 zip64 索引)"
```

---

### Task 3: SRT 解析与剧本对齐(subs / align)

**Files:**
- Create: `src/movie_kg/ingestion/movienet/subs.py`
- Create: `src/movie_kg/ingestion/movienet/align.py`
- Test: `tests/test_movienet_align.py`

**Interfaces:**
- Produces(Task 4 依赖): `parse_srt(text: str) -> list[Cue]`(Cue 为 dataclass `start/end: float, text: str`,影片绝对秒);`parse_script(text: str) -> list[ScriptLine]`(ScriptLine 为 dataclass `speaker: str, text: str`);`align_cues(cues, script_lines, threshold=0.6, lookahead=60) -> list[str | None]`(逐 cue 的剧本说话人,未匹配 None);`resolve_speaker(speaker: str, roster: list[str]) -> str | None`(全等 → 名/姓词级包含两级)

- [ ] **Step 1: 写失败测试(tests/test_movienet_align.py)**

```python
from movie_kg.ingestion.movienet.align import (
    ScriptLine, align_cues, parse_script, resolve_speaker)
from movie_kg.ingestion.movienet.subs import parse_srt

SRT = """1
00:00:00,500 --> 00:00:01,200
You can't handle the truth.

2
00:00:01,500 --> 00:00:01,900
I was just here to talk.

3
00:00:03,500 --> 00:00:03,900
The money is gone.
"""

SCRIPT = """EXT. OFFICE - DAY

                    MATTY
          You can't handle the truth.

                    NED
          I was just here to talk.

Later.

                    NED
          The money is gone.
"""


def test_parse_srt_reads_cues_with_ms():
    cues = parse_srt(SRT)
    assert [c.text for c in cues] == ["You can't handle the truth.",
                                      "I was just here to talk.",
                                      "The money is gone."]
    assert abs(cues[0].start - 0.5) < 1e-9 and abs(cues[0].end - 1.2) < 1e-9


def test_parse_srt_skips_blocks_without_body():
    cues = parse_srt("1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:03,000 --> 00:00:04,000\nok\n")
    assert len(cues) == 1 and cues[0].text == "ok"


def test_parse_script_extracts_speaker_blocks():
    lines = parse_script(SCRIPT)
    assert [(l.speaker, l.text) for l in lines] == [
        ("MATTY", "You can't handle the truth."),
        ("NED", "I was just here to talk."),
        ("NED", "The money is gone."),
    ]


def test_parse_script_cleans_contd_and_parenthetical():
    text = ("                    ALICE (CONT'D)\n"
            "          Hello\n          (beat)\n          there.\n")
    assert parse_script(text) == [ScriptLine(speaker="ALICE",
                                             text="Hello there.")]


def test_align_cues_matches_in_order():
    cues = parse_srt(SRT)
    out = align_cues(cues, parse_script(SCRIPT))
    assert out == ["MATTY", "NED", "NED"]


def test_align_cues_unmatched_is_none():
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\nCompletely unrelated line.\n")
    assert align_cues(cues, parse_script(SCRIPT)) == [None]


def test_align_cues_matches_split_cue_by_containment():
    # SRT 把一句剧本台词拆成两条 cue:包含关系兜底
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\nYou can't handle\n\n"
                     "2\n00:00:00,300 --> 00:00:00,400\nthe truth.\n")
    out = align_cues(cues, parse_script(SCRIPT))
    assert out[0] == "MATTY"          # 前半句包含于剧本台词
    assert out[1] in (None, "MATTY")  # 后半句允许未匹配


def test_alignment_monotonic_no_backtrack():
    # Review Focus #5:同名台词出现在剧本两处,cue 序列必须单调前进不回跳
    script = parse_script(
        "                    BOB\n          hello there\n\n"
        "                    ALICE\n          hello there\n")
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\nhello there\n\n"
                     "2\n00:00:00,300 --> 00:00:00,400\nhello there\n")
    assert align_cues(cues, script) == ["BOB", "ALICE"]


def test_resolve_speaker_two_levels():
    roster = ["Matty Walker", "Ned Racine"]
    assert resolve_speaker("MATTY", roster) == "Matty Walker"   # 名词级包含
    assert resolve_speaker("ned racine", roster) == "Ned Racine"  # 全等(大小写不敏感)
    assert resolve_speaker("GHOST", roster) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_movienet_align.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现 subs.py**

```python
"""SRT 字幕解析(影片绝对时间,毫秒保留在 Cue 内,ts 落盘时截断)。"""
import re
from dataclasses import dataclass

_CUE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
                  r"(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})")


@dataclass
class Cue:
    start: float  # 秒(影片绝对)
    end: float
    text: str


def parse_srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in text.strip().replace("\r\n", "\n").split("\n\n"):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        m = None
        body: list[str] = []
        for i, line in enumerate(lines):
            m = _CUE.search(line)
            if m:
                body = [l for l in lines[i + 1:] if l.strip()]
                break
        if m is None or not body:
            continue
        g = [int(x) for x in m.groups()]
        cues.append(Cue(
            start=g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000,
            end=g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000,
            text=" ".join(body)))
    return cues
```

- [ ] **Step 4: 实现 align.py**

```python
"""剧本解析与 SRT↔剧本台词对齐(DESIGN §3.4 stage 4)。"""
import difflib
import re
from dataclasses import dataclass

from movie_kg.ingestion.movienet.subs import Cue

# 角色行:深缩进(≥8 空格)+ 全大写短标签,可带 (CONT'D)/(V.O.) 等后缀
_SPEAKER = re.compile(r"^\s{8,}([A-Z][A-Z0-9 .'&\-]{1,40})(\(.*?\))?\s*$")
_CONTD = re.compile(r"\(CONT'?D\)|\(V\.?O\.?\)|\(O\.?S\.?\)", re.IGNORECASE)


@dataclass
class ScriptLine:
    speaker: str  # 剧本角色标签(大写)
    text: str


def parse_script(text: str) -> list[ScriptLine]:
    """识别「角色行 + 台词块」;场景标题/动作行(顶格)与括号指示不进结果。"""
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[ScriptLine] = []
    i = 0
    while i < len(lines):
        m = _SPEAKER.match(lines[i])
        if m and i + 1 < len(lines) and lines[i + 1].strip():
            speaker = _CONTD.sub("", m.group(1)).strip()
            body: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].strip() \
                    and not _SPEAKER.match(lines[j]):
                if not lines[j].strip().startswith("("):  # (beat) 等指示跳过
                    body.append(lines[j].strip())
                j += 1
            if body:
                out.append(ScriptLine(speaker=speaker, text=" ".join(body)))
            i = j
        else:
            i += 1
    return out


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())


def align_cues(cues: list[Cue], script_lines: list[ScriptLine],
               threshold: float = 0.6, lookahead: int = 60) -> list[str | None]:
    """逐 cue 在剧本中找对齐台词,返回剧本说话人(未匹配 None)。

    单调双指针:SRT 与剧本均按剧情顺序,只向前看 lookahead 行;
    相似度 = 包含关系(短/长)或 difflib ratio,过阈值取最高分。"""
    speakers: list[str | None] = [None] * len(cues)
    j = 0
    for ci, cue in enumerate(cues):
        nc = _norm(cue.text)
        if not nc:
            continue
        best: tuple[float, int] | None = None
        for jj in range(j, min(j + lookahead, len(script_lines))):
            ns = _norm(script_lines[jj].text)
            if not ns:
                continue
            if nc in ns or ns in nc:
                score = min(len(nc), len(ns)) / max(len(nc), len(ns))
            else:
                score = difflib.SequenceMatcher(None, nc, ns).ratio()
            if score >= threshold and (best is None or score > best[0]):
                best = (score, jj)
        if best is not None:
            speakers[ci] = script_lines[best[1]].speaker
            j = best[1] + 1
    return speakers


def resolve_speaker(speaker: str, roster: list[str]) -> str | None:
    """剧本角色标签 → 元数据角色名:全等(大小写不敏感)→ 名/姓词级包含。"""
    s = speaker.strip().lower()
    for c in roster:
        if c.lower() == s:
            return c
    for c in roster:
        if s and s in [w for w in re.split(r"[ .]", c.lower()) if w]:
            return c
    return None
```

- [ ] **Step 5: 运行测试**

Run: `python -m pytest tests/test_movienet_align.py -v`
Expected: 9 项 PASS

- [ ] **Step 6: 全量回归 + Commit**

Run: `python -m pytest`
Expected: 全绿

```bash
git add -A
git commit -m "feat: SRT 解析与剧本对齐(单调指针 + 包含/ratio,说话人两级匹配)"
```

---

### Task 4: MovieNet 转换器(convert 九 stage + 报告 + CLI)

**Files:**
- Create: `src/movie_kg/ingestion/movienet/convert.py`
- Modify: `src/movie_kg/cli.py`(movienet 组加 convert 命令)
- Test: `tests/test_movienet_convert.py`

**Interfaces:**
- Consumes: Task 1 的 `load_movie` / `SceneMeta` / `CharacterMeta` / `Dialogue` / `KeyframeMeta` / `fmt_ts`;Task 2 的源文件布局(`<src>/{annotation,subtitle,script,meta,keyframes}`);Task 3 的四个函数
- Produces: `convert(movie: str, src: Path, out: Path) -> Path`(输出目录,含 movie.json / scenes/*.json / frames/*.jpg / convert_report.md);`ConvertError`;各 stage 纯函数(`calibrate_fps / scene_windows / scene_characters / scene_dialogues / scene_place / scene_descriptions / keyframe_entries / extract_keyframes / write_report`)
- CLI: `kg movienet convert --movie tt0113243 --src data/raw/movienet --out data/movies/tt0113243`

- [ ] **Step 1: 写失败测试(tests/test_movienet_convert.py)**

```python
import io
import json
import tarfile

import pytest

from movie_kg.ingestion.data_loader import load_movie
from movie_kg.ingestion.movienet.convert import (
    ConvertError, calibrate_fps, convert, keyframe_entries, scene_dialogues,
    scene_windows)

SRT = """1
00:00:00,500 --> 00:00:01,200
You can't handle the truth.

2
00:00:01,500 --> 00:00:01,900
I was just here to talk.

3
00:00:02,000 --> 00:00:02,400
Boundary line.

4
00:00:03,500 --> 00:00:03,900
The money is gone.

5
00:00:03,700 --> 00:00:04,100
This line is nowhere in the script.
"""

SCRIPT = """EXT. OFFICE - DAY

                    MATTY
          You can't handle the truth.

                    NED
          I was just here to talk.

                    NED
          Boundary line.

                    NED
          The money is gone.
"""


def _ann(story_shots=((0, 1),), story_dur=((0.0, 2.0),)):
    return {
        "imdb_id": "tt0000001",
        "cast": [
            {"id": "x1", "pid": "nm1", "shot_idx": 0, "img_idx": 0},
            {"id": "x2", "pid": "nm2", "shot_idx": 1, "img_idx": 0},
        ],
        "scene": [
            {"id": "s0", "shot": [0, 1], "frame": [0, 48],
             "place_tag": ["office", "room"], "action_tag": None},
            {"id": "s1", "shot": [2, 3], "frame": [48, 96],
             "place_tag": None, "action_tag": None},
        ],
        "story": [
            {"id": "st0", "shot": list(s), "frame": [0, 48], "duration": list(d),
             "consistency": 0.9, "description": "Opening scene in the office.",
             "subtitle": []}
            for s, d in zip(story_shots, story_dur)
        ],
        "cinematic_style": None,
    }


META = {"imdb_id": "tt0000001", "title": "Mini (2020)",
        "cast": [{"id": "nm1", "name": "Actor One", "character": "Matty Walker"},
                 {"id": "nm2", "name": "Actor Two", "character": "Ned Racine"}]}


def _make_src(tmp_path, ann=None):
    src = tmp_path / "src"
    for sub in ("annotation", "subtitle", "script", "meta"):
        (src / sub).mkdir(parents=True, exist_ok=True)
    (src / "annotation" / "tt0000001.json").write_text(
        json.dumps(ann or _ann()), encoding="utf-8")
    (src / "meta" / "tt0000001.json").write_text(
        json.dumps(META), encoding="utf-8")
    (src / "subtitle" / "tt0000001.srt").write_text(SRT, encoding="utf-8")
    (src / "script" / "tt0000001.script").write_text(SCRIPT, encoding="utf-8")
    (src / "keyframes").mkdir(exist_ok=True)
    with tarfile.open(src / "keyframes" / "tt0000001.tar", "w") as tf:
        for shot in range(4):
            for img in range(3):
                data = b"\xff\xd8\xff\xd9"  # 最小 JPEG(存在且非空即可)
                ti = tarfile.TarInfo(f"tt0000001/shot_{shot:04d}_img_{img}.jpg")
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
    return src


def test_calibrate_fps_median_and_tolerance():
    fps = calibrate_fps(_ann()["story"])
    assert abs(fps - 24.0) < 1e-9  # (48-0)/(2.0-0.0)
    bad = _ann(story_shots=((0, 1), (2, 3)), story_dur=((0.0, 2.0), (2.0, 4.4)))
    with pytest.raises(ConvertError):
        calibrate_fps(bad["story"])   # 24 与 ~21.8 离散度超 ±0.5%


def test_dialogue_cue_at_scene_boundary():
    # Review Focus #3:cue 起点恰在边界 → 归后一场景,不重复计入
    scenes = scene_windows(_ann(), 24.0)
    per, stats = scene_dialogues(SRT, SCRIPT, scenes, ["Matty Walker", "Ned Racine"])
    assert [len(d) for d in per] == [2, 2]
    assert per[1][0].text == "Boundary line."      # 2.0s 的 cue 在 scene_002
    assert stats["no_script_match"] == 1 and stats["outside_scenes"] == 0


def test_keyframe_ts_clamped_into_window():
    # Review Focus #4:story 镜头插值越过场景窗口 → clamp 进窗口
    ann = _ann(story_shots=((0, 3),), story_dur=((0.0, 4.0),))
    ann["scene"][0]["frame"] = [0, 84]    # scene_001 窗口 [0, 3.5]
    ann["scene"][1]["frame"] = [84, 96]   # scene_002 窗口 [3.5, 4]
    scenes = scene_windows(ann, 24.0)
    kfs = keyframe_entries(ann, scenes)
    # story 覆盖 shots[0,3] dur[0,4]: 均匀插值 t(k)=k+0.5
    assert kfs[0][3].ts == "00:00:01"     # shot1: t=1.5 ∈ [0,3.5] 未越界
    assert kfs[1][0].ts == "00:00:03"     # shot2: 插值 2.5 < 3.5 → clamp 到 3.5
    assert kfs[1][3].ts == "00:00:03"     # shot3: 插值 3.5 恰在窗口起点


def test_convert_end_to_end(tmp_path):
    src = _make_src(tmp_path)
    out = tmp_path / "data" / "movies" / "tt0000001"
    convert("tt0000001", src, out)
    movie = load_movie(out)               # stage 9 自校验通过才返回
    assert movie.movie_id == "tt0000001" and movie.title == "Mini (2020)"
    s1, s2 = movie.scenes
    assert [c.name for c in s1.meta.characters] == ["Matty Walker", "Ned Racine"]
    assert [d.char for d in s1.meta.dialogues] == ["Matty Walker", "Ned Racine"]
    assert s1.meta.location == "office / room"
    assert s1.meta.description == "Opening scene in the office."
    assert len(s1.meta.flat_keyframes()) == 6      # 2 shots × 3 imgs
    assert s1.meta.virtual_time == "" and s1.meta.shots == []
    # scene_002: cast 名单空,说话人并入(Ned Racine)
    assert [c.name for c in s2.meta.characters] == ["Ned Racine"]
    assert [d.text for d in s2.meta.dialogues] == ["Boundary line.", "The money is gone."]
    assert s2.meta.location == "" and s2.meta.description == ""
    assert len(list((out / "frames").glob("*.jpg"))) == 12
    report = (out / "convert_report.md").read_text(encoding="utf-8")
    assert "fps 标定: 24.0000" in report
    assert "剧本未匹配 1" in report
    assert "loader 校验: 通过" in report


def test_convert_rejects_when_validation_fails(tmp_path):
    src = _make_src(tmp_path)   # 关键帧 tar 只有 shot 0-3,但场景引用 shot 0-3 之外
    ann = _ann()
    ann["scene"][1]["shot"] = [2, 9]
    (src / "annotation" / "tt0000001.json").write_text(
        json.dumps(ann), encoding="utf-8")
    out = tmp_path / "out"
    with pytest.raises(ConvertError):
        convert("tt0000001", src, out)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_movienet_convert.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现 convert.py**

```python
"""MovieNet → v0.4.0 输入转换(DESIGN §3.4,九个 stage)。"""
import json
import re
import statistics
import tarfile
from pathlib import Path

from movie_kg.ingestion.data_loader import InputValidationError, load_movie
from movie_kg.ingestion.movienet.align import align_cues, parse_script, resolve_speaker
from movie_kg.ingestion.movienet.subs import parse_srt
from movie_kg.ingestion.timefmt import fmt_ts
from movie_kg.models.scene import (CharacterMeta, Dialogue, KeyframeMeta,
                                   SceneMeta)

FPS_TOL = 0.005          # fps 离散度门槛 ±0.5%(DESIGN §3.4 stage 1)
MATCH_THRESHOLD = 0.6    # 剧本对齐阈值(试点校准)


class ConvertError(Exception):
    pass


def calibrate_fps(story: list[dict]) -> float:
    """stage 1: story 条目 Δframe/Δduration 取中位;离散度超限报错。"""
    ests = [(st["frame"][1] - st["frame"][0]) / (st["duration"][1] - st["duration"][0])
            for st in story
            if st["duration"][1] > st["duration"][0] and st["frame"][1] > st["frame"][0]]
    if not ests:
        raise ConvertError("无 story 条目可用于 fps 标定")
    med = statistics.median(ests)
    if any(abs(e - med) / med > FPS_TOL for e in ests):
        raise ConvertError(f"fps 离散度超限: {[round(e, 4) for e in ests]}")
    return med


def scene_windows(ann: dict, fps: float) -> list[dict]:
    """stage 2: 场景窗口(秒)+ shot 区间,按帧序。"""
    scenes = sorted(ann.get("scene") or [], key=lambda s: s["frame"][0])
    if not scenes:
        raise ConvertError("无场景标注")
    return [{"seq": i + 1,
             "start": s["frame"][0] / fps,
             "end": s["frame"][1] / fps,
             "shot_span": (s["shot"][0], s["shot"][1]),
             "place_tag": s.get("place_tag")}
            for i, s in enumerate(scenes)]


def scene_characters(ann: dict, meta: dict, scenes: list[dict]) -> list[list[str]]:
    """stage 3: 每场景在册角色(cast pid → meta 角色名,按 shot 区间聚合)。"""
    nm_to_char = {c["id"]: c.get("character", "").strip()
                  for c in meta.get("cast", []) if c.get("character")}
    named: dict[str, str] = {}
    for inst in ann.get("cast") or []:
        pid = inst.get("pid")
        if pid and pid != "others" and pid in nm_to_char:
            named[pid] = nm_to_char[pid]
    by_shot: dict[int, set[str]] = {}
    for inst in ann.get("cast") or []:
        name = named.get(inst.get("pid"))
        if name is not None:
            by_shot.setdefault(inst.get("shot_idx", -1), set()).add(name)
    out = []
    for sc in scenes:
        a, b = sc["shot_span"]
        names: set[str] = set()
        for k in range(a, b + 1):
            names |= by_shot.get(k, set())
        out.append(sorted(names))
    return out


def scene_dialogues(srt_text: str, script_text: str, scenes: list[dict],
                    roster: list[str]) -> tuple[list[list[Dialogue]], dict]:
    """stage 4: SRT 按场景窗口切片 + 剧本对齐 + 说话人归 roster。
    cue 归起点所在场景;两级未匹配分别计数后丢弃。"""
    cues = parse_srt(srt_text)
    speakers = (align_cues(cues, parse_script(script_text),
                           threshold=MATCH_THRESHOLD)
                if script_text.strip() else [None] * len(cues))
    bounds = [(sc["start"], sc["end"]) for sc in scenes]
    per_scene: list[list[Dialogue]] = [[] for _ in scenes]
    stats = {"total": len(cues), "no_script_match": 0,
             "speaker_not_in_roster": 0, "outside_scenes": 0}
    for cue, spk in zip(cues, speakers):
        si = next((i for i, (s, e) in enumerate(bounds) if s <= cue.start < e),
                  None)
        if si is None:                       # 片头/片尾等场景窗口之外
            stats["outside_scenes"] += 1
            continue
        if spk is None:
            stats["no_script_match"] += 1
            continue
        char = resolve_speaker(spk, roster)
        if char is None:
            stats["speaker_not_in_roster"] += 1
            continue
        per_scene[si].append(Dialogue(char=char, text=cue.text,
                                      ts=fmt_ts(cue.start)))
    return per_scene, stats


def scene_place(sc: dict) -> str:
    """stage 5: place_tag 多值 join;未标注 → 空。"""
    return " / ".join(sc.get("place_tag") or [])


def scene_descriptions(ann: dict, scenes: list[dict]) -> list[str]:
    """stage 6: story 段落赋给 shot 区间重叠最大的场景(粗粒度先验)。"""
    story = ann.get("story") or []
    out = []
    for sc in scenes:
        a, b = sc["shot_span"]
        best, best_ov = "", 0
        for st in story:
            sa, sb = st["shot"]
            ov = min(b, sb) - max(a, sa) + 1
            if ov > best_ov and st.get("description"):
                best, best_ov = st["description"].strip(), ov
        out.append(best)
    return out


def keyframe_entries(ann: dict, scenes: list[dict]) -> list[list[KeyframeMeta]]:
    """stage 7: 每场景关键帧元数据。ts 为近似锚定:story 段内按镜头索引线性
    插值(取镜头中点),无段覆盖时按场景窗口位置;一律 clamp 进场景窗口。"""
    spans = [(st["shot"][0], st["shot"][1],
              st["duration"][0], st["duration"][1])
             for st in ann.get("story") or []]
    out = []
    for sc in scenes:
        a, b = sc["shot_span"]
        entries = []
        for k in range(a, b + 1):
            t = None
            for sa, sb, da, db in spans:
                if sa <= k <= sb and sb > sa:
                    t = da + (k - sa + 0.5) / (sb - sa + 1) * (db - da)
                    break
            if t is None:  # story 未覆盖:场景窗口内按镜头位置
                t = sc["start"] + (k - a + 0.5) / (b - a + 1) \
                    * (sc["end"] - sc["start"])
            t = min(max(t, sc["start"]), sc["end"])  # clamp(§3.3 窗口校验)
            for img in range(3):
                entries.append(KeyframeMeta(frame_id=f"s{k:04d}i{img}",
                                            ts=fmt_ts(t)))
        out.append(entries)
    return out


def extract_keyframes(kf_tar: Path, out_dir: Path, wanted: set[str]) -> int:
    """stage 7 落盘:关键帧 tar → out_dir/s{shot}i{img}.jpg。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    with tarfile.open(kf_tar) as tf:
        for member in tf:
            m = re.match(r"(?:[^/]+/)?shot_(\d+)_img_(\d)\.jpg$", member.name)
            if not m or not member.isfile():
                continue
            fid = f"s{int(m.group(1)):04d}i{m.group(2)}"
            if fid not in wanted:
                continue
            fobj = tf.extractfile(member)
            if fobj is None:
                continue
            (out_dir / f"{fid}.jpg").write_bytes(fobj.read())
            n += 1
    return n


def write_report(out: Path, movie: str, fps: float, n_scenes: int,
                 stats: dict, places: list[str], descs: list[str],
                 kf_counts: list[int], n_files: int, validation: str) -> None:
    kept = stats["total"] - stats["no_script_match"] \
        - stats["speaker_not_in_roster"] - stats["outside_scenes"]
    lines = [
        f"# MovieNet 转换报告 — {movie}", "",
        f"- fps 标定: {fps:.4f}",
        f"- 场景数: {n_scenes}",
        f"- 台词: 总数 {stats['total']}, 剧本未匹配 {stats['no_script_match']}, "
        f"说话人不在 roster {stats['speaker_not_in_roster']}, "
        f"场景窗口外 {stats['outside_scenes']}, 保留 {kept}"
        f"({kept / stats['total']:.0%})" if stats["total"] else "- 台词: 总数 0",
        f"- place 覆盖: {sum(1 for p in places if p)}/{n_scenes}",
        f"- description 覆盖: {sum(1 for d in descs if d)}/{n_scenes}",
        f"- 关键帧: 落盘 {n_files} 个, 每场景中位 "
        f"{statistics.median(kf_counts):.0f}" if kf_counts else "- 关键帧: 0",
        f"- loader 校验: {validation}",
        "",
        "已知局限(DESIGN §3.4): keyframe.ts 为近似锚定(±数秒);"
        "关键帧密度 = 每镜头 3 帧;virtual_time 留空。",
        "",
    ]
    (out / "convert_report.md").write_text("\n".join(lines), encoding="utf-8")


def convert(movie: str, src: Path, out: Path) -> Path:
    """九个 stage 全流程;自校验通过才返回输出目录。"""
    ann = json.loads((src / "annotation" / f"{movie}.json")
                     .read_text(encoding="utf-8"))
    meta = json.loads((src / "meta" / f"{movie}.json")
                      .read_text(encoding="utf-8"))
    srt_text = (src / "subtitle" / f"{movie}.srt") \
        .read_text(encoding="utf-8", errors="replace")
    script_path = src / "script" / f"{movie}.script"
    script_text = (script_path.read_text(encoding="utf-8", errors="replace")
                   if script_path.exists() else "")

    fps = calibrate_fps(ann.get("story") or [])               # stage 1
    scenes = scene_windows(ann, fps)                           # stage 2
    cast_rosters = scene_characters(ann, meta, scenes)         # stage 3
    movie_roster = sorted({n for r in cast_rosters for n in r})
    per_dlg, dlg_stats = scene_dialogues(srt_text, script_text, scenes,
                                         movie_roster)         # stage 4
    places = [scene_place(sc) for sc in scenes]                # stage 5
    descs = scene_descriptions(ann, scenes)                    # stage 6
    kf_lists = keyframe_entries(ann, scenes)                   # stage 7

    # 说话人并入所在场景名单(cast 标注可能漏人;保证 loader char 对齐)
    scene_chars: list[list[str]] = []
    for roster, dlgs in zip(cast_rosters, per_dlg):
        names = list(roster)
        for d in dlgs:
            if d.char not in names:
                names.append(d.char)
        scene_chars.append(names)

    out.mkdir(parents=True, exist_ok=True)                     # stage 8
    (out / "scenes").mkdir(exist_ok=True)
    (out / "movie.json").write_text(json.dumps(
        {"movie_id": out.name, "title": meta.get("title", movie)},
        ensure_ascii=False, indent=1), encoding="utf-8")
    for sc, names, dlgs, place, desc, kfs in zip(
            scenes, scene_chars, per_dlg, places, descs, kf_lists):
        m = SceneMeta(scene_id=f"scene_{sc['seq']:03d}", movie_id=out.name,
                      start_ts=fmt_ts(sc["start"]), end_ts=fmt_ts(sc["end"]),
                      characters=[CharacterMeta(name=n) for n in names],
                      dialogues=dlgs, location=place, virtual_time="",
                      description=desc, props=[], shots=[], keyframes=kfs)
        (out / "scenes" / f"scene_{sc['seq']:03d}.json").write_text(
            m.model_dump_json(exclude_none=True), encoding="utf-8")
    wanted = {k.frame_id for kfs in kf_lists for k in kfs}
    n_files = extract_keyframes(src / "keyframes" / f"{movie}.tar",
                                out / "frames", wanted)

    validation = "通过"                                        # stage 9
    try:
        load_movie(out)
    except InputValidationError as e:
        validation = "失败: " + "; ".join(e.errors[:5])
    write_report(out, movie, fps, len(scenes), dlg_stats, places, descs,
                 [len(kfs) for kfs in kf_lists], n_files, validation)
    if validation != "通过":
        raise ConvertError(f"自校验失败,详见 {out / 'convert_report.md'}")
    return out
```

- [ ] **Step 4: CLI 加 convert 命令(Edit cli.py,fetch 命令之后)**

```python
@movienet_app.command()
def convert(movie: str = typer.Option(..., "--movie"),
            src: str = typer.Option("data/raw/movienet", "--src"),
            out: str = typer.Option(None, "--out",
                                    help="缺省为 data/movies/<movie>")):
    """MovieNet → v0.4.0 输入(九 stage,自校验 + convert_report)。"""
    from pathlib import Path

    from movie_kg.ingestion.movienet import convert as mnconvert
    out_path = Path(out) if out else Path("data/movies") / movie
    result = mnconvert.convert(movie, Path(src), out_path)
    typer.echo(f"转换完成: {result}(报告见 {result / 'convert_report.md'})")
```

- [ ] **Step 5: 运行测试**

Run: `python -m pytest tests/test_movienet_convert.py -v`
Expected: 5 项 PASS

- [ ] **Step 6: 全量回归 + Commit**

Run: `python -m pytest`
Expected: 全绿

```bash
git add -A
git commit -m "feat: MovieNet 转换器(九 stage + 自校验 + convert_report + CLI)"
```

---

### Task 5: 文档与版本收尾

**Files:**
- Modify: `README.md`(数据准备/使用/迁移段)
- Modify: `pyproject.toml`(version = "0.4.0")

**Interfaces:**
- Consumes: Task 2/4 的 CLI 命令名(`kg movienet fetch/convert`)
- Produces: 无代码;文档与发布版本

- [ ] **Step 1: README「数据准备」段替换为**

```markdown
## 数据准备

```
data/movies/<movie_id>/
  movie.json                     # {"movie_id": "m1", "title": "..."}
  frames/frame_001.jpg ...       # 输入 1: 关键帧目录(文件名 = frame_id)
  scenes/scene_001.json ...      # 输入 2: 场景元数据(完整台词 + 镜头/关键帧标注;
                                 #   字段与约定见 docs/DESIGN.md 3.2,时间戳一律影片
                                 #   绝对时间,shot_id/frame_id 全局唯一,顶层 keyframes
                                 #   为无镜头归属的帧)
```

**MovieNet 免片源接入**(v0.4.0,详见 docs/DESIGN.md 3.4):

```bash
kg movienet fetch   --movie tt0113243 --src data/raw/movienet
kg movienet convert --movie tt0113243 --src data/raw/movienet
kg build --movie tt0113243 --scenes 1-5   # 校准成本后全量
```

**从 v0.3.x 升级**:视频输入已移除,`video/` 目录不再读取——请将标注关键帧抽为
`frames/{frame_id}.jpg`(可顶层 `keyframes` 声明无镜头归属的帧)。视觉输入形态已变,
旧 `checkpoints/` 建议删除或 `kg build --from-scratch` 全量重建。
```

(注意:该代码块嵌在 markdown 中,实际写入时最外层用四反引号包裹或直接按 README 现有排版风格手写等价内容;语义以上述为准。)

- [ ] **Step 2: pyproject 版本**

`version = "0.3.0"` → `version = "0.4.0"`

- [ ] **Step 3: 全量回归 + Commit**

Run: `python -m pytest`
Expected: 全绿

```bash
git add -A
git commit -m "docs: README v0.4.0(帧目录数据准备、MovieNet 接入、迁移说明);版本 0.4.0"
```

---

## 试点执行(任务全部完成并终审通过后,控制者执行)

```bash
kg movienet fetch   --movie tt0113243 --src data/raw/movienet   # Hackers
kg movienet fetch   --movie tt0105236 --src data/raw/movienet   # Reservoir Dogs
kg movienet fetch   --movie tt0117500 --src data/raw/movienet   # The Rock
kg movienet convert --movie tt0113243 --src data/raw/movienet
kg movienet convert --movie tt0105236 --src data/raw/movienet
kg movienet convert --movie tt0117500 --src data/raw/movienet
kg build --movie tt0113243 --scenes 1-5    # 成本校准 → 全量
```

验收:三份 convert_report 的台词匹配率与 place 覆盖;`kg build` 跑通且事件带 evidence_frame_ids;The Rock(467 场景)按校准成本决定全量或抽样。数据落盘约 610MB(C 盘当前可容纳)。
