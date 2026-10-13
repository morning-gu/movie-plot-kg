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
