import json

from movie_kg.ingestion.movienet.align import align_detailed, parse_script
from movie_kg.ingestion.movienet.audit import (
    align_legacy, diff_alignment, run_audit, stratified_sample)
from movie_kg.ingestion.movienet.subs import parse_srt

SRT = """1
00:00:00,500 --> 00:00:01,200
I'm gonna die.

2
00:00:02,000 --> 00:00:02,400
Totally unrelated words.
"""

SCRIPT = """                    MR. ORANGE
          All this blood is scaring the shit outta me. I'm gonna die, I know it.

                    ALICE
          different content entirely here now
"""

ROW_KEYS = {"cue_idx", "band", "cue_text", "old_speaker", "new_speaker",
            "matched_speaker", "matched_text", "dedup_tokens", "l_ocr",
            "sibling", "label"}


def test_align_legacy_has_v040_semantics():
    # legacy 无覆盖兜底:逐字包含长行的 cue(新实现能救)在 legacy 下未匹配
    cues = parse_srt(SRT)
    lines = parse_script(SCRIPT)
    speakers, idx = align_legacy(cues, lines)
    assert speakers[0] is None            # 旧语义:containment 0.19 < 0.6 → None
    new_spk, new_idx, _ = align_detailed(cues, lines)
    assert new_spk[0] == "MR. ORANGE"     # 新实现覆盖兜底救回
    assert new_spk[1] is None and speakers[1] is None


def test_diff_alignment_counts_flips_and_kept():
    cues = parse_srt(SRT)
    lines = parse_script(SCRIPT)
    legacy = align_legacy(cues, lines)
    new = align_detailed(cues, lines)
    d = diff_alignment(cues, legacy, new, ["Mr. Orange", "Alice"])
    assert d["new_only"] == 1 and d["flips"] == 0
    assert d["kept_new"] == 1 and d["kept_legacy"] == 0


def test_diff_alignment_legacy_roster_keeps_old_semantics():
    # kept_legacy 走 legacy_roster(旧视觉口径)、kept_new 走 resolve_roster
    # (Task 5 交叉核对 519/489/309 旧 convert_report 值的前提)
    srt = ("1\n00:00:00,500 --> 00:00:01,200\nI'm gonna die.\n\n"
           "2\n00:00:02,000 --> 00:00:02,400\n"
           "Different content entirely here now.\n")
    cues = parse_srt(srt)
    lines = parse_script(SCRIPT)
    legacy = align_legacy(cues, lines)
    new = align_detailed(cues, lines)
    d = diff_alignment(cues, legacy, new,
                       resolve_roster=["Alice", "Mr. Orange"],
                       legacy_roster=["Mr. Orange"])
    # cue0: legacy 未匹配、新覆盖兜底救回 → kept_new 计(Mr. Orange 在新字典)
    # cue1: 双方都匹配 ALICE → kept_legacy 不计(Alice 不在旧字典)、kept_new 计
    assert d["new_only"] == 1 and d["both_matched"] == 1
    assert d["kept_legacy"] == 0 and d["kept_new"] == 2


def test_stratified_sample_rows_and_flags():
    cues = parse_srt(SRT)
    lines = parse_script(SCRIPT)
    legacy = align_legacy(cues, lines)
    new = align_detailed(cues, lines)
    rows = stratified_sample(cues, lines, legacy, new,
                             bands=[(0.6, 1.01), (0.0, 0.6)], per_band=5)
    assert rows and all(set(r) == ROW_KEYS for r in rows)
    assert all(r["label"] == "" for r in rows)
    r0 = rows[0]
    assert r0["cue_idx"] == 0 and r0["new_speaker"] == "MR. ORANGE"
    assert r0["matched_text"].startswith("All this blood")
    assert r0["dedup_tokens"] == 4 and r0["l_ocr"] is False


def test_run_audit_writes_report_and_csv(tmp_path):
    from tests.test_movienet_convert import _make_src
    src = _make_src(tmp_path)
    out = run_audit("tt0000001", src, tmp_path / "audit",
                    thresholds=[0.6], sample_n=10)
    assert (tmp_path / "audit" / "tt0000001.md").exists()
    assert (tmp_path / "audit" / "tt0000001_samples.csv").exists()
    report = (tmp_path / "audit" / "tt0000001.md").read_text(encoding="utf-8")
    assert "legacy" in report and "flips" in report
    assert "unmatched legacy" in report       # Task 5 交叉核对所需行


def test_cli_align_audit(tmp_path):
    from typer.testing import CliRunner

    from movie_kg.cli import main
    from tests.test_movienet_convert import _make_src
    src = _make_src(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["movienet", "align-audit",
                                  "--movie", "tt0000001",
                                  "--src", str(src),
                                  "--out", str(tmp_path / "audit")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "audit" / "tt0000001.md").exists()
