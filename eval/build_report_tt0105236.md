# 构建端评估报告

## 角色识别(逐场景)

| 场景 | gold | pred | P | R | F1 |
| --- | --- | --- | --- | --- | --- |
| scene_001 | Mr. Blonde,Mr. Brown,Mr. Orange | Mr. Blonde,Mr. Brown,Mr. Orange | 1.0 | 1.0 | 1.0 |
| scene_002 | Joe Cabot,Mr. Blonde,Mr. Blue,Mr. Brown,Mr. Orange,Mr. Pink,Mr. White,Nice Guy Eddie | Joe Cabot,Mr. Blonde,Mr. Brown,Mr. Orange,Mr. Pink,Mr. White,Nice Guy Eddie | 1.0 | 0.875 | 0.9333 |
| scene_003 | Joe Cabot,Mr. Blonde,Mr. Orange,Mr. Pink,Mr. White | Joe Cabot,Mr. Blonde,Mr. Orange,Mr. Pink,Mr. White | 1.0 | 1.0 | 1.0 |
| scene_004 | Joe Cabot,Mr. Blonde,Mr. Orange,Mr. Pink,Mr. White,Nice Guy Eddie | Mr. Pink,Mr. White,Nice Guy Eddie | 1.0 | 0.5 | 0.6667 |
| scene_005 | Mr. Pink,Mr. White,Nice Guy Eddie | Mr. Pink,Nice Guy Eddie | 1.0 | 0.6667 | 0.8 |
| scene_006 | Mr. Pink,Mr. White | Mr. Pink,Mr. White | 1.0 | 1.0 | 1.0 |
| scene_007 | Joe Cabot,Mr. Pink,Mr. White | Joe Cabot,Mr. Pink,Mr. White | 1.0 | 1.0 | 1.0 |
| scene_008 | Holdaway,Joe Cabot,Mr. Blonde,Mr. Brown,Mr. Orange,Mr. Pink,Mr. White,Nice Guy Eddie | Holdaway,Joe Cabot,Mr. Blonde,Mr. Brown,Mr. Orange,Mr. Pink,Mr. White,Nice Guy Eddie | 1.0 | 1.0 | 1.0 |
| scene_009 | Joe Cabot,Marvin Nash,Mr. Blonde,Mr. Pink,Mr. White | Joe Cabot,Mr. Blonde,Mr. Pink,Mr. White | 1.0 | 0.8 | 0.8889 |
| scene_010 | Holdaway,Joe Cabot,Marvin Nash,Mr. Blonde,Mr. Pink,Mr. White,Nice Guy Eddie | Holdaway,Joe Cabot,Mr. Blonde,Nice Guy Eddie | 1.0 | 0.5714 | 0.7273 |
| scene_011 | Mr. Pink,Mr. White,Nice Guy Eddie | Mr. Pink,Mr. White | 1.0 | 0.6667 | 0.8 |
| scene_012 | Mr. Pink,Mr. White,Nice Guy Eddie | Nice Guy Eddie | 1.0 | 0.3333 | 0.5 |
| scene_013 | Mr. White,Nice Guy Eddie | Mr. White,Nice Guy Eddie | 1.0 | 1.0 | 1.0 |
| scene_014 | Mr. White,Nice Guy Eddie | Mr. White | 1.0 | 0.5 | 0.6667 |
| scene_015 | Marvin Nash,Mr. Pink,Nice Guy Eddie |  | 0.0 | 0.0 | 0.0 |
| scene_016 | Marvin Nash,Mr. Pink,Mr. White,Nice Guy Eddie | Nice Guy Eddie | 1.0 | 0.25 | 0.4 |
| scene_017 | Mr. Blonde,Nice Guy Eddie |  | 0.0 | 0.0 | 0.0 |
| scene_018 | Mr. Blonde,Nice Guy Eddie | Nice Guy Eddie | 1.0 | 0.5 | 0.6667 |
| scene_019 | Mr. Blonde,Nice Guy Eddie | Mr. Blonde | 1.0 | 0.5 | 0.6667 |
| scene_020 | Joe Cabot,Marvin Nash,Mr. Blonde,Mr. Orange,Mr. Pink,Mr. White,Nice Guy Eddie | Joe Cabot,Marvin Nash,Mr. Blonde,Mr. Orange,Mr. Pink,Mr. White,Nice Guy Eddie | 1.0 | 1.0 | 1.0 |
| scene_021 | Holdaway,Mr. Blonde,Mr. Orange,Mr. White | Holdaway,Mr. Blonde,Mr. Orange | 1.0 | 0.75 | 0.8571 |
| scene_022 | Holdaway,Mr. Orange,Mr. Pink,Mr. White | Holdaway,Mr. Orange | 1.0 | 0.5 | 0.6667 |
| scene_023 | Mr. White |  | 0.0 | 0.0 | 0.0 |
| scene_024 | Holdaway,Joe Cabot,Mr. Orange,Mr. White,Nice Guy Eddie | Mr. Orange,Mr. White | 1.0 | 0.4 | 0.5714 |
| scene_025 | Joe Cabot,Mr. Orange,Mr. Pink,Mr. White,Nice Guy Eddie | Joe Cabot,Mr. Orange,Mr. Pink,Mr. White,Nice Guy Eddie | 1.0 | 1.0 | 1.0 |
| scene_026 | Mr. Orange,Nice Guy Eddie | Mr. Orange,Nice Guy Eddie | 1.0 | 1.0 | 1.0 |
| scene_027 | Mr. Orange,Mr. White,Nice Guy Eddie | Mr. Orange,Mr. White,Nice Guy Eddie | 1.0 | 1.0 | 1.0 |
| scene_028 | Mr. White | Mr. White | 1.0 | 1.0 | 1.0 |
| scene_029 | Joe Cabot,Mr. Orange,Mr. Pink,Mr. White | Joe Cabot,Mr. Orange,Mr. Pink,Mr. White | 1.0 | 1.0 | 1.0 |
| scene_030 | Holdaway,Joe Cabot,Marvin Nash,Mr. Orange | Holdaway,Marvin Nash | 1.0 | 0.5 | 0.6667 |
| scene_031 | Mr. Orange,Mr. White,Nice Guy Eddie | Mr. Orange,Mr. White,Nice Guy Eddie | 1.0 | 1.0 | 1.0 |
| scene_032 | Joe Cabot,Mr. Blonde,Mr. Orange,Mr. Pink,Mr. White,Nice Guy Eddie | Joe Cabot,Mr. Pink,Mr. White,Nice Guy Eddie | 1.0 | 0.6667 | 0.8 |
| scene_033 | Joe Cabot,Mr. Blonde,Mr. Blue,Mr. Brown,Mr. Orange,Mr. Pink,Mr. White,Nice Guy Eddie | Joe Cabot,Mr. Blonde,Mr. Brown,Mr. Orange,Mr. Pink,Mr. White,Nice Guy Eddie | 1.0 | 0.875 | 0.9333 |
| scene_034 | Mr. Blonde,Mr. White | Mr. Blonde,Mr. White | 1.0 | 1.0 | 1.0 |
| scene_035 | Mr. Brown,Mr. Orange,Mr. White | Mr. Brown,Mr. Orange,Mr. White | 1.0 | 1.0 | 1.0 |
| scene_036 | Mr. White |  | 0.0 | 0.0 | 0.0 |
| scene_037 | Joe Cabot,Marvin Nash,Mr. Blonde,Mr. Brown,Mr. Orange,Mr. Pink,Mr. White,Nice Guy Eddie | Joe Cabot,Mr. Orange,Mr. Pink,Mr. White,Nice Guy Eddie | 1.0 | 0.625 | 0.7692 |


## 汇总

| 指标 | 值 |
| --- | --- |
| scenes | 37 |
| precision | 0.8919 |
| recall | 0.6886 |
| f1 | 0.7562 |


## 失败场景

(见构建日志;无则略)
