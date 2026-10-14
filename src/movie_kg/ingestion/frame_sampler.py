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
