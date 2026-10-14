"""MovieNet → v0.4.0 输入转换(DESIGN §3.4,九个 stage)。"""
import json
import re
import statistics
import tarfile
from pathlib import Path

from movie_kg.ingestion.data_loader import InputValidationError, load_movie
from movie_kg.ingestion.movienet.align import align_cues, parse_script, resolve_speaker
from movie_kg.ingestion.movienet.subs import parse_srt
from movie_kg.ingestion.timefmt import fmt_ts
from movie_kg.models.scene import (CharacterMeta, Dialogue, KeyframeMeta,
                                   SceneMeta)

FPS_TOL = 0.005          # fps 离散度门槛 ±0.5%(DESIGN §3.4 stage 1)
MATCH_THRESHOLD = 0.6    # 剧本对齐阈值(试点校准)


class ConvertError(Exception):
    pass


def calibrate_fps(story: list[dict]) -> float:
    """stage 1: story 条目 Δframe/Δduration 取中位;离散度超限报错。"""
    ests = [(st["frame"][1] - st["frame"][0]) / (st["duration"][1] - st["duration"][0])
            for st in story
            if st["duration"][1] > st["duration"][0] and st["frame"][1] > st["frame"][0]]
    if not ests:
        raise ConvertError("无 story 条目可用于 fps 标定")
    med = statistics.median(ests)
    if any(abs(e - med) / med > FPS_TOL for e in ests):
        raise ConvertError(f"fps 离散度超限: {[round(e, 4) for e in ests]}")
    return med


def scene_windows(ann: dict, fps: float) -> tuple[list[dict], int]:
    """stage 2: 场景窗口(秒)+ shot 区间,按帧序;返回 (窗口列表, 合并数)。

    亚秒场景(整秒截断后起止同秒,fmt_ts 会产出 start==end 被 loader 拒绝)
    并入相邻场景:有前一输出场景则向后并入(延展其终点与 shot 上界),
    场景列表开头的亚秒场景挂起,待并入其后首个正常场景(起点与 shot 下界
    取挂起者);连续亚秒逐个累积;全片亚秒(病态)时安全网放宽终点 1s。
    """
    scenes = sorted(ann.get("scene") or [], key=lambda s: s["frame"][0])
    if not scenes:
        raise ConvertError("无场景标注")
    out: list[dict] = []
    pending: dict | None = None
    merged = 0
    for s in scenes:
        sc = {"start": s["frame"][0] / fps,
              "end": s["frame"][1] / fps,
              "shot_span": (s["shot"][0], s["shot"][1]),
              "place_tag": s.get("place_tag")}
        if int(sc["start"]) == int(sc["end"]):
            merged += 1
            target = out[-1] if out else pending
            if target is None:                  # 首场景即亚秒:挂起
                pending = sc
            else:                               # 向后并入(last 或挂起者)
                target["end"] = max(target["end"], sc["end"])
                a, b = target["shot_span"]
                target["shot_span"] = (a, max(b, sc["shot_span"][1]))
                target["place_tag"] = target["place_tag"] or sc["place_tag"]
        elif pending is not None:               # 挂起场景向前并入本场景
            sc["start"] = pending["start"]
            sc["shot_span"] = (pending["shot_span"][0], sc["shot_span"][1])
            sc["place_tag"] = sc["place_tag"] or pending["place_tag"]
            pending = None
            out.append(sc)
        else:
            out.append(sc)
    if pending is not None:                     # 安全网:全片亚秒(无后继)
        pending["end"] = int(pending["start"]) + 1
        out.append(pending)
    for i, sc in enumerate(out):
        sc["seq"] = i + 1
    return out, merged


def scene_characters(ann: dict, meta: dict, scenes: list[dict]) -> list[list[str]]:
    """stage 3: 每场景在册角色(cast pid → meta 角色名,按 shot 区间聚合)。"""
    nm_to_char = {c["id"]: c.get("character", "").strip()
                  for c in meta.get("cast", []) if c.get("character")}
    named: dict[str, str] = {}
    for inst in ann.get("cast") or []:
        pid = inst.get("pid")
        if pid and pid != "others" and pid in nm_to_char:
            named[pid] = nm_to_char[pid]
    by_shot: dict[int, set[str]] = {}
    for inst in ann.get("cast") or []:
        name = named.get(inst.get("pid"))
        if name is not None:
            by_shot.setdefault(inst.get("shot_idx", -1), set()).add(name)
    out = []
    for sc in scenes:
        a, b = sc["shot_span"]
        names: set[str] = set()
        for k in range(a, b + 1):
            names |= by_shot.get(k, set())
        out.append(sorted(names))
    return out


def scene_dialogues(srt_text: str, script_text: str, scenes: list[dict],
                    roster: list[str]) -> tuple[list[list[Dialogue]], dict]:
    """stage 4: SRT 按场景窗口切片 + 剧本对齐 + 说话人归 roster。
    cue 归起点所在场景;两级未匹配分别计数后丢弃。"""
    cues = parse_srt(srt_text)
    speakers = (align_cues(cues, parse_script(script_text),
                           threshold=MATCH_THRESHOLD)
                if script_text.strip() else [None] * len(cues))
    bounds = [(sc["start"], sc["end"]) for sc in scenes]
    per_scene: list[list[Dialogue]] = [[] for _ in scenes]
    stats = {"total": len(cues), "no_script_match": 0,
             "speaker_not_in_roster": 0, "outside_scenes": 0}
    for cue, spk in zip(cues, speakers):
        si = next((i for i, (s, e) in enumerate(bounds) if s <= cue.start < e),
                  None)
        if si is None:                       # 片头/片尾等场景窗口之外
            stats["outside_scenes"] += 1
            continue
        if spk is None:
            stats["no_script_match"] += 1
            continue
        char = resolve_speaker(spk, roster)
        if char is None:
            stats["speaker_not_in_roster"] += 1
            continue
        per_scene[si].append(Dialogue(char=char, text=cue.text,
                                      ts=fmt_ts(cue.start)))
    return per_scene, stats


def scene_place(sc: dict) -> str:
    """stage 5: place_tag 多值 join;未标注 → 空。"""
    return " / ".join(sc.get("place_tag") or [])


def scene_descriptions(ann: dict, scenes: list[dict]) -> list[str]:
    """stage 6: story 段落赋给 shot 区间重叠最大的场景(粗粒度先验)。"""
    story = ann.get("story") or []
    out = []
    for sc in scenes:
        a, b = sc["shot_span"]
        best, best_ov = "", 0
        for st in story:
            sa, sb = st["shot"]
            ov = min(b, sb) - max(a, sa) + 1
            if ov > best_ov and st.get("description"):
                best, best_ov = st["description"].strip(), ov
        out.append(best)
    return out


def keyframe_entries(ann: dict, scenes: list[dict]) -> list[list[KeyframeMeta]]:
    """stage 7: 每场景关键帧元数据。ts 为近似锚定:story 段内按镜头索引线性
    插值(取镜头中点),无段覆盖时按场景窗口位置;一律 clamp 进场景窗口。"""
    spans = [(st["shot"][0], st["shot"][1],
              st["duration"][0], st["duration"][1])
             for st in ann.get("story") or []]
    out = []
    seen_shots: set[int] = set()  # scene.shot 为闭区间,相邻场景共享边界镜头:
    for sc in scenes:             # 每个镜头只归属首个出现的场景,frame_id 才全局唯一
        a, b = sc["shot_span"]
        entries = []
        for k in range(a, b + 1):
            if k in seen_shots:
                continue
            seen_shots.add(k)
            t = None
            for sa, sb, da, db in spans:
                if sa <= k <= sb and sb > sa:
                    t = da + (k - sa + 0.5) / (sb - sa + 1) * (db - da)
                    break
            if t is None:  # story 未覆盖:场景窗口内按镜头位置
                t = sc["start"] + (k - a + 0.5) / (b - a + 1) \
                    * (sc["end"] - sc["start"])
            t = min(max(t, sc["start"]), sc["end"])  # clamp(§3.3 窗口校验)
            for img in range(3):
                entries.append(KeyframeMeta(frame_id=f"s{k:04d}i{img}",
                                            ts=fmt_ts(t)))
        out.append(entries)
    return out


def extract_keyframes(kf_tar: Path, out_dir: Path, wanted: set[str]) -> int:
    """stage 7 落盘:关键帧 tar → out_dir/s{shot}i{img}.jpg。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    with tarfile.open(kf_tar) as tf:
        for member in tf:
            m = re.match(r"(?:[^/]+/)?shot_(\d+)_img_(\d)\.jpg$", member.name)
            if not m or not member.isfile():
                continue
            fid = f"s{int(m.group(1)):04d}i{m.group(2)}"
            if fid not in wanted:
                continue
            fobj = tf.extractfile(member)
            if fobj is None:
                continue
            (out_dir / f"{fid}.jpg").write_bytes(fobj.read())
            n += 1
    return n


def write_report(out: Path, movie: str, fps: float, n_scenes: int,
                 merged_count: int, n_chars: int, stats: dict,
                 places: list[str], descs: list[str], kf_counts: list[int],
                 n_files: int, validation: str) -> None:
    kept = stats["total"] - stats["no_script_match"] \
        - stats["speaker_not_in_roster"] - stats["outside_scenes"]
    lines = [
        f"# MovieNet 转换报告 — {movie}", "",
        f"- fps 标定: {fps:.4f}",
        f"- 场景数: {n_scenes}",
        f"- 亚秒场景合并: {merged_count}",
        f"- 角色数: {n_chars}",
        f"- 台词: 总数 {stats['total']}, 剧本未匹配 {stats['no_script_match']}, "
        f"说话人不在 roster {stats['speaker_not_in_roster']}, "
        f"场景窗口外 {stats['outside_scenes']}, 保留 {kept}"
        f"({kept / stats['total']:.0%})" if stats["total"] else "- 台词: 总数 0",
        f"- place 覆盖: {sum(1 for p in places if p)}/{n_scenes}",
        f"- description 覆盖: {sum(1 for d in descs if d)}/{n_scenes}",
        f"- 关键帧: 落盘 {n_files} 个, 每场景中位 {statistics.median(kf_counts):.0f}, "
        f"min {min(kf_counts)}, max {max(kf_counts)}"
        if kf_counts else "- 关键帧: 0",
        f"- loader 校验: {validation}",
        "",
        "已知局限(DESIGN §3.4): keyframe.ts 为近似锚定(±数秒);"
        "关键帧密度 = 每镜头 3 帧;virtual_time 留空。",
        "",
    ]
    (out / "convert_report.md").write_text("\n".join(lines), encoding="utf-8")


def _fetch_hint(movie: str) -> str:
    return f"先运行 kg movienet fetch --movie {movie}"


def convert(movie: str, src: Path, out: Path) -> Path:
    """九个 stage 全流程;自校验通过才返回输出目录。"""
    hint = _fetch_hint(movie)
    try:
        ann = json.loads((src / "annotation" / f"{movie}.json")
                         .read_text(encoding="utf-8"))
        meta = json.loads((src / "meta" / f"{movie}.json")
                          .read_text(encoding="utf-8"))
        srt_text = (src / "subtitle" / f"{movie}.srt") \
            .read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise ConvertError(f"源文件缺失或不可读: {e.filename}({hint})") from e
    script_path = src / "script" / f"{movie}.script"
    script_text = (script_path.read_text(encoding="utf-8", errors="replace")
                   if script_path.exists() else "")

    fps = calibrate_fps(ann.get("story") or [])               # stage 1
    scenes, merged_count = scene_windows(ann, fps)            # stage 2
    cast_rosters = scene_characters(ann, meta, scenes)         # stage 3
    movie_roster = sorted({n for r in cast_rosters for n in r})
    per_dlg, dlg_stats = scene_dialogues(srt_text, script_text, scenes,
                                         movie_roster)         # stage 4
    places = [scene_place(sc) for sc in scenes]                # stage 5
    descs = scene_descriptions(ann, scenes)                    # stage 6
    kf_lists = keyframe_entries(ann, scenes)                   # stage 7

    # 说话人并入所在场景名单(cast 标注可能漏人;保证 loader char 对齐)
    scene_chars: list[list[str]] = []
    for roster, dlgs in zip(cast_rosters, per_dlg):
        names = list(roster)
        for d in dlgs:
            if d.char not in names:
                names.append(d.char)
        scene_chars.append(names)

    out.mkdir(parents=True, exist_ok=True)                     # stage 8
    (out / "scenes").mkdir(exist_ok=True)
    (out / "movie.json").write_text(json.dumps(
        {"movie_id": out.name, "title": meta.get("title", movie)},
        ensure_ascii=False, indent=1), encoding="utf-8")
    for sc, names, dlgs, place, desc, kfs in zip(
            scenes, scene_chars, per_dlg, places, descs, kf_lists):
        m = SceneMeta(scene_id=f"scene_{sc['seq']:03d}", movie_id=out.name,
                      start_ts=fmt_ts(sc["start"]), end_ts=fmt_ts(sc["end"]),
                      characters=[CharacterMeta(name=n) for n in names],
                      dialogues=dlgs, location=place, virtual_time="",
                      description=desc, props=[], shots=[], keyframes=kfs)
        (out / "scenes" / f"scene_{sc['seq']:03d}.json").write_text(
            m.model_dump_json(exclude_none=True), encoding="utf-8")
    wanted = {k.frame_id for kfs in kf_lists for k in kfs}
    try:
        n_files = extract_keyframes(src / "keyframes" / f"{movie}.tar",
                                    out / "frames", wanted)
    except OSError as e:
        raise ConvertError(f"关键帧包缺失或不可读: {e.filename}({hint})") from e

    validation = "通过"                                        # stage 9
    try:
        load_movie(out)
    except InputValidationError as e:
        validation = "失败: " + "; ".join(e.errors[:5])
    n_chars = len({n for names in scene_chars for n in names})
    write_report(out, movie, fps, len(scenes), merged_count, n_chars,
                 dlg_stats, places, descs, [len(kfs) for kfs in kf_lists],
                 n_files, validation)
    if validation != "通过":
        raise ConvertError(f"自校验失败,详见 {out / 'convert_report.md'}")
    return out
