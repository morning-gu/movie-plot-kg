import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ModelsConfig:
    base_url: str
    api_key: str
    vision_model: str
    text_model: str
    embedding_model: str
    embedding_dims: int


@dataclass
class Neo4jConfig:
    uri: str
    user: str
    password: str
    database: str


def _read_yaml(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_models_config(path: str | Path = "config/models.yaml") -> ModelsConfig:
    raw = _read_yaml(path)
    api_key = os.environ.get(raw["api_key_env"], "")
    if not api_key:
        raise RuntimeError(f"缺少环境变量 {raw['api_key_env']}(在 .env 中设置)")
    return ModelsConfig(
        base_url=raw["base_url"], api_key=api_key,
        vision_model=raw["vision_model"], text_model=raw["text_model"],
        embedding_model=raw["embedding_model"], embedding_dims=int(raw["embedding_dims"]),
    )


def load_neo4j_config(path: str | Path = "config/neo4j.yaml") -> Neo4jConfig:
    raw = _read_yaml(path)
    return Neo4jConfig(
        uri=raw["uri"], user=raw["user"],
        password=os.environ.get(raw["password_env"], ""),
        database=raw.get("database", "neo4j"),
    )
