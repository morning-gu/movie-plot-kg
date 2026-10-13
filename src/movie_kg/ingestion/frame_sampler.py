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
