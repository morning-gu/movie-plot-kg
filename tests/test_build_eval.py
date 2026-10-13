from movie_kg.eval.build_eval import (
    aggregate_prf, character_prf, sample_relations, score_relation_samples)
from movie_kg.models.extraction import Participant, SceneEvent, SceneExtraction
from movie_kg.models.scene import CharacterMeta, SceneMeta
from tests.fakes import MockSession

META = SceneMeta(scene_id="scene_001", movie_id="m1", start_ts="00:00:00",
                 end_ts="00:02:00",
                 characters=[CharacterMeta(name="张三", aliases=[]),
                             CharacterMeta(name="李四", aliases=[])],
                 dialogues=[], location="x", virtual_time="y")


def _ext(participants):
    return SceneExtraction(
        scene_id="scene_001", movie_id="m1", status="ok",
        events=[SceneEvent(id="evt_m1_001_01", event_type="action", topic="t",
                           description="d", scene_id="scene_001", movie_id="m1",
                           order=1, participants=participants)])


def test_character_prf_excludes_unaligned():
    ext = _ext([Participant(name="张三"), Participant(name="未识别:王五")])
    rows = character_prf(_movie(), [ext])
    row = rows[0]
    assert set(row.gold) == {"张三", "李四"}
    assert set(row.pred) == {"张三"}   # 未识别不进 pred
    assert row.precision == 1.0 and row.recall == 0.5


def _movie():
    from movie_kg.models.scene import MovieData, SceneData
    return MovieData(movie_id="m1", title="t",
                     scenes=[SceneData(meta=META, srt_text="", video_path="v.mp4")])


def test_aggregate_prf():
    rows = character_prf(_movie(), [_ext([Participant(name="张三")])])
    agg = aggregate_prf(rows)
    assert agg["scenes"] == 1 and "f1" in agg


def test_sample_relations_queries_each_type():
    session = MockSession(results=[[{"start": "a", "end": "b", "props": {}}] * 5] * 5)
    rows = sample_relations(session, n_per_type=5)
    assert len(rows) == 25
    assert {r["type"] for r in rows} == {
        "PRECEDES", "CAUSES", "SUBEVENT_OF", "PARTICIPATES_IN", "RELATED_TO"}


def test_score_relation_samples():
    rows = [{"type": "CAUSES", "label": "correct"},
            {"type": "CAUSES", "label": "incorrect"},
            {"type": "PRECEDES", "label": "correct"}]
    scores = score_relation_samples(rows)
    assert scores["CAUSES"] == 0.5 and scores["PRECEDES"] == 1.0
