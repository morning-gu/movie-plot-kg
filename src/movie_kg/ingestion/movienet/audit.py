"""台词对齐新旧对照审计(阶段 1 验收与阶段 2 阈值决策的数据来源)。

legacy = v0.4.0 语义基线(无覆盖兜底/兄弟候选/短门/l→i/荣誉剥前缀),
保留旧归一化;统计口径对齐 convert_report(场景内 cue,场景外先丢弃)。
"""
import csv
import json
import random
import re
from pathlib import Path

from movie_kg.ingestion.movienet.align import (
    _norm, _score_prefiltered, align_detailed, parse_script, resolve_speaker)
from movie_kg.ingestion.movienet.convert import (
    calibrate_fps, scene_characters, scene_windows)
from movie_kg.ingestion.movienet.subs import parse_srt

DEFAULT_BANDS = [(0.0, 0.45), (0.45, 0.5), (0.5, 0.55), (0.55, 0.6),
                 (0.6, 1.01)]


def _norm_v040(s: str) -> str:
    """v0.4.0 归一化(无 l→i):legacy 基线必须用旧语义,审计才有效。"""
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())


def _resolve_speaker_v040(speaker: str, roster: list[str]) -> str | None:
    """v0.4.0 两级解析(无荣誉剥前缀):legacy 统计的解析侧同样必须是旧语义
    (试点实证:新四级解析会把 MRS. MURPHY 解进旧 roster,kept_legacy 虚高 23)。"""
    s = speaker.strip().lower()
    for c in roster:
        if c.lower() == s:
            return c
    for c in roster:
        if s and s in [w for w in re.split(r"[ .]", c.lower()) if w]:
            return c
    return None


def align_legacy(cues, script_lines, threshold: float = 0.6,
                 lookahead: int = 60) -> tuple[list, list]:
    """v0.4.0 对齐语义:单调窗口 + 全局回退,无兄弟候选/短门/覆盖兜底。
    带预归一化与长度预筛(与旧 _score 逐对等价,探针以三部影片
    convert_report 真实产出校验 3/3 PASS)。返回 (speakers, matched_idx)。"""
    norm_lines = [_norm_v040(l.text) for l in script_lines]
    speakers: list = [None] * len(cues)
    idxs: list = [None] * len(cues)
    j = 0
    for ci, cue in enumerate(cues):
        nc = _norm_v040(cue.text)
        if not nc:
            continue
        w_hi = min(j + lookahead, len(script_lines))
        best: tuple[float, int] | None = None
        for jj in range(j, w_hi):
            ns = norm_lines[jj]
            if not ns:
                continue
            s = _score_prefiltered(nc, ns, threshold)
            if s >= threshold and (best is None or s > best[0]):
                best = (s, jj)
        if best is None:                       # 全局回退:失步后全剧本找最优
            for jj in range(len(script_lines)):
                if j <= jj < w_hi:
                    continue
                ns = norm_lines[jj]
                if not ns:
                    continue
                s = _score_prefiltered(nc, ns, threshold)
                if s >= threshold and (best is None or s > best[0]):
                    best = (s, jj)
        if best is not None:
            speakers[ci] = script_lines[best[1]].speaker
            idxs[ci] = best[1]
            j = best[1] + 1
    return speakers, idxs


def diff_alignment(cues, legacy, new, resolve_roster, legacy_roster=None,
                   indices=None) -> dict:
    """legacy=(speakers, idxs) vs new=(speakers, idxs, scores) 逐 cue 对照。

    kept_new 用 resolve_roster(新口径:视觉 roster ∪ meta.cast);
    kept_legacy 用 legacy_roster(默认同 resolve_roster;run_audit 传旧视觉
    roster,使 legacy 统计与 v0.4.0 convert_report 完全可比)。
    indices 限定统计的 cue 下标(None=全部);场景口径由调用方传入。"""
    legacy_spk, _ = legacy
    new_spk, _, _ = new
    old_roster = legacy_roster if legacy_roster is not None else resolve_roster
    idx_range = indices if indices is not None else range(len(cues))
    d = {"both_matched": 0, "flips": 0, "new_only": 0, "legacy_only": 0,
         "kept_legacy": 0, "kept_new": 0, "flips_old_resolvable": 0}
    for i in idx_range:
        old, cur = legacy_spk[i], new_spk[i]
        if old is not None and cur is not None:
            d["both_matched"] += 1
            if old != cur:
                d["flips"] += 1
                if _resolve_speaker_v040(old, old_roster) is not None:
                    d["flips_old_resolvable"] += 1
        elif cur is not None:
            d["new_only"] += 1
        elif old is not None:
            d["legacy_only"] += 1
        if old is not None and _resolve_speaker_v040(old, old_roster) is not None:
            d["kept_legacy"] += 1
        if cur is not None and resolve_speaker(cur, resolve_roster) is not None:
            d["kept_new"] += 1
    return d


def stratified_sample(cues, script_lines, legacy, new, bands, per_band,
                      seed: int = 42) -> list[dict]:
    """按命中分带分层抽样,产出人工标注行(label 留空)。"""
    legacy_spk, _ = legacy
    new_spk, new_idx, new_score = new
    rng = random.Random(seed)
    by_band: dict[tuple, list[int]] = {b: [] for b in bands}
    for i, (idx, sc) in enumerate(zip(new_idx, new_score)):
        if idx is None:
            continue
        for b in bands:
            if b[0] <= sc < b[1]:
                by_band[b].append(i)
                break
    rows: list[dict] = []
    for b, cand in by_band.items():
        for i in rng.sample(cand, min(per_band, len(cand))):
            prev = new_idx[i - 1] if i > 0 else None
            rows.append({
                "cue_idx": i,
                "band": f"{b[0]}-{b[1]}",
                "cue_text": cues[i].text,
                "old_speaker": legacy_spk[i] or "",
                "new_speaker": new_spk[i] or "",
                "matched_speaker": script_lines[new_idx[i]].speaker,
                "matched_text": script_lines[new_idx[i]].text,
                "dedup_tokens": len(set(_norm(cues[i].text).split())),
                "l_ocr": any(t in ("l", "lt") for t in
                             re.findall(r"[a-z0-9]+", cues[i].text.lower())),
                "sibling": prev is not None and prev == new_idx[i],
                "label": "",
            })
    rows.sort(key=lambda r: r["cue_idx"])
    return rows


def run_audit(movie: str, src: Path, out_dir: Path,
              thresholds: list[float] | None = None,
              sample_n: int = 100) -> Path:
    """legacy@0.6 vs 各阈值新对齐:汇总报告 + 分层抽样 CSV。返回报告路径。

    统计口径对齐 convert_report:场景内 cue;legacy 用旧视觉 roster,
    新对齐用 视觉 roster ∪ meta.cast(与 convert v0.4.1 一致)。"""
    thresholds = thresholds or [0.6]
    ann = json.loads((src / "annotation" / f"{movie}.json")
                     .read_text(encoding="utf-8"))
    meta = json.loads((src / "meta" / f"{movie}.json")
                      .read_text(encoding="utf-8"))
    srt_text = (src / "subtitle" / f"{movie}.srt") \
        .read_text(encoding="utf-8", errors="replace")
    script_text = (src / "script" / f"{movie}.script") \
        .read_text(encoding="utf-8", errors="replace")
    cues = parse_srt(srt_text)
    lines = parse_script(script_text)
    fps = calibrate_fps(ann.get("story") or [])
    scenes, _ = scene_windows(ann, fps)
    bounds = [(s["start"], s["end"]) for s in scenes]
    inside = [i for i, c in enumerate(cues)
              if any(s <= c.start < e for s, e in bounds)]
    rosters = scene_characters(ann, meta, scenes)
    movie_roster = sorted({n for r in rosters for n in r})
    meta_roster = sorted({c["character"].strip() for c in meta.get("cast", [])
                          if c.get("character", "").strip()})
    resolve_roster = sorted(set(movie_roster) | set(meta_roster))

    legacy = align_legacy(cues, lines)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    sections = []
    n_in = len(inside)
    for t in thresholds:
        new = align_detailed(cues, lines, threshold=t)
        d = diff_alignment(cues, legacy, new, resolve_roster,
                           legacy_roster=movie_roster, indices=inside)
        sections.append((t, d))
        per_band = max(1, sample_n // len(DEFAULT_BANDS))
        for r in stratified_sample(cues, lines, legacy, new,
                                   DEFAULT_BANDS, per_band):
            r["threshold"] = t
            rows.append(r)

    md = [f"# 台词对齐审计 — {movie}", "",
          f"- cues 总数 {len(cues)},场景内 {n_in},剧本行 {len(lines)}",
          "- legacy = v0.4.0 语义 @0.6(无覆盖兜底/兄弟候选/短门/l→i/"
          "荣誉剥前缀);kept_legacy 用旧视觉 roster,kept_new 用 "
          "roster ∪ meta.cast", ""]
    for t, d in sections:
        unmatched_legacy = n_in - d["both_matched"] - d["legacy_only"]
        md += [f"## threshold = {t}", "",
               "| 指标 | 值 |", "|---|---|",
               f"| matched(新) | {d['both_matched'] + d['new_only']} |",
               f"| unmatched legacy(场景内) | {unmatched_legacy} |",
               f"| kept legacy(旧口径) | {d['kept_legacy']} "
               f"({d['kept_legacy'] / n_in:.0%}) |" if n_in else
               "| kept legacy(旧口径) | 0 |",
               f"| kept new(新口径) | {d['kept_new']} "
               f"({d['kept_new'] / n_in:.0%}) |" if n_in else
               "| kept new(新口径) | 0 |",
               f"| both_matched | {d['both_matched']} |",
               f"| flips(说话人改变) | {d['flips']} |",
               f"| flips 中旧可解析 | {d['flips_old_resolvable']} |",
               f"| new_only | {d['new_only']} |",
               f"| legacy_only | {d['legacy_only']} |", ""]
    report = out_dir / f"{movie}.md"
    report.write_text("\n".join(md), encoding="utf-8")
    fields = ["threshold", "cue_idx", "band", "cue_text", "old_speaker",
              "new_speaker", "matched_speaker", "matched_text",
              "dedup_tokens", "l_ocr", "sibling", "label"]
    with open(out_dir / f"{movie}_samples.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return report
