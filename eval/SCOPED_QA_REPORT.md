# 按片问答(scoped QA)对照实验报告(2026-10-22)

设计:`docs/superpowers/specs/2026-10-22-movie-scoped-qa-design.md`
目的:量化跨片向量污染对问答的实际影响,确立 scoped 语义下的新基线。

## 实验设置

同一 60 题集、k=10、同日串行两腿(scoped 后跑,避免并发限速干扰):

- **global 腿**(复跑,本轮权威数):`kg eval qa --scope global` → 0.5167
- **scoped 腿**(每题隔离到所属片):`kg eval qa`(默认)→ 0.55

注:global 复跑 0.5167 低于历史 0.5667(2026-10-21 同配置)。全局路径有
逐字节回归测试钉住,判为作答/判卷 LLM 随机方差——60 题样本下 ±3 题
(±5pp)在方差带内。历史值引作参照,本实验结论以同日配对差值为准。

## 总对照

| 指标 | global(污染) | scoped(隔离) | Δ |
|---|---|---|---|
| accuracy | 0.5167 | **0.55** | **+3.3pp** |
| mean_hit_rate | 0.5972 | **0.6222** | **+2.5pp** |

分意图:

| 意图 | global | scoped | Δ |
|---|---|---|---|
| factual | 0.75 | 0.8333 | +8.3pp |
| causal | 0.5833 | 0.5 | -8.3pp(1 题翻转,噪声带内) |
| relational | 0.5833 | **0.75** | **+16.7pp** |
| temporal | 0.1667 | 0.1667 | 0(结构性缺口,非污染) |
| synthesis | 0.5 | 0.5 | 0 |

## 按片(scoped 腿新增汇总)

| 影片 | n | accuracy | mean_hit_rate |
|---|---|---|---|
| tt0105236(Reservoir Dogs) | 20 | 0.35 | 0.825 |
| tt0113243(Hackers) | 20 | 0.60 | 0.575 |
| tt0117500(The Rock) | 20 | **0.70** | 0.4667 |

## 逐题翻转(6/60)

**改善 4 题(F→T):**

| 题 | 意图 | hit_rate 变化 | 备注 |
|---|---|---|---|
| rd_r4 | relational | 1.0→1.0 | 判定翻转,作答差异 |
| hk_f2 | factual | 0.3333→0.3333 | 判定翻转,作答差异 |
| rk_r2 | relational | 0.5→0.5 | 判定翻转,作答差异 |
| rk_s4 | synthesis | 0.0→0.0 | 两腿都未命中 gold,作答差异 |

**恶化 2 题(T→F):**

| 题 | 意图 | hit_rate 变化 | 备注 |
|---|---|---|---|
| rd_s1 | synthesis | 1.0→1.0 | 作答引入仓库代号段落,判卷挑剔 |
| hk_c3 | causal | 1.0→1.0 | 归因表述差异(威胁 vs 真相逼近) |

**关键观察:6 个判定翻转的 hit_rate 几乎都没动**——accuracy 差值
(+2 题)完全在作答/判卷噪声带内,方向与污染移除一致但单看不足以下结论。

## 检索层证据(hit_rate 逐题变化,更能归因于隔离本身)

hit_rate 净变化 +2.5pp,集中在 5 题:

- **+0.5 × rd_f1 / rd_c4 / rd_t1,+0.33 × rk_r3**(4 题改善):
  rd_t1 正是文档记录的污染案例(global 检索 top 命中全是别片事件,
  hit 0.0 → scoped 0.5)——隔离把被别片挤占的席位还给了本片 gold
- **-0.33 × rd_t2**(1 题回落):非隔离逻辑悖论——规划器改写查询是
  LLM 逐次生成,两腿查询本身不同,排名差异属查询方差

## 结论

1. **跨片污染的真实代价 ≈ +2.5pp hit_rate / +3.3pp accuracy**(同日配对)。
   检索层的改善是机理性的(污染案例 rd_t1 直接翻正);accuracy 增益方向
   一致但幅度在判卷噪声带内,n=60 下不宜过度解读
2. **relational +16.7pp 是最大分意图增益**——关系题的实体绑定受益于
   按片收窄(同名角色不再错绑)
3. **scoped 0.55 为新官方基线**(eval 默认语义已切 scoped;0.7 验收线
   按此口径衡量,当前差 15pp)
4. **隔离修不了 temporal**(两腿同为 0.1667):时序缺口是结构性的
   (宽窗口方向,见 ANSWERING_OPTIMIZATION_REPORT),与污染无关

## 附带发现:RD 的"送医"矛盾簇(数据质量,非检索)

RD 0.35 全场最低,但 mean_hit_rate 0.825 全场最高——证据大多都在,答案却错。
13 道失败题中 **4 题(rd_f2/rd_c3/rd_s2/rd_s3)是同一矛盾**:gold 称
"Mr. White 坚持送医",图谱证据却支撑"拒绝送医"。其中 rd_f2/rd_c3/rd_s2
两腿 hit 均 1.0、作答一致地与 gold 矛盾;rd_s3 两腿 hit 均 0.0——检索
也未命中,但作答同样落在"拒绝送医"上。这是 gold 与抽取结果的事实冲突
(非检索问题),解决该簇是单点最大杠杆:纯修 gold 可望 0.35→0.50
(rd_s3 还需检索修复才到 0.55),建议下一步核查
(evt_tt0105236_008_02 一带的抽取与 gold 谁对谁错)。

→ **已核查并修正,见下节(同日追加)。**

## 追加核查:送医矛盾簇 gold 修正(2026-10-22 同日)

裁决依据:**剧本原文**(`data/raw/movienet/script/tt0105236.script`),
图谱侧证据为只读查询(节点 topic/description + 因果/时序边)。

### 裁决:图谱对,gold 错

送医线剧本全程(行号为剧本文件行号):

| 位置 | 台词/情节 | 图谱对应 |
|---|---|---|
| 590-618 车内 | "You're hurt really fucking bad, but you ain't dying … Joe's gonna get you a doctor" | — |
| 713-718 场景4 | Orange:"I'm shot in the belly. And without medical attention, I'm gonna die";White:"**I can't take you to a hospital**" | 004_03/004_04 ✓ |
| 723-724 场景4 | Orange:"Just drive me up to the front, drop me on the sidewalk"(只求放到医院门口) | 004_03"送到医院门口"逐句对应 ✓ |
| 1393-1395 场景8 | White:"**He will be dead fer sure, if we don't get him to a hospital**" | 008_02 主题 ✓ |
| 1400-1407 场景8 | "won't live through the night. **That bullet in his belly is my fault** … I'm not gonna just sit around and watch him die" | 008_02 ✓ |
| 1423-1427 场景8 | White 具体主张:"**Joe could get him to a doctor**, Joe could get a doctor to come and see him" | 008_02"联系 Joe"✓ |
| 1449-1455 场景8 | White 承认 Orange 哀求过送医院,"I told him to hold off till Joe got here" | — |
| 1562-1569 场景8 | Pink:"**We ain't taking him to a hospital**";White:"If we don't, he'll die" | 008_02/008_04 ✓ |
| 2510-2517 结尾 | Eddie:"I'll call a doctor" | — |

**结论**:White 从未主张亲自送医院——他坚持的是"Orange 必须得到救治",
具体方案始终是 Joe/Eddie 找医生,且当面拒绝过 Orange 本人的送院哀求
(场景 4)。坚决反对送医院的是 **Pink**(rd_s1 的 gold 本来就写对了)。
图谱 4 条事件全部忠实于剧本;"gold 与抽取事实冲突"的矛头全部指向 gold。

### 修正(5 项:矛盾簇 4 + 连带发现 1)

| 题 | 修正 | 依据 |
|---|---|---|
| rd_f2 | gold 重写:伤势危重(腹部中弹、不治活不过当晚)+ 安抚称不会死 + 拒绝直接送院(怕暴露)、等 Joe 找医生 | 590-618 / 713-718 / 1400-1407 |
| rd_c3 | **问题去假前提**"坚持送到医院"→"坚持要得到救治";gold 重写:腹部中弹是自己造成的、有责任感、不肯看他死、主张联系 Joe | 1400-1427 |
| rd_s2 | gold:"坚持送医"→"坚持为他寻求救治(不肯眼睁睁看他死,主张联系 Joe 找医生)" | 同上 |
| rd_s3 | gold 重写:补全两层——Orange 苦求医院门口被拒 / White 对 Pink 强调不送必死;Pink 反对、争执升级动手拔枪;Blonde 另有所图 | 713-724 / 1393-1569 |
| rd_t2 | **连带发现**:问题前提为假——Holdaway 从未到过仓库(只存在于"MR. ORANGE"闪回训练线,剧本 2871 行起 Denny's 场景);且原 gold"怀疑内鬼"发生在 Blonde 到达之前,与 gold 锚点 008_06-08 错位。问题改为"Mr. Blonde 到达仓库之后发生了什么?",gold 与锚点对齐(008_05-08:指责滥杀→对峙→Blonde 夺主导权原地等 Joe→带 White/Pink 出仓库) | 1635-1643 + 008_05-08 |

### 隔离验证:旧答案 × 新 gold 重判(不重答,消除答案方差)

| 题 | 旧判定 | 重判 | 解读 |
|---|---|---|---|
| rd_f2 | False | **True** | gold 即失败原因,实锤 |
| rd_c3 | False | False | 预期内:旧答案是对旧问题假前提的"纠偏式"回答(开篇即"并未坚持"),顶撞新问法;重新作答应通过 |
| rd_s2 | False | False | 旧答案将 White 定性为"任务优先、冷酷",与新 gold"重情重义"在定性轴上真冲突(非送医表述问题) |
| rd_s3 | False | False | 预期内:hit 0.0,旧答案只覆盖 White↔Orange 层,缺 White↔Pink 层 |

### 新发现:事件描述语言三片均 ~50/50 中英混合

| 影片 | 事件数 | 中文描述 | 英文描述 |
|---|---|---|---|
| RD | 185 | 90 | 95 |
| Hackers | 579 | 313 | 266 |
| Rock | 1299 | 684 | 615 |

抽取输出语言未钉死(MovieNet 标注仅含英文粗粒度 action_tag,描述由视觉
LLM 生成)。中文 query 对英文描述事件召回偏弱,且送医簇恰好骑在语言分界
上:场景 4(中文)检索命中,场景 8(英文)落空(rd_s3 hit 0.0;rd_t2 原
gold 008_06-08 亦英文,两腿 hit 0.0)。注意非硬墙——rd_f2 的 gold 同为
英文 008_02 却 hit 1.0,语言弱势与查询改写方差复合作用。**建议**:抽取
prompt 钉死输出语言后重建,或对英文描述翻译归一后重嵌(可与 83 部扩量
的重建合并做)。

### 修正后全量重跑(scope=movie,同日)

**受阻**:重跑中途触发 SiliconFlow **402 账户余额耗尽**(继 2026-10-19
充值后再次耗尽),`run_qa` 中断、报告未写出(旧基线报告完好,锚定
45fe6e2)。隔离验证(上节)已确认 rd_f2 的失败原因即 gold。待账户充值后
执行 `kg eval qa`(命令与配置不变)即可补齐本节数字。
