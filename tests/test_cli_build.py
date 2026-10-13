import shutil
from pathlib import Path

from typer.testing import CliRunner

import movie_kg.cli as cli_mod
from movie_kg.cli import main
from tests.fakes import MockDriver, MockSession, ScriptedLLM
from tests.fixtures import make_movie_dir

A1 = {"events": [{"event_type": "action", "topic": "t", "description": "d",
                  "participating_characters": [{"name": "张三", "emotion": None,
                                                "role_description": None, "items": []}],
                  "subevents": []}]}
A2 = {"character_relations": [{"char_a": "张三", "char_b": "李四",
                               "relation_type": "enemy", "is_new": True,
                               "evidence_event_index": 0}]}
B1 = {"ordered": [{"name": "第一天傍晚", "description": "d1"},
                  {"name": "第二天清晨", "description": "d2"}]}
B2 = {"characters": [{"canonical": "张三", "aliases": ["老张"], "description": "头目"}],
      "locations": [{"canonical": "码头仓库", "aliases": [], "description": "d"},
                    {"canonical": "警局办公室", "aliases": [], "description": "d"}]}
B3 = {"causal_links": []}

runner = CliRunner()
REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"


def test_build_end_to_end(tmp_path, monkeypatch):
    d = make_movie_dir(tmp_path)
    # 配置与提示词模板默认路径都是 cwd 相对(config.py:34,46 / prompts.py:3);
    # chdir 后需在临时目录就位整个 config/ 与所需环境变量
    shutil.copytree(REPO_CONFIG, tmp_path / "config")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw-test")
    monkeypatch.chdir(tmp_path)
    session = MockSession()
    llm = ScriptedLLM(A1, A2, A1, A2, B1, B2, B3)  # A×2场景×2 + B1/B2/B3
    monkeypatch.setattr(cli_mod, "_make_llm", lambda cfg: llm)
    monkeypatch.setattr(cli_mod, "_make_driver", lambda cfg: MockDriver(session))
    result = runner.invoke(main, ["build", "--movie", "m1"])
    assert result.exit_code == 0, result.output
    # checkpoint 已落盘
    assert (d / "checkpoints" / "scene_001.json").exists()
    # 图写入发生了
    all_queries = "\n".join(q for q, _ in session.queries)
    for frag in ["MERGE (e:Event", "PARTICIPATES_IN", "PRECEDES", "RELATED_TO",
                 "timeline_index", "BELONGS_TO"]:
        assert frag in all_queries
