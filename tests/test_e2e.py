"""端到端冒烟: build -> ask -> eval build,全程离线(ScriptedLLM + MockDriver)。"""
import shutil
from pathlib import Path

from typer.testing import CliRunner

import movie_kg.cli as cli_mod
from movie_kg.cli import main
from tests.fakes import MockDriver, MockSession, ScriptedLLM
from tests.fixtures import make_movie_dir

A1 = {"events": [{"event_type": "conflict", "topic": "对峙", "description": "张三与李四对峙",
                  "participating_characters": [
                      {"name": "张三", "emotion": "愤怒", "role_description": "发起者",
                       "items": []},
                      {"name": "李四", "emotion": "紧张", "role_description": "应对",
                       "items": []}],
                  "subevents": [
                      {"event_type": "action", "topic": "拔枪", "description": "张三拔枪",
                       "participating_characters": []}]}]}
A2 = {"character_relations": [{"char_a": "张三", "char_b": "李四",
                               "relation_type": "enemy", "is_new": True,
                               "change_description": None, "evidence_event_index": 0}]}
B1 = {"ordered": [{"name": "第一天傍晚", "description": "d1"},
                  {"name": "第二天清晨", "description": "d2"}]}
B2 = {"characters": [{"canonical": "张三", "aliases": ["老张"], "description": "码头头目"},
                     {"canonical": "李四", "aliases": [], "description": "商人"}],
      "locations": [{"canonical": "码头仓库", "aliases": [], "description": "d"},
                    {"canonical": "警局办公室", "aliases": [], "description": "d"}]}
B3 = {"causal_links": [{"cause_id": "evt_m1_001_01", "effect_id": "evt_m1_001_02",
                        "reason": "对峙升级引发拔枪"}]}
PLAN = {"intent": "causal", "rewritten_query": "导致敌对的事件链",
        "target_entities": [{"type": "Character", "name_hint": "张三"}],
        "graph_queries": [{"template_id": "causes_of",
                           "params": {"event_hint": "对峙", "depth": 3}}]}
ANSWER = {"answer": "对峙升级引发拔枪,两人转为敌对。",
          "cited_ids": ["evt_m1_001_01", "evt_m1_001_02"]}

runner = CliRunner()
REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"


def test_full_pipeline_smoke(tmp_path, monkeypatch):
    d = make_movie_dir(tmp_path)
    # 配置与提示词模板默认路径都是 cwd 相对(config.py:34,46 / prompts.py:3);
    # chdir 后需在临时目录就位整个 config/ 与所需环境变量(同 test_cli_build.py)
    shutil.copytree(REPO_CONFIG, tmp_path / "config")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw")
    monkeypatch.chdir(tmp_path)
    session = MockSession()
    llm = ScriptedLLM(A1, A2, A1, A2, B1, B2, B3)  # build 消耗 7 次
    monkeypatch.setattr(cli_mod, "_make_llm", lambda cfg: llm)
    monkeypatch.setattr(cli_mod, "_make_driver", lambda cfg: MockDriver(session))
    r = runner.invoke(main, ["build", "--movie", "m1"])
    assert r.exit_code == 0, r.output
    assert (d / "checkpoints" / "scene_002.json").exists()

    llm2 = ScriptedLLM(PLAN, ANSWER)  # ask 恰 2 次
    monkeypatch.setattr(cli_mod, "_make_llm", lambda cfg: llm2)
    r = runner.invoke(main, ["ask", "导致张三和李四敌对的事件链?"])
    assert r.exit_code == 0, r.output
    assert "拔枪" in r.output
    assert len(llm2.calls) == 2

    r = runner.invoke(main, ["eval", "build", "--movie", "m1"])
    assert r.exit_code == 0, r.output
    from pathlib import Path
    assert Path("eval/build_report.md").exists()
    report = Path("eval/build_report.md").read_text(encoding="utf-8")
    assert "F1" in report and "scene_001" in report
