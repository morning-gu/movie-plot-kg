from movie_kg.graph.schema import DDL_STATEMENTS, apply_schema
from tests.fakes import MockSession


def test_ddl_statements_complete():
    joined = "\n".join(DDL_STATEMENTS)
    # 唯一约束自带后备索引:普通 id 索引与约束同 schema 互斥,不得出现
    for redundant in ["CREATE INDEX event_id", "CREATE INDEX character_id",
                      "CREATE INDEX location_id", "CREATE INDEX virtualtime_id",
                      "CREATE INDEX scene_id"]:
        assert redundant not in joined
    for fragment in ["CREATE INDEX character_name",
                     "CREATE CONSTRAINT event_unique", "CREATE CONSTRAINT scene_unique",
                     "CREATE VECTOR INDEX event_vec", "CREATE VECTOR INDEX location_vec",
                     "vector.dimensions", "vector.similarity_function",
                     "1024", "cosine"]:
        assert fragment in joined
    # 每类实体都有 movie_id 索引
    for label in ["Event", "Character", "VirtualTime", "Location", "Scene"]:
        assert f"FOR (n:{label}) ON (n.movie_id)" in joined


def test_apply_schema_runs_all():
    session = MockSession()
    apply_schema(session)
    assert len(session.queries) == len(DDL_STATEMENTS)
