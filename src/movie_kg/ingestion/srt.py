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
