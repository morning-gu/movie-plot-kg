import pytest

from movie_kg.graph.query_templates import (
    TEMPLATES, TemplateParamError, execute_template, validate_params)
from tests.fakes import MockSession


def test_seven_templates_registered():
    assert set(TEMPLATES) == {
        "relation_between_chars", "events_after", "causes_of", "events_of_char",
        "events_at_place", "events_at_time", "subevents_of"}


def test_build_relation_query():
    cypher, params = validate_params_and_build(
        "relation_between_chars", {"a": "char_m1_a1b2c3d4", "b": "char_m1_e5f67890"})
    assert "$a" in cypher and "RELATED_TO*1..2" in cypher and "x.valid" in cypher
    assert params["a"] == "char_m1_a1b2c3d4"


def validate_params_and_build(tid, params):
    spec = TEMPLATES[tid]
    validated = validate_params(spec, params)
    return spec.build(validated)


def test_events_after_interpolates_depth():
    cypher, _ = validate_params_and_build(
        "events_after", {"id": "evt_m1_001_01", "depth": 3})
    assert "PRECEDES*1..3" in cypher and "$id" in cypher


def test_events_after_returns_anchor_and_successors():
    # temporal 专项:回答端需要锚点事件本身的上下文,不只后继
    cypher, _ = validate_params_and_build(
        "events_after", {"id": "evt_m1_001_01", "depth": 2})
    assert "RETURN e1, e2" in cypher


@pytest.mark.parametrize("bad", [
    {"a": "Zhang'} RETURN n //", "b": "char_m1_e5f67890"},          # 注入
    {"a": "char_m1_a1b2c3d4"},                                      # 缺参数
    {"a": "nounderscore", "b": "char_m1_e5f67890"},                 # 不合 ID 正则
])
def test_bad_ids_rejected(bad):
    with pytest.raises(TemplateParamError):
        validate_params_and_build("relation_between_chars", bad)


@pytest.mark.parametrize("bad_depth", ["3 OR 1=1", 6, 0, 2.5, None])
def test_bad_depth_rejected(bad_depth):
    with pytest.raises(TemplateParamError):
        validate_params_and_build("events_after",
                                  {"id": "evt_m1_001_01", "depth": bad_depth})


def test_unknown_template_raises():
    with pytest.raises(TemplateParamError):
        execute_template(MockSession(), "drop_database", {})


def test_execute_returns_records():
    session = MockSession(results=[[{"e": {"id": "evt_m1_001_02"}}]])
    rows = execute_template(session, "events_after",
                            {"id": "evt_m1_001_01", "depth": 3})
    assert rows[0]["e"]["id"] == "evt_m1_001_02"


def test_emotion_text_validated():
    cypher, params = validate_params_and_build(
        "events_of_char", {"id": "char_m1_a1b2c3d4", "emotion": "愤怒"})
    assert "$emotion" in cypher and params["emotion"] == "愤怒"
    with pytest.raises(TemplateParamError):
        validate_params_and_build(
            "events_of_char", {"id": "char_m1_a1b2c3d4",
                               "emotion": "x' RETURN //"})
    # emotion 可为 null(不过滤)
    _, p = validate_params_and_build(
        "events_of_char", {"id": "char_m1_a1b2c3d4", "emotion": None})
    assert p["emotion"] is None
