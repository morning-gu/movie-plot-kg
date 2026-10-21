import re
from dataclasses import dataclass, field
from typing import Any, Callable

ID_RE = re.compile(r"^[a-z]+(?:_[A-Za-z0-9]+)+$")


class TemplateParamError(Exception):
    pass


def _valid_id(v: str) -> str:
    if not isinstance(v, str) or not ID_RE.match(v):
        raise TemplateParamError(f"非法实体 ID: {v!r}")
    return v


def _valid_depth(v) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not (1 <= v <= 5):
        raise TemplateParamError(f"深度必须是 1-5 的整数: {v!r}")
    return v


def _valid_text(v) -> str:
    if (not isinstance(v, str) or not v or len(v) > 32
            or any(ch in v for ch in "'\"`;")):
        raise TemplateParamError(f"非法文本参数: {v!r}")
    return v


def _valid_optional_text(v):
    return None if v is None else _valid_text(v)


@dataclass
class TemplateSpec:
    template_id: str
    required: dict[str, Callable[[Any], Any]] = field(default_factory=dict)
    build: Callable[[dict], tuple[str, dict]] = None


def _relation_between_chars(p: dict) -> tuple[str, dict]:
    return ("MATCH (a:Character)-[r:RELATED_TO*1..2]-(b:Character) "
            "WHERE a.id = $a AND b.id = $b AND all(x IN r WHERE x.valid) "
            "RETURN a, r, b", {"a": p["a"], "b": p["b"]})


def _events_after(p: dict) -> tuple[str, dict]:
    return (f"MATCH (e1:Event)-[:PRECEDES*1..{p['depth']}]->(e2:Event) "
            "WHERE e1.id = $id RETURN e2 ORDER BY e2.global_order",
            {"id": p["id"]})


def _causes_of(p: dict) -> tuple[str, dict]:
    return (f"MATCH path=(c:Event)-[:CAUSES*1..{p['depth']}]->(e:Event) "
            "WHERE e.id = $id RETURN path", {"id": p["id"]})


def _events_of_char(p: dict) -> tuple[str, dict]:
    return ("MATCH (c:Character)-[r:PARTICIPATES_IN]->(e:Event) "
            "WHERE c.id = $id AND ($emotion IS NULL OR r.emotion = $emotion) "
            "RETURN e, r ORDER BY e.global_order",
            {"id": p["id"], "emotion": p.get("emotion")})


def _events_at_place(p: dict) -> tuple[str, dict]:
    return ("MATCH (e:Event)-[:LOCATED_AT]->(l:Location) WHERE l.id = $id "
            "RETURN e ORDER BY e.global_order", {"id": p["id"]})


def _events_at_time(p: dict) -> tuple[str, dict]:
    return ("MATCH (e:Event)-[:OCCURS_AT]->(vt:VirtualTime) WHERE vt.id = $id "
            "RETURN e ORDER BY e.global_order", {"id": p["id"]})


def _subevents_of(p: dict) -> tuple[str, dict]:
    return ("MATCH (s:Event)-[:SUBEVENT_OF]->(p:Event) WHERE p.id = $id "
            "RETURN s", {"id": p["id"]})


TEMPLATES: dict[str, TemplateSpec] = {
    "relation_between_chars": TemplateSpec(
        "relation_between_chars", {"a": _valid_id, "b": _valid_id},
        _relation_between_chars),
    "events_after": TemplateSpec(
        "events_after", {"id": _valid_id, "depth": _valid_depth}, _events_after),
    "causes_of": TemplateSpec(
        "causes_of", {"id": _valid_id, "depth": _valid_depth}, _causes_of),
    "events_of_char": TemplateSpec(
        "events_of_char", {"id": _valid_id, "emotion": _valid_optional_text},
        _events_of_char),
    "events_at_place": TemplateSpec(
        "events_at_place", {"id": _valid_id}, _events_at_place),
    "events_at_time": TemplateSpec(
        "events_at_time", {"id": _valid_id}, _events_at_time),
    "subevents_of": TemplateSpec(
        "subevents_of", {"id": _valid_id}, _subevents_of),
}


def validate_params(spec: TemplateSpec, params: dict) -> dict:
    missing = set(spec.required) - set(params)
    if missing:
        raise TemplateParamError(f"{spec.template_id} 缺少参数: {sorted(missing)}")
    return {k: validator(params[k]) for k, validator in spec.required.items()}


def execute_template(session, template_id: str, params: dict) -> list[dict]:
    spec = TEMPLATES.get(template_id)
    if spec is None:
        raise TemplateParamError(f"未知模板: {template_id}")
    validated = validate_params(spec, params)
    cypher, out_params = spec.build(validated)
    return session.run(cypher, **out_params)
