"""剧本解析与 SRT↔剧本台词对齐(DESIGN §3.4 stage 4)。"""
import difflib
import re
from dataclasses import dataclass

from movie_kg.ingestion.movienet.subs import Cue

# 角色行:深缩进(≥8 空格,或 ≥3 制表符——The Rock 等剧本用 tab 缩进:说话人 5-6 tab、
# 对白 2 tab)+ 全大写短标签,可带 (CONT'D)/(V.O.) 等后缀
_SPEAKER = re.compile(r"^(?:\t{3,}| {8,})([A-Z][A-Z0-9 .'&\-]{1,40})(\(.*?\))?\s*$")
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


_L2I = {"l": "i", "lt": "it"}  # Hackers 字幕 OCR:独立 "l" 应为 I、"lt" 应为 it


def _norm(s: str) -> str:
    toks = re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()
    return " ".join(_L2I.get(t, t) for t in toks)


def _score(nc: str, ns: str) -> float:
    """归一化文本相似度:包含关系(短/长)或 difflib ratio。"""
    if nc in ns or ns in nc:
        return min(len(nc), len(ns)) / max(len(nc), len(ns))
    return difflib.SequenceMatcher(None, nc, ns).ratio()


def _score_prefiltered(nc: str, ns: str, floor: float) -> float:
    """_score + 长度预筛:ub = 2·min/(la+lb) 同时是 containment(min/max)
    与 difflib ratio 的上界,低于 floor 的对不必构造 SequenceMatcher。
    注意 autojunk 保持默认 True——v0.4.0 等价性(legacy 基线)依赖它,
    勿与 _cov_pass 的 autojunk=False「一致化」。"""
    if not nc or not ns:
        return 0.0
    la, lb = len(nc), len(ns)
    if 2 * min(la, lb) / (la + lb) < floor:
        return 0.0
    if nc in ns or ns in nc:
        return min(la, lb) / max(la, lb)
    sm = difflib.SequenceMatcher(None, nc, ns)
    if sm.real_quick_ratio() < floor or sm.quick_ratio() < floor:
        return 0.0
    return sm.ratio()


def _cov_pass(nc: str, ns: str, cue_toks: frozenset,
              line_toks: frozenset) -> tuple[float, int] | None:
    """覆盖兜底合格判定:cue/行去重 token ≥4、cue token 召回 ≥0.85、
    最长公共连续段 ≥ cue 一半(散落 token 不算——拼接长行上的叙事撞车,
    试点评审 M1c)。返回 (recall, contig) 供候选排序;不合格 None。
    不过长度预筛:独白包含对的长度比可低至 0.02,预筛会误杀(评审 B2)。"""
    if len(cue_toks) < 4 or len(line_toks) < 4:
        return None
    recall = len(cue_toks & line_toks) / len(cue_toks)
    if recall < 0.85:
        return None
    if nc in ns:
        contig = len(nc)
    else:
        # autojunk=False: b>=200 字符时 autojunk 会把高频字符移出种子,
        # 近逐字公共段塌缩为 ~1,独白长行(覆盖兜底主战场)大量漏救
        # (终审实证 RD cue100: 1 vs 真实 31);_score 系保持 autojunk=True
        # 以维持 v0.4.0 等价性,两处不对称是有意的。
        contig = difflib.SequenceMatcher(None, nc, ns,
                                         autojunk=False).find_longest_match(
            0, len(nc), 0, len(ns)).size
    if contig < 0.5 * len(nc):
        return None
    return recall, contig


def align_detailed(cues: list[Cue], script_lines: list[ScriptLine],
                   threshold: float = 0.6,
                   lookahead: int = 60) -> tuple[list[str | None],
                                                 list[int | None],
                                                 list[float | None]]:
    """逐 cue 对齐,返回 (剧本说话人, 命中行 idx, 命中分;未匹配 None)。

    规则(DESIGN §3.4 stage 4):
    - base:窗口 [j, j+lookahead) 取最高分(平分取最早);兄弟候选 j-1
      须严格更优才取代窗口胜者(平分窗口优先,保单调语义);均无胜者时
      全局回退(窗口外全剧本取最大)——影片非线性叙事/删改段落失步重同步。
    - 短门:cue 去重 token < 3 时有效阈值抬至 0.6(短串 difflib 无判别力)。
    - 覆盖兜底:base 全无胜者时,全剧本找近逐字包含行,取
      (recall, 连续段长, -idx) 最大;命中分记 recall。
    """
    norm_lines = [_norm(l.text) for l in script_lines]
    line_toks = [frozenset(nl.split()) for nl in norm_lines]
    speakers: list[str | None] = [None] * len(cues)
    idxs: list[int | None] = [None] * len(cues)
    scores: list[float | None] = [None] * len(cues)
    j = 0
    for ci, cue in enumerate(cues):
        nc = _norm(cue.text)
        if not nc:
            continue
        cue_toks = frozenset(nc.split())
        eff_thr = 0.6 if len(cue_toks) < 3 else threshold
        w_hi = min(j + lookahead, len(script_lines))

        def _base_score(jj: int) -> float:
            # token 交集下限:零交集的纯字符相似(如 atropine 案例,
            # difflib 可达 0.68)一律拒绝——分数再高不做数(阶段 2)
            if not (cue_toks & line_toks[jj]):
                return 0.0
            return _score_prefiltered(nc, norm_lines[jj], eff_thr)

        best: tuple[float, int] | None = None
        for jj in range(j, w_hi):                       # base: 窗口
            ns = norm_lines[jj]
            if not ns:
                continue
            s = _base_score(jj)
            if s >= eff_thr and (best is None or s > best[0]):
                best = (s, jj)
        if j > 0 and norm_lines[j - 1]:                 # 兄弟候选:严格更优
            s = _base_score(j - 1)
            if s >= eff_thr and (best is None or s > best[0]):
                best = (s, j - 1)
        if best is None:                                # base: 全局回退
            for jj in range(len(script_lines)):
                if j <= jj < w_hi:
                    continue
                ns = norm_lines[jj]
                if not ns:
                    continue
                s = _base_score(jj)
                if s >= eff_thr and (best is None or s > best[0]):
                    best = (s, jj)
        if best is None:                                # 覆盖兜底
            cov_best: tuple[float, int, int] | None = None
            for jj, ns in enumerate(norm_lines):
                if not ns:
                    continue
                r = _cov_pass(nc, ns, cue_toks, line_toks[jj])
                if r is not None:
                    key = (r[0], r[1], -jj)
                    if cov_best is None or key > cov_best:
                        cov_best = key
            if cov_best is not None:
                best = (cov_best[0], -cov_best[2])
        if best is not None:
            speakers[ci] = script_lines[best[1]].speaker
            idxs[ci] = best[1]
            scores[ci] = best[0]
            j = best[1] + 1
    return speakers, idxs, scores


def align_cues(cues: list[Cue], script_lines: list[ScriptLine],
               threshold: float = 0.6, lookahead: int = 60) -> list[str | None]:
    """align_detailed 的说话人视图(convert 调用面)。"""
    return align_detailed(cues, script_lines, threshold, lookahead)[0]


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
