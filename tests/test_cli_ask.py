import shutil
from pathlib import Path

from typer.testing import CliRunner

import movie_kg.cli as cli_mod
from movie_kg.cli import main
from tests.fakes import MockDriver, MockSession, ScriptedLLM

PLAN = {"intent": "factual", "rewritten_query": "张三发怒",
        "target_entities": [{"type": "Character", "name_hint": "张三"}],
        "graph_queries": []}
ANSWER = {"answer": "张三在码头仓库对峙时发怒。",
          "cited_ids": ["evt_m1_001_01"]}

runner = CliRunner()
REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"


def test_ask_two_llm_calls(tmp_path, monkeypatch):
    # 配置与提示词模板默认路径都是 cwd 相对(config.py:34,46 / prompts.py:3);
    # chdir 后需在临时目录就位整个 config/ 与所需环境变量(同 test_cli_build.py)
    shutil.copytree(REPO_CONFIG, tmp_path / "config")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw-test")
    monkeypatch.chdir(tmp_path)
    session = MockSession(results=[[], [], [], []])  # 向量召回 4 索引 + 绑定查询
    llm = ScriptedLLM(PLAN, ANSWER)
    monkeypatch.setattr(cli_mod, "_make_llm", lambda cfg: llm)
    monkeypatch.setattr(cli_mod, "_make_driver", lambda cfg: MockDriver(session))
    result = runner.invoke(main, ["ask", "张三在哪里发怒了?"])
    assert result.exit_code == 0, result.output
    assert "码头仓库" in result.output
    assert len(llm.calls) == 2, "每问题必须恰 2 次 LLM 调用"
