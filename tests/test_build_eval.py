from movie_kg.eval.build_eval import (
    aggregate_prf, character_prf, merge_labels, sample_relations,
    score_relation_samples)
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
                     scenes=[SceneData(meta=META, video_path="v.mp4")])


def test_aggregate_prf():
    rows = character_prf(_movie(), [_ext([Participant(name="张三")])])
    agg = aggregate_prf(rows)
    assert agg["scenes"] == 1 and "f1" in agg


def test_sample_relations_queries_each_type():
    session = MockSession(results=[[{"start": "a", "end": "b", "props": {}}] * 5] * 5)
    rows = sample_relations(session, "m1", n_per_type=5)
    assert len(rows) == 25
    assert {r["type"] for r in rows} == {
        "PRECEDES", "CAUSES", "SUBEVENT_OF", "PARTICIPATES_IN", "RELATED_TO"}
    # 抽样必须按 movie_id 圈定范围,不跨影片
    assert all(p["movie"] == "m1" for _, p in session.queries)


def test_score_relation_samples():
    rows = [{"type": "CAUSES", "label": "correct"},
            {"type": "CAUSES", "label": "incorrect"},
            {"type": "PRECEDES", "label": "correct"}]
    scores = score_relation_samples(rows)
    assert scores["CAUSES"] == 0.5 and scores["PRECEDES"] == 1.0


def test_merge_labels_carries_over_by_key():
    existing = [{"type": "CAUSES", "start": "a", "end": "b", "label": "correct"},
                {"type": "CAUSES", "start": "x", "end": "y", "label": "incorrect"}]
    sampled = [{"type": "CAUSES", "start": "a", "end": "b", "props": {}, "label": ""},
               {"type": "CAUSES", "start": "c", "end": "d", "props": {}, "label": ""}]
    merged = merge_labels(existing, sampled)
    assert len(merged) == 2                     # 未再抽样的旧行不进入结果
    assert merged[0]["label"] == "correct"      # 匹配 (type,start,end): label 带入
    assert merged[1]["label"] == ""             # 无匹配: 保持空
    assert all(m["label"] != "incorrect" for m in merged)  # 旧行 label 被忽略
