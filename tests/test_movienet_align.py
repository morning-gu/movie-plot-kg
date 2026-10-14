from movie_kg.ingestion.movienet.align import (
    ScriptLine, align_cues, parse_script, resolve_speaker)
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
