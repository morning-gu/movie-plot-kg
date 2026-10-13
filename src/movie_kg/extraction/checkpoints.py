import json
import os
from pathlib import Path

from movie_kg.models.extraction import SceneExtraction


class CheckpointStore:
    def __init__(self, movie_dir: str | Path):
        self.dir = Path(movie_dir) / "checkpoints"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, scene_id: str) -> Path:
        return self.dir / f"{scene_id}.json"

    def save(self, ext: SceneExtraction) -> Path:
        p = self.path(ext.scene_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(ext.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, p)
        return p

    def load(self, scene_id: str) -> SceneExtraction | None:
        p = self.path(scene_id)
        if not p.exists():
            return None
        return SceneExtraction.model_validate_json(p.read_text(encoding="utf-8"))

    def _with_status(self, status: str) -> list[str]:
        out = []
        for p in sorted(self.dir.glob("scene_*.json")):
            if self.load(p.stem) is not None and self.load(p.stem).status == status:
                out.append(p.stem)
        return out

    def all_done(self) -> list[str]:
        return self._with_status("ok")

    def all_failed(self) -> list[str]:
        return self._with_status("failed")

    def has(self, scene_id: str) -> bool:
        return self.path(scene_id).exists()
