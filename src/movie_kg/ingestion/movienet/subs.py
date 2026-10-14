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
    # 分隔行可能含空格/制表符(真实抓取数据常见):\n\s*\n 严格宽于 "\n\n",
    # 否则下一条 cue 会被静默吞进上一条的正文
    for block in re.split(r"\n\s*\n", text.strip().replace("\r\n", "\n")):
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
