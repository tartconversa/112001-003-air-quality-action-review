# 合成输入数据

用于解释区域空气质量变化的合成监测、气象与治理记录。
所有记录均为虚构，仅供本地开发使用。日期与数值均为示例。
可由 `python scripts/generate_fixtures.py data` 重新生成（确定性输出）。

## stations.csv — 站点仪器/点位有效期

| 字段 | 说明 |
| --- | --- |
| station_id | 站点编号 |
| area_id | 所属监测区域 |
| instrument | 仪器型号 |
| valid_from / valid_to | 有效期起止；valid_to 为空表示至今有效 |
| event | 该段开始事件：`instrument_change`（仪器更换）/ `relocation`（迁址）/ 空（首段） |

同一站点的各段不得重叠。跨段的两期不作直接比较。

## observations.csv — 逐日 PM2.5 观测

| 字段 | 说明 |
| --- | --- |
| station_id | 站点编号 |
| date | 观测日期（YYYY-MM-DD） |
| pm25 | 日均浓度（µg/m³）；空值表示缺测 |
| valid_hours | 当日有效小时数（0–24），<18 视为无效日 |
| quality_flag | `valid` / `missing` 等；仅 `valid` 计入统计 |
| obs_version | 观测版本号；同一 (站点, 日期) 取版本最大者为有效值 |

补录或更正以更高 `obs_version` 的新行追加（见 `backfill/observations.csv`），
旧版本保留用于审计；导入时自动检测有效值变更并标记受影响的分析与复核行动。

## weather.csv — 区域逐日气象

| 字段 | 说明 |
| --- | --- |
| area_id | 监测区域 |
| date | 日期 |
| temp_c / wind_m_s / rain_mm | 气温（℃）/ 风速（m/s）/ 降雨（mm） |

## actions.csv — 治理行动

| 字段 | 说明 |
| --- | --- |
| action_id | 行动编号（唯一） |
| action_type | 行动类型 |
| area_scope | 适用监测区域，多个区域用 `;` 分隔 |
| start / end | 行动起止日期 |
| description | 说明 |

跨区域的行动在各区域结果中标注适用范围，全市汇总不重复记功。

## backfill/observations.csv — 补录与更正（obs_version=2）

演示用第二批数据：S02 三月 8 天缺测补录、S01 2026-02-14 异常值更正。
用 `python -m aqanalysis ingest --data-dir data/backfill` 导入，
随后 `status` 可查看需要重新评估的分析与行动。
