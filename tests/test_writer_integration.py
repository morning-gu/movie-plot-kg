import os

import pytest

from movie_kg.extraction.global_passes import OrderedVt
from movie_kg.graph.schema import apply_schema
from movie_kg.graph.writer import GraphWriter
from tests.test_writer import EVENTS, META, _ext

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("NEO4J_URI"),
                       reason="需要 NEO4J_URI / NEO4J_PASSWORD"),
]


@pytest.fixture
def session():
    from neo4j import GraphDatabase
    cfg_uri = os.environ["NEO4J_URI"]
    driver = GraphDatabase.driver(cfg_uri, auth=(os.environ.get("NEO4J_USER", "neo4j"),
                                                 os.environ.get("NEO4J_PASSWORD", "")))
    s = driver.session()
    s.run("MATCH (n {movie_id: 'm1'}) DETACH DELETE n")
    apply_schema(s)
    yield s
    s.run("MATCH (n {movie_id: 'm1'}) DETACH DELETE n")
    s.close()
    driver.close()


def test_roundtrip_write_and_read(session):
    w = GraphWriter(session)
    w.ensure_movie("m1", "测试影片")
    w.ensure_scene(META)
    w.write_extraction(_ext(), META)
    w.apply_vt_order([OrderedVt(name="第一天傍晚", timeline_index=0, description="d")])
    rec = session.run("MATCH (e:Event {id: 'evt_m1_001_01'}) RETURN e").single()
    assert rec is not None and rec["e"]["global_order"] == 1001
    rec = session.run(
        "MATCH (c:Character)-[r:PARTICIPATES_IN]->(:Event {id: 'evt_m1_001_01'}) "
        "RETURN c.name AS n, r.emotion AS emo").single()
    assert rec["n"] == "张三" and rec["emo"] == "愤怒"
