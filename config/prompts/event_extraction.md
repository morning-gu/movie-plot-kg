你是影视剧剧情分析专家。分析以下场景的帧序列与台词,提取剧情事件。

场景先验: 角色名单=<<ROSTER>>, 标注地点=<<LOCATION>>, 标注时间=<<TIME>>
场景描述: <<SCENE_DESC>>
道具: <<PROPS>>
台词:
<<DIALOGUES>>
镜头时间轴:
<<SHOTS>>

要求:
- 事件按发生顺序排列;子事件同样输出完整结构,不输出纯文本描述
- 子事件不再嵌套子事件,其 subevents 保持为空数组
- 角色名必须从角色名单中选择;无法确定时标记 "未识别:原名"
- event_type ∈ dialogue / conflict / action / turning_point / discovery
- 标注描述(场景/镜头/关键帧)仅为辅助线索,判断以画面与台词为准
- 每个事件与子事件给出 evidence_frame_ids: 支撑该事件的关键帧 ID,只能引用本场景出现过的 frame_id;无法确定时给空数组

输出 JSON:
{"events": [{"event_type": "...", "topic": "...", "description": "...",
  "evidence_frame_ids": ["..."],
  "participating_characters": [{"name": "...", "emotion": "...", "role_description": "...", "items": ["..."]}],
  "subevents": [{"event_type": "...", "topic": "...", "description": "...",
   "evidence_frame_ids": [], "participating_characters": []}]}]}
