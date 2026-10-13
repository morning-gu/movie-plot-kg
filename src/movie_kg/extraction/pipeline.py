from dataclasses import dataclass, field

from movie_kg.extraction.checkpoints import CheckpointStore
from movie_kg.extraction.event_extractor import extract_scene_events
from movie_kg.extraction.relation_extractor import extract_scene_relations
from movie_kg.ingestion.frame_sampler import sample_frames
from movie_kg.llm import LLMError
from movie_kg.models.extraction import SceneExtraction
from movie_kg.models.scene import MovieData


@dataclass
class PhaseAResult:
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: int = 0
    usage: dict = field(default_factory=lambda: {"prompt_tokens": 0,
                                                 "completion_tokens": 0})


def _prior_relations(store: CheckpointStore) -> list[dict]:
    """从已完成 checkpoint 汇总每对角色最新有效关系。"""
    latest: dict[tuple[str, str], dict] = {}
    for sid in store.all_done():
        ext = store.load(sid)
        if ext is None:
            continue
        for r in ext.relations:
            latest[(r.char_a, r.char_b)] = {"char_a": r.char_a, "char_b": r.char_b,
                                            "relation_type": r.relation_type}
    return list(latest.values())


def run_phase_a(llm, movie: MovieData, store: CheckpointStore,
                scene_range: tuple[int, int] | None = None,
                from_scratch: bool = False, max_frames: int = 16) -> PhaseAResult:
    result = PhaseAResult()
    for scene in movie.scenes:
        seq = int(scene.meta.scene_id.rsplit("_", 1)[1])
        if scene_range and not (scene_range[0] <= seq <= scene_range[1]):
            continue
        if from_scratch:
            store.path(scene.meta.scene_id).unlink(missing_ok=True)
        else:
            ckpt = store.load(scene.meta.scene_id)
            if ckpt is not None and ckpt.status == "ok":
                result.skipped += 1
                continue
        try:
            frames = sample_frames(scene.video_path, scene.meta.keyframes,
                                   max_frames=max_frames)
            events = extract_scene_events(llm, frames, scene)
            relations = extract_scene_relations(
                llm, events, scene.meta.characters, _prior_relations(store))
            store.save(SceneExtraction(scene_id=scene.meta.scene_id,
                                       movie_id=movie.movie_id, status="ok",
                                       events=events, relations=relations))
            result.completed.append(scene.meta.scene_id)
        except (LLMError, ValueError):
            # ValueError: LLM 输出结构合法但语义越界(如 evidence_event_index 越界,
            # parse_a2 抛出);同 LLMError 一样标记场景失败,不阻塞后续场景(§5.2)
            store.save(SceneExtraction(scene_id=scene.meta.scene_id,
                                       movie_id=movie.movie_id, status="failed"))
            result.failed.append(scene.meta.scene_id)
    result.usage = dict(getattr(llm, "usage", result.usage))
    return result
