import base64
import json

from movie_kg.extraction.parse import parse_a1
from movie_kg.ingestion.frame_sampler import Frame
from movie_kg.ingestion.timefmt import fmt_ts
from movie_kg.models.extraction import A1Output
from movie_kg.models.scene import SceneData
from movie_kg.prompts import load_prompt, render


def _roster_text(scene: SceneData) -> str:
    return "; ".join(
        f"{c.name}({','.join(c.aliases)})" if c.aliases else c.name
        for c in scene.meta.characters)


def _dialogue_text(scene: SceneData) -> str:
    lines = []
    for d in scene.meta.dialogues:
        cue = (f"({'/'.join(x for x in (d.emotion, d.action) if x)})"
               if d.emotion or d.action else "")
        lines.append(f"{d.ts} {d.char}{cue}: {d.text}")
    return "\n".join(lines) if lines else "(无台词)"


def _scene_desc(scene: SceneData) -> str:
    return scene.meta.description or "(无)"


def _props_text(scene: SceneData) -> str:
    return ", ".join(scene.meta.props) if scene.meta.props else "(无)"


def _shots_text(scene: SceneData) -> str:
    lines = []
    for sh in scene.meta.shots:
        t = f" {sh.shot_type}" if sh.shot_type else ""
        d = f": {sh.description}" if sh.description else ""
        lines.append(f"{sh.shot_id} [{sh.start_ts}–{sh.end_ts}]{t}{d}")
    return "\n".join(lines) if lines else "(无镜头标注)"


def _frame_caption(f: Frame) -> str:
    if f.keyframe is None:
        return f"[补充帧 {fmt_ts(f.ts)}]"
    k = f.keyframe
    t = f" {k.frame_type}" if k.frame_type else ""
    d = f": {k.description}" if k.description else ""
    return f"帧 {k.frame_id} ({fmt_ts(f.ts)}{t}){d}"


def extract_scene_events(llm, frames: list[Frame], scene: SceneData) -> list:
    """A1: 帧(图文交错)+ 元数据台词 + 先验 -> 结构化事件(LLMError 向上抛)。"""
    system = render(load_prompt("event_extraction"),
                    ROSTER=_roster_text(scene),
                    LOCATION=scene.meta.location,
                    TIME=scene.meta.virtual_time,
                    SCENE_DESC=_scene_desc(scene),
                    PROPS=_props_text(scene),
                    DIALOGUES=_dialogue_text(scene),
                    SHOTS=_shots_text(scene))
    content: list[dict] = [{"type": "text", "text": "请分析以下帧序列与台词,输出 JSON。"}]
    for f in frames:
        b64 = base64.b64encode(f.jpeg).decode("ascii")
        content.append({"type": "text", "text": _frame_caption(f)})
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]
    cfg = getattr(llm, "cfg", None)
    model = cfg.vision_model if cfg is not None else "vision"
    raw = llm.chat_json(model=model, messages=messages, schema=A1Output)
    return parse_a1(json.loads(raw.model_dump_json()), scene.meta)
