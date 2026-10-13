from movie_kg.extraction.checkpoints import CheckpointStore
from movie_kg.models.extraction import SceneExtraction


def _ext(scene_id="scene_001", status="ok"):
    return SceneExtraction(scene_id=scene_id, movie_id="m1", status=status)


def test_save_load_roundtrip(tmp_path):
    store = CheckpointStore(tmp_path / "m1")
    ext = _ext()
    path = store.save(ext)
    assert path.exists() and path.name == "scene_001.json"
    assert store.load("scene_001") == ext


def test_load_missing_returns_none(tmp_path):
    store = CheckpointStore(tmp_path / "m1")
    assert store.load("scene_009") is None


def test_all_done_filters_failed(tmp_path):
    store = CheckpointStore(tmp_path / "m1")
    store.save(_ext("scene_001", "ok"))
    store.save(_ext("scene_002", "failed"))
    store.save(_ext("scene_003", "ok"))
    assert store.all_done() == ["scene_001", "scene_003"]
    assert store.all_failed() == ["scene_002"]
    assert store.has("scene_002")


def test_load_old_checkpoint_without_evidence(tmp_path):
    import json
    from movie_kg.extraction.checkpoints import CheckpointStore
    store = CheckpointStore(tmp_path)
    old = {"scene_id": "scene_001", "movie_id": "m1", "status": "ok",
           "events": [{"id": "evt_m1_001_01", "event_type": "action", "topic": "t",
                       "description": "d", "scene_id": "scene_001", "movie_id": "m1",
                       "order": 1}], "relations": []}
    store.path("scene_001").write_text(json.dumps(old), encoding="utf-8")
    ext = store.load("scene_001")
    assert ext is not None and ext.status == "ok"
    assert ext.events[0].evidence_frame_ids == []
