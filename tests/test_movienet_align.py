from movie_kg.ingestion.movienet.align import (
    ScriptLine, _norm, align_cues, parse_script, resolve_speaker)
from movie_kg.ingestion.movienet.subs import parse_srt

SRT = """1
00:00:00,500 --> 00:00:01,200
You can't handle the truth.

2
00:00:01,500 --> 00:00:01,900
I was just here to talk.

3
00:00:03,500 --> 00:00:03,900
The money is gone.
"""

SCRIPT = """EXT. OFFICE - DAY

                    MATTY
          You can't handle the truth.

                    NED
          I was just here to talk.

Later.

                    NED
          The money is gone.
"""


def test_parse_srt_reads_cues_with_ms():
    cues = parse_srt(SRT)
    assert [c.text for c in cues] == ["You can't handle the truth.",
                                      "I was just here to talk.",
                                      "The money is gone."]
    assert abs(cues[0].start - 0.5) < 1e-9 and abs(cues[0].end - 1.2) < 1e-9


def test_parse_srt_skips_blocks_without_body():
    cues = parse_srt("1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:03,000 --> 00:00:04,000\nok\n")
    assert len(cues) == 1 and cues[0].text == "ok"


def test_parse_srt_splits_on_whitespace_only_separator():
    # 分隔行含空格/制表符(真实抓取数据常见):不得吞掉下一条 cue,
    # 也不得把它的索引/时间戳污染进上一条正文
    text = ("1\n00:00:00,500 --> 00:00:01,000\nfirst line\n   \n"
            "2\n00:00:02,000 --> 00:00:03,000\nsecond line\n\t\n"
            "3\n00:00:04,000 --> 00:00:05,000\nthird line\n")
    cues = parse_srt(text)
    assert [c.text for c in cues] == ["first line", "second line", "third line"]
    assert abs(cues[1].start - 2.0) < 1e-9


def test_parse_script_extracts_speaker_blocks():
    lines = parse_script(SCRIPT)
    assert [(l.speaker, l.text) for l in lines] == [
        ("MATTY", "You can't handle the truth."),
        ("NED", "I was just here to talk."),
        ("NED", "The money is gone."),
    ]


def test_parse_script_cleans_contd_and_parenthetical():
    text = ("                    ALICE (CONT'D)\n"
            "          Hello\n          (beat)\n          there.\n")
    assert parse_script(text) == [ScriptLine(speaker="ALICE",
                                             text="Hello there.")]


def test_align_cues_matches_in_order():
    cues = parse_srt(SRT)
    out = align_cues(cues, parse_script(SCRIPT))
    assert out == ["MATTY", "NED", "NED"]


def test_align_cues_unmatched_is_none():
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\nCompletely unrelated line.\n")
    assert align_cues(cues, parse_script(SCRIPT)) == [None]


def test_align_cues_matches_split_cue_by_containment():
    # SRT 把一句剧本台词拆成两条 cue:包含关系兜底
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\nYou can't handle\n\n"
                     "2\n00:00:00,300 --> 00:00:00,400\nthe truth.\n")
    out = align_cues(cues, parse_script(SCRIPT))
    assert out[0] == "MATTY"          # 前半句包含于剧本台词
    assert out[1] in (None, "MATTY")  # 后半句允许未匹配


def test_alignment_monotonic_no_backtrack():
    # Review Focus #5:同名台词出现在剧本两处,cue 序列必须单调前进不回跳
    script = parse_script(
        "                    BOB\n          hello there\n\n"
        "                    ALICE\n          hello there\n")
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\nhello there\n\n"
                     "2\n00:00:00,300 --> 00:00:00,400\nhello there\n")
    assert align_cues(cues, script) == ["BOB", "ALICE"]


def test_resolve_speaker_two_levels():
    roster = ["Matty Walker", "Ned Racine"]
    assert resolve_speaker("MATTY", roster) == "Matty Walker"   # 名词级包含
    assert resolve_speaker("ned racine", roster) == "Ned Racine"  # 全等(大小写不敏感)
    assert resolve_speaker("GHOST", roster) is None


def test_parse_srt_splits_dash_speaker_cues():
    # SRT 双说话人惯例:同一 cue 内 "- " 行各成一条(时间戳相同)
    srt = ("1\n00:00:00,500 --> 00:00:01,200\n- Upstairs!\n- What's going on?\n\n"
           "2\n00:00:02,000 --> 00:00:02,500\nJust one line.\n")
    cues = parse_srt(srt)
    assert [c.text for c in cues] == ["Upstairs!", "What's going on?",
                                      "Just one line."]
    assert cues[0].start == cues[1].start and cues[0].end == cues[1].end


def test_parse_script_accepts_tab_indented_speakers():
    # The Rock 实测形状:说话人 6 tab,对白 2 tab,括注 3 tab
    text = ("\t\t\t\t\t\tCARLA \n"
            "\t\tEvery little dream I dream about you\n"
            "\t\t\t(New Jersey accent) \n"
            "\t\tHey there.\n")
    assert parse_script(text) == [ScriptLine(
        speaker="CARLA",
        text="Every little dream I dream about you Hey there.")]


def test_align_cues_global_fallback_resyncs():
    # 影片闪回:cue 的匹配在单调指针之前,窗口失步后全局回退命中
    script = parse_script(
        "                    AA\n          alpha\n\n"
        "                    BB\n          bravo\n\n"
        "                    CC\n          charlie\n")
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\nalpha\n\n"
                     "2\n00:00:00,300 --> 00:00:00,400\nbravo\n\n"
                     "3\n00:00:00,500 --> 00:00:00,600\nalpha\n")
    assert align_cues(cues, script, lookahead=1) == ["AA", "BB", "AA"]


def test_norm_maps_ocr_l_to_i():
    # Hackers 字幕 15.5% 含 OCR 的 "l"(应为 I)与 "lt"(应为 it);
    # token 级映射,不影响含 l 的正常词(评审 M3)
    assert _norm("l got in trouble with lt's choice") == \
        "i got in trouble with it s choice"
    assert _norm("trouble lullaby") == "trouble lullaby"


def test_norm_unchanged_for_clean_text():
    # 既有行为不变钉:_norm 被对齐全链路使用,漂移会破坏既有匹配
    assert _norm("You can't handle the truth.") == "you can t handle the truth"


def test_resolve_speaker_strips_honorific_as_retry():
    # 评审 M6:剥前缀作为精确/词级失败后的重试,不降级现有路径
    roster = ["Lauren Murphy", "Mr. White"]
    assert resolve_speaker("MRS. MURPHY", roster) == "Lauren Murphy"  # 剥后词级
    assert resolve_speaker("MR. WHITE", roster) == "Mr. White"        # 精确路径不变
    assert resolve_speaker("MRS.", roster) is None                    # 剥后为空


def test_resolve_speaker_superset_of_two_level():
    # 旧版两级行为逐例保持(超集原则)
    for label, expect in [("MATTY", "Matty Walker"),
                          ("ned racine", "Ned Racine"),
                          ("GHOST", None)]:
        assert resolve_speaker(label, ["Matty Walker", "Ned Racine"]) == expect
