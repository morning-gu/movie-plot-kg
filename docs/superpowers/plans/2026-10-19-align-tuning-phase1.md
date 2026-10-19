# 台词对齐调优阶段 1(零风险召回 + 审计工具)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不动阈值(保持 0.6)的前提下落地 6 项零/低精度风险的台词召回改进(覆盖兜底、兄弟候选、l→i OCR 归一、荣誉前缀重试、meta.cast 并入解析字典、性能修复),并交付新旧对照审计工具,为阶段 2 的阈值决策提供数据。

**Architecture:** `align.py` 的对齐循环重构为 `align_detailed`(预归一化 + 长度预筛 + 兄弟候选 + 短门 + 覆盖兜底),`align_cues` 变为薄包装;`convert.py` 的说话人解析字典从视觉 roster 扩为 roster ∪ meta.cast;新增 `audit.py`(保留 v0.4.0 legacy 语义做逐 cue 对照 + 分层抽样导出)与 `kg movienet align-audit` 命令。

**Tech Stack:** Python 3.11+ / difflib / typer / pytest(全部合成数据测试,真实数据只在 Task 5)

**Spec:** 对话内批准的修订设计 v2(2026-10-19,评估轮产出;独立评审报告与探针数据见 `C:\Users\admin\AppData\Local\Temp\align-review\` 与 `C:\Users\admin\AppData\Local\Temp\movienet-align-probe\probe_out.txt`)。关键约束全部转录至下方 Global Constraints。

## Global Constraints

- `MATCH_THRESHOLD` 保持 **0.6**(`convert.py` 不改该常量;阈值决策属阶段 2,由审计数据驱动)。
- **预筛永不门控覆盖兜底路径**(评审 B2:字面「跳过该对」会杀掉 21%/9%/3% 的覆盖目标)。
- **兄弟候选仅在严格更优时胜出**:候选行 j-1 的分必须 `> 窗口最高分` 且 ≥ 有效阈值;平分时窗口行胜(保单调语义)。
- **短门口径 = 去重 token**:cue 去重 token < 3 → 有效阈值抬至 0.6。
- **覆盖兜底合格条件**:cue 与行去重 token 都 ≥ 4,recall = |cue∩行|/|cue| ≥ **0.85**,且最长公共连续段(len(nc) 若 `nc in ns`,否则 `find_longest_match(...).size`)≥ **0.5·len(nc)**;仅在 base 路径(窗口+全局回退)无胜者时生效;多候选取 (recall, 连续段长, -idx) 最大。
- `resolve_speaker` 四级超集:exact(s) → word(s) → exact(strip(s)) → word(strip(s)),strip 仅剥 `mr/mrs/ms/dr` 前缀;剥后为空直接跳过该路径。
- **语义超集原则**:旧版能解析的 (label→角色) 结果不变;对齐绑定原则上保持,兄弟候选与 l→i 引入的翻转由 Task 5 审计量化(不做无凭据的改绑)。
- 现有测试不删、不改断言(test_movienet_align.py / test_movienet_convert.py 全部保持绿)。
- 测试命令:`.venv/Scripts/python.exe -m pytest`;提交信息中文,`feat:`/`fix:`/`docs:` 前缀。
- Task 5 的转换输出写到侧目录 `data/movies-align-v041/`,**不得覆盖 `data/movies/`**(会作废其下的抽取 checkpoints;正式重转在阶段 3 决策后配 `--from-scratch`)。

## Review Focus

1. **预筛把覆盖兜底目标一起裁掉**(B2,字面实现必踩)→ Task 2 `test_align_cues_rescues_verbatim_piece_of_long_line`(ub≈0.33 的长度悬殊对仍被救回)。
2. **兄弟候选改绑既有正确绑定**(B1 变体:平分或低分抢走窗口真身行)→ Task 2 `test_last_line_candidate_loses_ties_to_window`(现有单调测试)+ `test_last_line_never_beats_higher_scoring_window_match`。
3. **散装 token 覆盖假阳性**(Elois/party 两案实证:token 全在场但散落不同句子)→ Task 2 `test_coverage_rejects_scattered_tokens`。
4. **_norm 漂移破坏既有匹配 / l→i 误伤含 l 的正常词** → Task 1 `test_norm_unchanged_for_clean_text` + `test_norm_maps_ocr_l_to_i`(token 级映射,"trouble" 不变)。
5. **audit 的 legacy 基线偏离 v0.4.0 真实产出**(基线错则审计全废)→ Task 5 三部影片 legacy@0.6 必须复现旧 convert_report 值(929/519、408/489、1277/309)。

---

### Task 1: `_norm` token 级 OCR 归一 + `resolve_speaker` 荣誉前缀四级

**Files:**
- Modify: `src/movie_kg/ingestion/movienet/align.py:44-45`(`_norm`)、`align.py:92-101`(`resolve_speaker`)
- Test: `tests/test_movienet_align.py`(追加)

**Interfaces:**
- Produces: `_norm(s: str) -> str`(token 级 `{"l": "i", "lt": "it"}` 映射,Task 2 依赖);`resolve_speaker(speaker: str, roster: list[str]) -> str | None`(四级超集,Task 3 的 convert 与 Task 4 的 audit 依赖)

- [ ] **Step 1: 写失败测试**

```python
from movie_kg.ingestion.movienet.align import _norm, resolve_speaker


def test_norm_maps_ocr_l_to_i():
    # Hackers 字幕 15.5% 含 OCR 的 "l"(应为 I)与 "lt"(应为 it);
    # token 级映射,不影响含 l 的正常词(评审 M3)
    assert _norm("l got in trouble with lt's choice") == \
        "i got in trouble with it s choice"
    assert _norm("trouble lullaby") == "trouble lullaby"


def test_norm_unchanged_for_clean_text():
    # 既有行为不变钉:_norm 被对齐全链路使用,漂移会破坏既有匹配
    assert _norm("You can't handle the truth.") == "you can t handle the truth"


def test_resolve_speaker_strips_honorific_as_retry():
    # 评审 M6:剥前缀作为精确/词级失败后的重试,不降级现有路径
    roster = ["Lauren Murphy", "Mr. White"]
    assert resolve_speaker("MRS. MURPHY", roster) == "Lauren Murphy"  # 剥后词级
    assert resolve_speaker("MR. WHITE", roster) == "Mr. White"        # 精确路径不变
    assert resolve_speaker("MRS.", roster) is None                    # 剥后为空


def test_resolve_speaker_superset_of_two_level():
    # 旧版两级行为逐例保持(超集原则)
    for label, expect in [("MATTY", "Matty Walker"),
                          ("ned racine", "Ned Racine"),
                          ("GHOST", None)]:
        assert resolve_speaker(label, ["Matty Walker", "Ned Racine"]) == expect
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_movienet_align.py -k "norm or honorific or superset" -v`
Expected: FAIL(`_norm("l ...")` 返回 `"l got ..."`;`MRS. MURPHY` 返回 None)

- [ ] **Step 3: 实现**

`_norm` 改为(映射位置是关键决策:token 级、在 split 之后):

```python
_L2I = {"l": "i", "lt": "it"}


def _norm(s: str) -> str:
    toks = re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()
    return " ".join(_L2I.get(t, t) for t in toks)
```

`resolve_speaker` 改为四级(原两级作为前两级原样保留,stripped 路径整体后置):

```python
_HONORIFIC = re.compile(r"^(?:mr|mrs|ms|dr)[.\s]+", re.IGNORECASE)


def resolve_speaker(speaker: str, roster: list[str]) -> str | None:
    """剧本角色标签 → 元数据角色名(四级超集):
    全等 → 名/姓词级 →(剥 mr/mrs/ms/dr 前缀后)全等 → 词级。"""
    s = speaker.strip().lower()
    stripped = _HONORIFIC.sub("", s).strip()
    for cand in (s, stripped):
        if not cand:
            continue
        for c in roster:
            if c.lower() == cand:
                return c
    for cand in (s, stripped):
        if not cand:
            continue
        for c in roster:
            if cand in [w for w in re.split(r"[ .]", c.lower()) if w]:
                return c
    return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_movienet_align.py -v`
Expected: 全部 PASS(含既有测试——`_norm` 变化不破坏 `test_align_cues_matches_in_order` 等)

- [ ] **Step 5: 提交**

```bash
git add src/movie_kg/ingestion/movienet/align.py tests/test_movienet_align.py
git commit -m "feat: 对齐归一化吸收 Hackers 字幕 OCR 的 l/lt + 说话人解析增荣誉前缀重试(四级超集)"
```

---

### Task 2: `align_detailed` 核心循环(预归一化/预筛/兄弟候选/短门/覆盖兜底)

**Files:**
- Modify: `src/movie_kg/ingestion/movienet/align.py:48-89`(`_score` 不动;`align_cues` 重构为 `align_detailed` + 薄包装;新增 `_score_prefiltered`)
- Test: `tests/test_movienet_align.py`(追加)

**Interfaces:**
- Consumes: Task 1 的 `_norm`
- Produces:
  - `align_detailed(cues: list[Cue], script_lines: list[ScriptLine], threshold: float = 0.6, lookahead: int = 60) -> tuple[list[str | None], list[int | None], list[float | None]]` —— (speakers, matched_line_idx, matched_score);cov 兜底命中时 score 记 recall 值。Task 4 的 audit 依赖此返回。
  - `align_cues(cues, script_lines, threshold=0.6, lookahead=60) -> list[str | None]` = `align_detailed(...)[0]`(convert.py 调用面不变)
  - `_score_prefiltered(nc: str, ns: str, floor: float) -> float`(模块私有,测试导入)

**算法规格(签名与测试不决定的部分,实现按此写;测试内的朴素参考实现也按此独立转写):**

```text
预计算(每剧本一次): norm_lines[i] = _norm(script_lines[i].text)
                     line_toks[i] = frozenset(norm_lines[i].split())
逐 cue:
  nc = _norm(cue.text);为空跳过
  cue_toks = frozenset(nc.split());eff_thr = 0.6 if len(cue_toks) < 3 else threshold
  # base 路径
  窗口 W = [j, min(j+lookahead, len));对 W 内非空 ns 用 _score_prefiltered(nc, ns, eff_thr)
  窗口胜者 = W 内 score ≥ eff_thr 的最大者(严格 >,平分取最早 idx)
  兄弟候选 e = j-1(仅 j>0):score(e) ≥ eff_thr 且(窗口胜者为 None 或 score(e) > 窗口胜者分)才取代之
  上述无胜者 → 全局回退:扫描全剧本中不在 W 内的行(同 _score_prefiltered),取 ≥ eff_thr 最大者
  # 覆盖兜底(base 全无胜者时才生效;不受预筛门控)
  候选空间 = W ∪ {e} ∪ 全剧本;行合格 ⇔ len(cue_toks) ≥ 4 且 len(line_toks[行]) ≥ 4
      且 recall = |cue_toks ∩ line_toks[行]| / len(cue_toks) ≥ 0.85
      且 contig = len(nc) if nc in ns else find_longest_match(0,len(nc),0,len(ns)).size
                ≥ 0.5 * len(nc)
  兜底胜者 = 合格行中 (recall, contig, -idx) 最大
  命中(任一路径, idx)→ speakers/idx/score 记录;j = idx + 1(idx = j-1 时 j 不动)
```

`_score_prefiltered(nc, ns, floor)`:先算上界 `ub = 2*min(la,lb)/(la+lb)`(la/lb 为长度);`ub < floor` 或任一侧为空 → 0.0;否则与 `_score` 同值(containment 分支或 difflib ratio;`quick_ratio < floor` 时 0.0)。上界同时覆盖 containment(min/max ≤ ub)与 ratio 两分支,探针 3/3 PASS 已验证等价性。

- [ ] **Step 1: 写失败测试(行为钉)**

```python
def test_align_cues_rescues_verbatim_piece_of_long_line():
    # 独白病态(评审 B2/M4 探针实证 'I'm gonna die.'):containment 分
    # 13/67≈0.19、长度比 0.325 < 0.6 → base 与预筛都到不了;
    # 覆盖兜底(recall=1.0、连续段=全长)救回,且预筛不得门控它
    script = parse_script(
        "                    MR. ORANGE\n"
        "          All this blood is scaring the shit outta me. "
        "I'm gonna die, I know it.\n")
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\nI'm gonna die.\n")
    assert align_cues(cues, script) == ["MR. ORANGE"]


def test_coverage_rejects_scattered_tokens():
    # Elois 反例(已验剧本原文):cue 的 token 全部在场但乱序散落
    # (recall=1.0、difflib≈0.37<0.6)→ 最长连续段 18/42 < 50% → 拒绑
    # (评审 M1c/F1;fixture 必须乱序——顺序散落时 difflib 子序列比可 ≥0.6)
    line = ("So nobody knows for sure what really happened back then. "
            "One night, anyway, she meets Elois, who waits and "
            "plays it real cool.")
    script = parse_script(f"                    EDDIE\n          {line}\n")
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\n"
                     "So anyway, one night she plays it real cool.\n")
    assert align_cues(cues, script) == [None]


def test_sibling_pieces_reuse_last_matched_line():
    # 一行拆两 cue(兄弟结构,一行 2.4 cue 是常态):首片段覆盖兜底命中后,
    # 次片段经兄弟候选(j-1)复用同一行(评审 m4 根因)
    script = parse_script(
        "                    BOB\n"
        "          alpha bravo charlie delta echo foxtrot golf hotel\n\n"
        "                    ALICE\n          something entirely different here\n")
    cues = parse_srt(
        "1\n00:00:00,100 --> 00:00:00,200\nalpha bravo charlie delta\n\n"
        "2\n00:00:00,300 --> 00:00:00,400\necho foxtrot golf hotel\n")
    assert align_cues(cues, script) == ["BOB", "BOB"]


def test_last_line_candidate_loses_ties_to_window():
    # 兄弟候选平分不抢窗口行:单调语义不变(现有测试即本钉,此处显式重申)
    script = parse_script(
        "                    BOB\n          hello there\n\n"
        "                    ALICE\n          hello there\n")
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\nhello there\n\n"
                     "2\n00:00:00,300 --> 00:00:00,400\nhello there\n")
    assert align_cues(cues, script) == ["BOB", "ALICE"]


def test_last_line_never_beats_higher_scoring_window_match():
    # 兄弟候选严格更优才生效:B1 翻转例的梦台词——窗口内 1.0 真身行
    # 不被低分兄弟行改绑
    script = parse_script(
        "                    PINK\n          wake up and apologize now\n\n"
        "                    MR. WHITE\n"
        "          you shoot me in a dream, you better wake up and apologize\n")
    cues = parse_srt(
        "1\n00:00:00,100 --> 00:00:00,200\nwake up and apologize now\n\n"
        "2\n00:00:00,300 --> 00:00:00,400\n"
        "You shoot me in a dream, you better wake up and apologize.\n")
    assert align_cues(cues, script) == ["PINK", "MR. WHITE"]


def test_short_cue_guard_uses_dedup_token_count():
    # 短门口径 = 去重 token:'Yak, yak, yak.' 去重后 1 词 → 门槛抬至 0.6,
    # 阈值降到 0.4 也不放行(阶段 2 的保护,先钉口径)
    script = parse_script("                    BOB\n          yak yak yak indeed ok fine\n")
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\nYak, yak, yak.\n")
    assert align_cues(cues, script, threshold=0.4) == [None]
    # 对照:阈值 0.4 下 3+ 去重 token 的 cue 正常放行(短门只护短 cue)
    cues2 = parse_srt("1\n00:00:00,100 --> 00:00:00,200\n"
                      "yak yak yak indeed and more words here\n")
    script2 = parse_script(
        "                    BOB\n          yak yak yak indeed and more words here\n")
    assert align_cues(cues2, script2, threshold=0.4) == ["BOB"]
```

- [ ] **Step 2: 写失败测试(等价性钉)**

```python
import random


def test_score_prefiltered_equivalence():
    # 预筛只裁必然低于 floor 的对:阈值判定与数值完全等价(300 随机组)
    from movie_kg.ingestion.movienet.align import _score, _score_prefiltered
    rng = random.Random(7)
    words = ["alpha", "bravo", "charlie", "delta", "x", "y", "l", "it"]
    for _ in range(300):
        nc = " ".join(rng.choice(words) for _ in range(rng.randint(1, 8)))
        ns = " ".join(rng.choice(words) for _ in range(rng.randint(1, 12)))
        for floor in (0.4, 0.45, 0.6):
            fast = _score_prefiltered(nc, ns, floor)
            slow = _score(nc, ns)
            assert (fast >= floor) == (slow >= floor)
            if fast >= floor:
                assert abs(fast - slow) < 1e-12


def test_align_detailed_matches_naive_reference():
    # 朴素参考(无预筛/无缓存,按算法规格独立转写)与生产实现产出一致
    # (评审 M5:复杂度被低估,等价性需要参考实现)
    from movie_kg.ingestion.movienet.align import align_detailed
    from movie_kg.ingestion.movienet.subs import Cue as _Cue  # 若 Cue 不可直接构造则用 parse_srt 造

    def naive(cues, script_lines, threshold, lookahead):
        # 按计划「算法规格」逐条直写,不做任何性能捷径:
        # 逐 cue 每次现算 _norm/_score;窗口扫描后单独评 j-1(严格 >);
        # 全局回退;覆盖兜底(现算 token 集与 find_longest_match);j = idx+1
        ...  # 实现即规格转写,约 45 行

    rng = random.Random(11)
    speakers_pool = ["BOB", "ALICE", "CURLY"]
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot",
             "yak", "hello", "there", "now"]
    for _ in range(300):
        lines = [ScriptLine(
            speaker=rng.choice(speakers_pool),
            text=" ".join(rng.choice(words) for _ in range(rng.randint(2, 9))))
            for _ in range(rng.randint(2, 10))]
        srt = "\n\n".join(
            f"{i + 1}\n00:00:0{i % 10},{100} --> 00:00:0{i % 10},{200}\n"
            + " ".join(rng.choice(words) for _ in range(rng.randint(1, 6)))
            for i in range(rng.randint(1, 8))) + "\n"
        cues = parse_srt(srt)
        thr = rng.choice([0.4, 0.45, 0.6])
        look = rng.choice([1, 3, 60])
        assert align_detailed(cues, lines, threshold=thr, lookahead=look) \
            == naive(cues, lines, thr, look)
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_movienet_align.py -v`
Expected: 新增测试 FAIL(现 `align_cues` 无覆盖兜底/兄弟候选/短门;`align_detailed`/`_score_prefiltered` 不存在)

- [ ] **Step 4: 实现 `align_detailed` + `_score_prefiltered`,按算法规格**

`align_cues` 改为 `return align_detailed(cues, script_lines, threshold, lookahead)[0]`。`_score` 保持不动。注意:预筛只在 base 两处循环(`_score_prefiltered`);覆盖兜底用预计算的 `line_toks` 与 `find_longest_match`,从不经过预筛。

- [ ] **Step 5: 跑全文件测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_movienet_align.py -v`
Expected: 全部 PASS,含既有 12 个测试(`test_align_cues_matches_split_cue_by_containment` 的 `out[1]` 仍为 None∈(None,"MATTY") ✓;`test_align_cues_global_fallback_resyncs` 的全局回退行为不变 ✓)

- [ ] **Step 6: 提交**

```bash
git add src/movie_kg/ingestion/movienet/align.py tests/test_movienet_align.py
git commit -m "feat: 对齐核心重构 align_detailed——预归一化+长度预筛(10min→秒级)、覆盖兜底(recall≥0.85+连续段≥50%)、兄弟候选(严格更优)、短门(去重<3词抬0.6)"
```

---

### Task 3: `convert.py` 解析字典并入 meta.cast + DESIGN.md 修订

**Files:**
- Modify: `src/movie_kg/ingestion/movienet/convert.py:260-262`(`convert()` 内 roster 构造)、`docs/DESIGN.md:222`(§3.4 stage 4 行)、`docs/DESIGN.md` 变更记录表(追加 v0.4.1 行)
- Test: `tests/test_movienet_convert.py`(追加)

**Interfaces:**
- Consumes: Task 1 `resolve_speaker`(四级)、Task 2 `align_cues`(签名不变)
- Produces: `convert()` 行为变更——解析字典 = 视觉 roster ∪ meta.cast;Task 4 audit 的 resolve 口径与此一致

- [ ] **Step 1: 写失败测试**

```python
SRT_M = """1
00:00:00,500 --> 00:00:01,200
You can't handle the truth.

2
00:00:03,500 --> 00:00:03,900
The money is gone.
"""

SCRIPT_M = """EXT. OFFICE - DAY

                    MATTY
          You can't handle the truth.

                    SALLY
          The money is gone.
"""


def test_convert_resolves_speaker_from_meta_cast_beyond_visual_roster(tmp_path):
    # 评审 M2:roster 10 人是视觉标注交集的产物;meta.cast(24/65/69)才是
    # cast 全集。视觉未标注的说话人(SALLY)经 meta.cast 解析保留,
    # 并进入 scene.characters(character eval 的 gold 随之自动跟进)
    ann = _ann()
    ann["cast"] = [{"id": "x1", "pid": "nm1", "shot_idx": 0, "img_idx": 0}]
    # 场景2 视觉 roster 自然为空(cast 实例只在 shot 0),shot 区间沿用 _ann
    meta = {"imdb_id": "tt0000001", "title": "Mini (2020)",
            "cast": [{"id": "nm1", "name": "Actor One", "character": "Matty Walker"},
                     {"id": "nm3", "name": "Actor Three", "character": "Sally"}]}
    src = tmp_path / "src"
    for sub in ("annotation", "subtitle", "script", "meta", "keyframes"):
        (src / sub).mkdir(parents=True, exist_ok=True)
    (src / "annotation" / "tt0000001.json").write_text(json.dumps(ann), encoding="utf-8")
    (src / "meta" / "tt0000001.json").write_text(json.dumps(meta), encoding="utf-8")
    (src / "subtitle" / "tt0000001.srt").write_text(SRT_M, encoding="utf-8")
    (src / "script" / "tt0000001.script").write_text(SCRIPT_M, encoding="utf-8")
    with tarfile.open(src / "keyframes" / "tt0000001.tar", "w") as tf:
        for shot in range(4):     # 场景引用 shot 0-3,tar 必须全覆盖
            for img in range(3):
                data = b"\xff\xd8\xff\xd9"
                ti = tarfile.TarInfo(f"tt0000001/shot_{shot:04d}_img_{img}.jpg")
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
    out = tmp_path / "out"
    convert("tt0000001", src, out)
    movie = load_movie(out)
    s2 = movie.scenes[1]
    assert [d.char for d in s2.meta.dialogues] == ["Sally"]      # 不再 speaker_not_in_roster
    assert "Sally" in [c.name for c in s2.meta.characters]       # 说话人并入场景名单(gold 跟进)
    report = (out / "convert_report.md").read_text(encoding="utf-8")
    assert "说话人不在 roster 0" in report
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_movienet_convert.py::test_convert_resolves_speaker_from_meta_cast_beyond_visual_roster -v`
Expected: FAIL(Sally 被计入 `speaker_not_in_roster`,dialogues 为空)

- [ ] **Step 3: 实现(convert.py `convert()` 内,替换现 movie_roster 构造段)**

```python
    movie_roster = sorted({n for r in cast_rosters for n in r})
    # 说话人解析字典 = 视觉 roster ∪ meta.cast 全集(评审 M2:视觉交集
    # 只有 10-12 人,meta.cast 有 24/65/69;未视觉标注的说话人不再丢弃)
    meta_roster = sorted({c["character"].strip() for c in meta.get("cast", [])
                          if c.get("character", "").strip()})
    resolve_roster = sorted(set(movie_roster) | set(meta_roster))
    per_dlg, dlg_stats = scene_dialogues(srt_text, script_text, scenes,
                                         resolve_roster)
```

`scene_characters`(视觉名单,场景 characters 的基底)不动。

- [ ] **Step 4: 修订 DESIGN.md**

§3.4 stage 4 行(现 222 行)替换为:

```markdown
| 4 | 台词 | SRT(影片绝对时间,毫秒截断)按场景窗口切片 → 剧本对齐(difflib 相似度 ≥0.6 + 长度预筛加速;base 无胜者时覆盖兜底:cue/行去重 token ≥4、召回 ≥0.85、最长公共连续段 ≥ cue 一半;兄弟候选(上一命中行,严格更优才胜出);短 cue(<3 去重 token)门槛抬至 0.6;token 级 l→i/lt→it OCR 归一)→ 说话人归解析字典(视觉 roster ∪ meta.cast;全等 → 名/姓词级 → 剥 mr/mrs/ms/dr 前缀重试);未对上或不在解析字典 → 丢弃,分别计数;`kg movienet align-audit` 提供新旧对照审计 |
```

变更记录表追加一行(v0.4.1,日期 2026-10-19,内容一句话概括本计划六项改进 + 审计命令)。

- [ ] **Step 5: 跑全文件测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_movienet_convert.py tests/test_movienet_align.py -v`
Expected: 全部 PASS(既有 `test_convert_end_to_end` 的 roster 恰为 meta.cast 子集,不受影响)

- [ ] **Step 6: 提交**

```bash
git add src/movie_kg/ingestion/movienet/convert.py docs/DESIGN.md tests/test_movienet_convert.py
git commit -m "feat: 说话人解析字典并入 meta.cast 全集(视觉交集 10 人 → cast 全集 24/65/69)+ DESIGN §3.4 同步"
```

---

### Task 4: 审计工具 `audit.py` + `kg movienet align-audit`

**Files:**
- Create: `src/movie_kg/ingestion/movienet/audit.py`、`tests/test_movienet_audit.py`
- Modify: `src/movie_kg/cli.py`(movienet 子命令追加;参考现有 fetch/convert 命令与 `tests/test_cli_build.py` 的 CliRunner 测试模式)

**Interfaces:**
- Consumes: Task 2 `align_detailed`、`align.py` 的 `_score`/`ScriptLine`;`convert.py` 的 `calibrate_fps`/`scene_windows`/`scene_characters`;`subs.parse_srt`、`align.parse_script`
- Produces:
  - `align_legacy(cues, script_lines, threshold=0.6, lookahead=60) -> tuple[list[str | None], list[int | None]]` —— v0.4.0 语义(旧 `_norm`(无 l→i)、无覆盖兜底/兄弟候选/短门/荣誉剥前缀;带预归一化+预筛——探针 3/3 PASS 已证与旧产出等价)
  - `diff_alignment(cues, legacy: tuple, new: tuple, resolve_roster: list[str]) -> dict` —— 键:`both_matched`/`flips`(旧新都匹配但说话人不同)/`new_only`/`legacy_only`/`kept_legacy`/`kept_new`(roster 解析口径)
  - `stratified_sample(cues, new_idx, new_score, bands: list[tuple[float, float]], per_band: int, seed: int = 42) -> list[dict]` —— 行键:`cue_idx, band, cue_text, old_speaker, new_speaker, matched_speaker, matched_text, dedup_tokens, l_ocr, sibling, label`(label 留空供人工填)
  - `run_audit(movie: str, src: Path, out_dir: Path, thresholds: list[float] = [0.6], sample_n: int = 100) -> Path` —— 逐阈值跑新对齐,与 legacy@0.6 对照;写 `<out_dir>/<movie>.md`(汇总表)与 `<out_dir>/<movie>_samples.csv`
  - CLI:`kg movienet align-audit --movie tt0105236 [--src data/raw/movienet] [--out eval/align_audit] [--thresholds 0.6] [--sample 100]`

- [ ] **Step 1: 写失败测试**

```python
import json

from movie_kg.ingestion.movienet.audit import (
    align_legacy, diff_alignment, stratified_sample)

SRT = """1
00:00:00,500 --> 00:00:01,200
I'm gonna die.

2
00:00:02,000 --> 00:00:02,400
Totally unrelated words.
"""

SCRIPT = """                    MR. ORANGE
          All this blood is scaring the shit outta me. I'm gonna die, I know it.

                    ALICE
          different content entirely here now
"""


def test_align_legacy_has_v040_semantics():
    # legacy 无覆盖兜底:逐字包含长行的 cue(新实现能救)在 legacy 下未匹配
    cues = parse_srt(SRT)
    lines = parse_script(SCRIPT)
    speakers, idx = align_legacy(cues, lines)
    assert speakers[0] is None            # 旧语义:containment 0.19 < 0.6 → None
    from movie_kg.ingestion.movienet.align import align_detailed
    new_spk, new_idx, _ = align_detailed(cues, lines)
    assert new_spk[0] == "MR. ORANGE"     # 新实现覆盖兜底救回
    assert new_spk[1] is None and speakers[1] is None


def test_diff_alignment_counts_flips_and_kept():
    cues = parse_srt(SRT)
    lines = parse_script(SCRIPT)
    legacy = align_legacy(cues, lines)
    new = align_detailed(cues, lines)
    d = diff_alignment(cues, legacy, new, ["Mr. Orange", "Alice"])
    assert d["new_only"] == 1 and d["flips"] == 0
    assert d["kept_new"] == 1 and d["kept_legacy"] == 0


def test_stratified_sample_rows_and_flags():
    cues = parse_srt(SRT)
    lines = parse_script(SCRIPT)
    _, new_idx, new_score = align_detailed(cues, lines)
    rows = stratified_sample(cues, new_idx, new_score,
                             bands=[(0.6, 1.01), (0.0, 0.6)], per_band=5)
    assert rows and all(set(r) == {"cue_idx", "band", "cue_text", "old_speaker",
                                   "new_speaker", "matched_speaker", "matched_text",
                                   "dedup_tokens", "l_ocr", "sibling", "label"}
                        for r in rows)
    assert all(r["label"] == "" for r in rows)


def test_run_audit_writes_report_and_csv(tmp_path):
    # 合成一部最小影片(annotation/meta/srt/script 四源),跑通全流程
    ...  # 复用 test_movienet_convert.py 的 _make_src 思路造 src(可 import 或复制精简版)
    out = run_audit("tt0000001", src, tmp_path, thresholds=[0.6], sample_n=10)
    assert (out.parent / "tt0000001.md").exists()
    assert (out.parent / "tt0000001_samples.csv").exists()
```

CLI 测试(参考 `tests/test_cli_build.py` 的 CliRunner 模式):invoke `["movienet", "align-audit", "--movie", "tt0000001", "--src", str(src), "--out", str(tmp)]` → exit_code == 0 且报告文件存在。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_movienet_audit.py -v`
Expected: FAIL(ModuleNotFoundError: audit)

- [ ] **Step 3: 实现 audit.py**

要点:`align_legacy` 内嵌私有 `_norm_v040`(旧归一化:无 l→i)复用 `_score`,循环结构同 v0.4.0 `align_cues`(单调窗口 + 全局回退,无兄弟候选/短门/覆盖),但用预归一化 + `_score_prefiltered`(性能;等价性由 Task 5 真实数据交叉核对兜底)。`l_ocr` 标记 = cue 原文 split 后含 `"l"` 或 `"lt"` token;`sibling` 标记 = new_idx[cue] == new_idx[cue-1](同一行复用)。`run_audit` 内部按 Task 3 同款构造 `resolve_roster`(视觉 roster ∪ meta.cast)供 kept% 与可解析统计。报告 `<movie>.md` 逐阈值小节:matched / kept%(解析口径)/ flips / new_only / legacy_only / flips 中旧可解析数。

- [ ] **Step 4: 跑测试确认通过 + CLI 测试**

Run: `.venv/Scripts/python.exe -m pytest tests/test_movienet_audit.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/movie_kg/ingestion/movienet/audit.py tests/test_movienet_audit.py src/movie_kg/cli.py
git commit -m "feat: kg movienet align-audit——legacy(v0.4.0 语义)vs 新对齐逐 cue 对照、翻转统计与分层抽样导出"
```

---

### Task 5: 三部影片实跑与阶段 1 验收(无新代码)

**Files:**
- 产出: `eval/align_audit/`(报告 + CSV,提交);`data/movies-align-v041/`(git-ignored 侧目录,**不提交**)

**Interfaces:**
- Consumes: Task 3/4 的 convert 与 align-audit;主仓 `data/raw/movienet/`(绝对路径,worktree 内无此数据)

- [ ] **Step 1: 侧目录重转换三部(主仓数据绝对路径)**

```bash
.venv/Scripts/kg movienet convert --movie tt0105236 \
  --src <主仓>/data/raw/movienet --out <主仓>/data/movies-align-v041/tt0105236
# tt0113243、tt0117500 同理
```

Expected: 三份 convert_report 生成;kept% 相比旧值(519/1512=34%、489/1108=44%、309/1763=18%)上升;`data/movies/` 未被触碰。

- [ ] **Step 2: legacy 基线交叉核对(评审 Focus #5)**

从三部 align-audit 报告的 legacy@0.6 汇总读取 unmatched/kept,必须等于旧 convert_report 值:**RD (929, 519)、Hackers (408, 489)、Rock (1277, 309)**。不等 → audit 的 legacy 实现有误,修复后重跑,不得继续。

- [ ] **Step 3: 跑审计 + 记录翻转**

```bash
.venv/Scripts/kg movienet align-audit --movie tt0105236 \
  --thresholds 0.6 --sample 100   # 三部同理;输出 eval/align_audit/
```

Expected: 每片得到 kept% 汇总、flips/new_only/legacy_only 计数、100 条分层抽样 CSV。**验收判据:flips 应接近 0**(兄弟候选与 l→i 是仅有的改绑源;若某片 flips > 新增匹配的 5%,暂停并分析翻转样本后再继续)。

- [ ] **Step 4: 汇总与提交**

汇总表(旧 kept% → 新 kept%、flips、new_only)写入 `eval/align_audit/README.md`;git add 报告与 CSV 并提交:

```bash
git add eval/align_audit/
git commit -m "docs: 台词对齐阶段 1 验收数据(三部 kept% 对比 + legacy 交叉核对 + 翻转审计)"
```

- [ ] **Step 5: 向用户汇报并请示阶段 2**

汇报 kept% 提升与翻转数据;阶段 2(阈值 0.5/0.45 的分层标注审计)与阶段 3(图谱重建)由用户决定后启动。

---

## Self-Review 记录

- **Spec 覆盖**:设计 v2 的 8 项——①性能(预归一化+预筛)Task 2;②meta.cast Task 3;③l→i Task 1;④荣誉前缀 Task 1;⑤覆盖硬化(兜底制/下限 4/连续性/多候选取优)Task 2;⑥兄弟复用 Task 2;⑦审计工具 Task 4+5;⑧DESIGN.md 修订 Task 3。评估口径调整经核查无需改代码(gold 随 scene JSON 自动跟进,Task 3 测试钉住该事实)。
- **步骤扫描**:无 TBD;算法规格块是「签名与测试不决定的算法」属正当 body;naive 参考实现标注为规格转写。
- **类型一致**:`align_detailed` 返回三元组在 Task 2 定义、Task 4 消费;`diff_alignment`/`stratified_sample`/`run_audit` 签名在 Task 4 Interfaces 固定。
- **Review Focus**:5 项各有归属测试(见上表)。
- **比例**:测试与算法规格占大头(本计划的核心风险即语义细节),实现 body 均为签名级。
