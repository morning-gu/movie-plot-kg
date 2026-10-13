from movie_kg.config import load_models_config, load_neo4j_config


def test_load_models_config(repo_config_paths):
    models_path, _ = repo_config_paths
    cfg = load_models_config(models_path)
    assert cfg.base_url.startswith("https://")
    assert cfg.api_key == "sk-test"
    assert cfg.vision_model and cfg.text_model and cfg.embedding_model
    assert cfg.embedding_dims == 1024


def test_load_neo4j_config(repo_config_paths):
    _, neo4j_path = repo_config_paths
    cfg = load_neo4j_config(neo4j_path)
    assert cfg.uri.startswith("bolt://")
    assert cfg.password == "pw-test"
    assert cfg.database == "neo4j"


def test_missing_api_key_raises(repo_config_paths, monkeypatch):
    models_path, _ = repo_config_paths
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    import pytest
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        load_models_config(models_path)
