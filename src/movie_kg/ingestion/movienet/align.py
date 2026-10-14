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
