import io
from dataclasses import dataclass
from pathlib import Path

import av

from movie_kg.ingestion.srt import parse_ts


@dataclass
class Frame:
    ts: float
    jpeg: bytes


def sample_frames(video_path: Path, keyframes: list[str], max_frames: int = 16) -> list[Frame]:
    """关键帧时间点优先,均匀采样补足,总数 ≤ max_frames。"""
    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        duration = (container.duration or 0) / 1_000_000.0
        targets: list[float] = sorted(
            {min(max(parse_ts(k), 0.0), duration) for k in keyframes})
        fill = max_frames - len(targets)
        if fill > 0 and duration > 0:
            step = duration / (fill + 1)
            targets += [step * (i + 1) for i in range(fill)]
        deduped: list[float] = []
        for ts in sorted(targets):
            if not deduped or ts - deduped[-1] > 0.1:
                deduped.append(ts)
        frames: list[Frame] = []
        for ts in deduped[:max_frames]:
            container.seek(int(ts / stream.time_base), stream=stream)
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
                frames.append(Frame(ts=ts, jpeg=buf.getvalue()))
        return frames
