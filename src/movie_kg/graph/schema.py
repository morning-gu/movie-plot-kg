DDL_STATEMENTS = [
    # 注:各实体的 id 唯一约束自带后备范围索引,不另建普通 id 索引——
    # 同 schema 的普通索引与唯一约束互斥(Neo4j 5 实测 IndexAlreadyExists)
    "CREATE INDEX character_name IF NOT EXISTS FOR (c:Character) ON (c.name)",
    "CREATE INDEX event_scene_id IF NOT EXISTS FOR (e:Event) ON (e.scene_id)",
    "CREATE INDEX event_movie_id IF NOT EXISTS FOR (n:Event) ON (n.movie_id)",
    "CREATE INDEX character_movie_id IF NOT EXISTS FOR (n:Character) ON (n.movie_id)",
    "CREATE INDEX virtualtime_movie_id IF NOT EXISTS FOR (n:VirtualTime) ON (n.movie_id)",
    "CREATE INDEX location_movie_id IF NOT EXISTS FOR (n:Location) ON (n.movie_id)",
    "CREATE INDEX scene_movie_id IF NOT EXISTS FOR (n:Scene) ON (n.movie_id)",
    "CREATE CONSTRAINT event_unique IF NOT EXISTS FOR (e:Event) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT char_unique IF NOT EXISTS FOR (c:Character) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT loc_unique IF NOT EXISTS FOR (l:Location) REQUIRE l.id IS UNIQUE",
    "CREATE CONSTRAINT vt_unique IF NOT EXISTS FOR (vt:VirtualTime) REQUIRE vt.id IS UNIQUE",
    "CREATE CONSTRAINT scene_unique IF NOT EXISTS FOR (s:Scene) REQUIRE s.id IS UNIQUE",
    "CREATE VECTOR INDEX event_vec IF NOT EXISTS FOR (e:Event) ON (e.embedding) "
    "OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}}",
    "CREATE VECTOR INDEX character_vec IF NOT EXISTS FOR (c:Character) ON (c.embedding) "
    "OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}}",
    "CREATE VECTOR INDEX virtualtime_vec IF NOT EXISTS FOR (vt:VirtualTime) ON (vt.embedding) "
    "OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}}",
    "CREATE VECTOR INDEX location_vec IF NOT EXISTS FOR (l:Location) ON (l.embedding) "
    "OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}}",
]


def apply_schema(session) -> None:
    for stmt in DDL_STATEMENTS:
        session.run(stmt)
