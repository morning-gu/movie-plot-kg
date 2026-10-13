from movie_kg.graph.schema import DDL_STATEMENTS, apply_schema
from tests.fakes import MockSession


def test_ddl_statements_complete():
    joined = "\n".join(DDL_STATEMENTS)
    for fragment in ["CREATE INDEX event_id", "CREATE INDEX scene_id",
                     "CREATE CONSTRAINT event_unique", "CREATE CONSTRAINT scene_unique",
                     "CREATE VECTOR INDEX event_vec", "CREATE VECTOR INDEX location_vec",
                     "vector.dimensions", "1024", "cosine"]:
        assert fragment in joined
    # 每类实体都有 movie_id 索引
    for label in ["Event", "Character", "VirtualTime", "Location", "Scene"]:
        assert f"FOR (n:{label}) ON (n.movie_id)" in joined


def test_apply_schema_runs_all():
    session = MockSession()
    apply_schema(session)
    assert len(session.queries) == len(DDL_STATEMENTS)
