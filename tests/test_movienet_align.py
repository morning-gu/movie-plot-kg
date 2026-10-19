import difflib
import random

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


def test_align_cues_rescues_verbatim_piece_of_long_line():
    # 独白病态(评审 B2/M4 探针实证 'I'm gonna die.'):containment 分
    # 13/67≈0.19、长度比 0.325 < 0.6 → base 与预筛都到不了;
    # 覆盖兜底(recall=1.0、连续段=全长)救回,且预筛不得门控它
    script = parse_script(
        "                    MR. ORANGE\n"
        "          All this blood is scaring the shit outta me. "
        "I'm gonna die, I know it.\n")
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\nI'm gonna die.\n")
    assert align_cues(cues, script) == ["MR. ORANGE"]


def test_coverage_rejects_scattered_tokens():
    # Elois 反例(已验剧本原文):cue 的 token 全部在场但乱序散落
    # (recall=1.0、difflib≈0.37<0.6)→ 最长连续段 18/42 < 50% → 拒绑
    # (评审 M1c/F1;fixture 必须乱序——顺序散落时 difflib 子序列比可 ≥0.6)
    line = ("So nobody knows for sure what really happened back then. "
            "One night, anyway, she meets Elois, who waits and "
            "plays it real cool.")
    script = parse_script(f"                    EDDIE\n          {line}\n")
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\n"
                     "So anyway, one night she plays it real cool.\n")
    assert align_cues(cues, script) == [None]


def test_sibling_pieces_reuse_last_matched_line():
    # 一行拆两 cue(兄弟结构,一行 2.4 cue 是常态):首片段覆盖兜底命中后,
    # 次片段经兄弟候选(j-1)复用同一行(评审 m4 根因)
    script = parse_script(
        "                    BOB\n"
        "          alpha bravo charlie delta echo foxtrot golf hotel\n\n"
        "                    ALICE\n          something entirely different here\n")
    cues = parse_srt(
        "1\n00:00:00,100 --> 00:00:00,200\nalpha bravo charlie delta\n\n"
        "2\n00:00:00,300 --> 00:00:00,400\necho foxtrot golf hotel\n")
    assert align_cues(cues, script) == ["BOB", "BOB"]


def test_last_line_candidate_loses_ties_to_window():
    # 兄弟候选平分不抢窗口行:单调语义不变
    script = parse_script(
        "                    BOB\n          hello there\n\n"
        "                    ALICE\n          hello there\n")
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\nhello there\n\n"
                     "2\n00:00:00,300 --> 00:00:00,400\nhello there\n")
    assert align_cues(cues, script) == ["BOB", "ALICE"]


def test_last_line_never_beats_higher_scoring_window_match():
    # 兄弟候选严格更优才生效:B1 翻转例的梦台词——窗口内 1.0 真身行
    # 不被低分兄弟行改绑
    script = parse_script(
        "                    PINK\n          wake up and apologize now\n\n"
        "                    MR. WHITE\n"
        "          you shoot me in a dream, you better wake up and apologize\n")
    cues = parse_srt(
        "1\n00:00:00,100 --> 00:00:00,200\nwake up and apologize now\n\n"
        "2\n00:00:00,300 --> 00:00:00,400\n"
        "You shoot me in a dream, you better wake up and apologize.\n")
    assert align_cues(cues, script) == ["PINK", "MR. WHITE"]


def test_short_cue_guard_uses_dedup_token_count():
    # 短门口径 = 去重 token:'Yak, yak, yak.' 去重后 1 词 → 门槛抬至 0.6,
    # 阈值降到 0.4 也不放行(阶段 2 的保护,先钉口径)
    script = parse_script("                    BOB\n          yak yak yak indeed ok fine\n")
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\nYak, yak, yak.\n")
    assert align_cues(cues, script, threshold=0.4) == [None]
    # 对照:阈值 0.4 下 3+ 去重 token 的 cue 正常放行(短门只护短 cue)
    cues2 = parse_srt("1\n00:00:00,100 --> 00:00:00,200\n"
                      "yak yak yak indeed and more words here\n")
    script2 = parse_script(
        "                    BOB\n          yak yak yak indeed and more words here\n")
    assert align_cues(cues2, script2, threshold=0.4) == ["BOB"]


def test_base_path_rejects_zero_token_overlap():
    # atropine 案例(终审实证):'The atropine, Isherwood.' 曾凭纯字符相似
    # (difflib ≈0.68 但 token 零交集)绑到无关行——base 路径须有 token
    # 交集下限:零交集一律拒绝,分数再高不做数(阶段 2,设计 v2 批准项)
    script = parse_script(
        "                    DR. LING\n          a tree pine shore hood\n")
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\n"
                     "The atropine, Isherwood.\n")
    assert align_cues(cues, script, threshold=0.4) == [None]
    # 对照:有 token 交集的正常对不受影响
    script2 = parse_script(
        "                    DR. LING\n          the atropine isherwood now\n")
    assert align_cues(cues, script2, threshold=0.4) == ["DR. LING"]


def test_coverage_contiguity_survives_autojunk_on_long_lines():
    # difflib autojunk(b>=200 字符时出现 >n//100 次的字符被移出 b2j)会废掉
    # find_longest_match 的种子,近逐字公共段塌缩为 1 → 覆盖兜底在独白长行上
    # 大量漏救(终审实测:三部合计 136 条 cue;66/23/30 条剧本行 >=200 字符)。
    # fixture 为真实数据 RD cue100 ↔ 剧本行 67(264 字符,autojunk 下连续段
    # 1 vs 真实 31;cue 非精确子串——"that shit's" 替换了 "it's")。
    line = ("I don't tip because society says I gotta. I tip when somebody "
            "deserves a tip. When somebody really puts forth an effort, they "
            "deserve a little something extra. But this tipping automatically, "
            "that shit's for the birds. As far as I'm concerned, they're just "
            "doin' their job.")
    script = parse_script(f"                    MR. PINK\n          {line}\n")
    cues = parse_srt("1\n00:00:00,100 --> 00:00:00,200\n"
                     "But this tipping automatically, it's for the birds.\n")
    assert align_cues(cues, script) == ["MR. PINK"]


def test_score_prefiltered_equivalence():
    # 预筛只裁必然低于 floor 的对:阈值判定与数值完全等价(300 随机组)
    from movie_kg.ingestion.movienet.align import (_score,
                                                    _score_prefiltered)
    rng = random.Random(7)
    words = ["alpha", "bravo", "charlie", "delta", "x", "y", "l", "it"]
    for _ in range(300):
        nc = " ".join(rng.choice(words) for _ in range(rng.randint(1, 8)))
        ns = " ".join(rng.choice(words) for _ in range(rng.randint(1, 12)))
        for floor in (0.4, 0.45, 0.6):
            fast = _score_prefiltered(nc, ns, floor)
            slow = _score(nc, ns)
            assert (fast >= floor) == (slow >= floor)
            if fast >= floor:
                assert abs(fast - slow) < 1e-12


def _naive_align(cues, script_lines, threshold, lookahead=60):
    """算法规格的朴素转写(测试专用):无预筛、无缓存,逐对现算 _norm/_score。"""
    import movie_kg.ingestion.movienet.align as A
    n = len(script_lines)
    speakers = [None] * len(cues)
    idxs = [None] * len(cues)
    scores = [None] * len(cues)
    j = 0
    for ci, cue in enumerate(cues):
        nc = A._norm(cue.text)
        if not nc:
            continue
        cue_toks = set(nc.split())
        eff = 0.6 if len(cue_toks) < 3 else threshold
        window = range(j, min(j + lookahead, n))

        def base_score(jj):
            ns = A._norm(script_lines[jj].text)
            if not ns or not (cue_toks & set(ns.split())):
                return 0.0      # token 零交集下限(与生产实现锁步)
            return A._score(nc, ns)

        best = None
        for jj in window:
            s = base_score(jj)
            if s >= eff and (best is None or s > best[0]):
                best = (s, jj)
        if j > 0:  # 兄弟候选 j-1:严格更优才取代窗口胜者
            s = base_score(j - 1)
            if s >= eff and (best is None or s > best[0]):
                best = (s, j - 1)
        if best is None:  # 全局回退:窗口外全剧本取最大
            for jj in range(n):
                if jj in window:
                    continue
                s = base_score(jj)
                if s >= eff and (best is None or s > best[0]):
                    best = (s, jj)
        if best is None:  # 覆盖兜底(base 全无胜者;不过预筛)
            cov = None
            for jj in range(n):
                ns = A._norm(script_lines[jj].text)
                if not ns:
                    continue
                lt = set(ns.split())
                if len(cue_toks) < 4 or len(lt) < 4:
                    continue
                recall = len(cue_toks & lt) / len(cue_toks)
                if recall < 0.85:
                    continue
                if nc in ns:
                    contig = len(nc)
                else:
                    contig = difflib.SequenceMatcher(
                        None, nc, ns, autojunk=False).find_longest_match(
                        0, len(nc), 0, len(ns)).size
                if contig < 0.5 * len(nc):
                    continue
                key = (recall, contig, -jj)
                if cov is None or key > cov:
                    cov = key
            if cov is not None:
                best = (cov[0], -cov[2])
        if best is not None:
            speakers[ci] = script_lines[best[1]].speaker
            idxs[ci] = best[1]
            scores[ci] = best[0]
            j = best[1] + 1
    return speakers, idxs, scores


def test_align_detailed_matches_naive_reference():
    # 朴素参考与生产实现产出一致(评审 M5:预筛/缓存不得改变语义)
    from movie_kg.ingestion.movienet.align import align_detailed
    rng = random.Random(11)
    speakers_pool = ["BOB", "ALICE", "CURLY"]
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot",
             "yak", "hello", "there", "now"]
    for _ in range(300):
        lines = [ScriptLine(
            speaker=rng.choice(speakers_pool),
            text=" ".join(rng.choice(words) for _ in range(rng.randint(2, 9))))
            for _ in range(rng.randint(2, 10))]
        srt = "\n\n".join(
            f"{i + 1}\n00:00:0{i % 10},{100} --> 00:00:0{i % 10},{200}\n"
            + " ".join(rng.choice(words) for _ in range(rng.randint(1, 6)))
            for i in range(rng.randint(1, 8))) + "\n"
        cues = parse_srt(srt)
        thr = rng.choice([0.4, 0.45, 0.6])
        look = rng.choice([1, 3, 60])
        assert align_detailed(cues, lines, threshold=thr, lookahead=look) \
            == _naive_align(cues, lines, thr, look)
