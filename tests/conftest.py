import pytest
import sys
sys.path.insert(0, "tests")


@pytest.fixture
def repo_config_paths(tmp_path, monkeypatch):
    """复制仓库 config/*.yaml 到临时目录并注入测试环境变量。"""
    import shutil
    from pathlib import Path
    shutil.copy("config/models.yaml", tmp_path / "models.yaml")
    shutil.copy("config/neo4j.yaml", tmp_path / "neo4j.yaml")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw-test")
    return tmp_path / "models.yaml", tmp_path / "neo4j.yaml"
