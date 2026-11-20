# 空气质量治理行动分析

面向城市环境部门的月度空气质量变化解释工具。导入站点及仪器有效期、
污染物观测、气象资料、治理行动及其覆盖范围，生成按站点和区域解释的
趋势分析，并把需要复核的变化登记为有责任区域和跟进期限的行动。

## 运行环境

需要 Python 3.11 或更新版本。项目仅使用 Python 标准库。

## 快速开始

```bash
# 1. 导入数据（站点、观测 v1、气象、治理行动）
python -m aqanalysis ingest --data-dir data

# 2. 分析观测覆盖的所有月份
python -m aqanalysis analyze --all

# 3. 发布某月报告（输出 Markdown，同时存档为不可变修订）
python -m aqanalysis report --period 2026-04

# 4. 补录/更正观测（data/backfill 中为 obs_version=2 的记录）
python -m aqanalysis ingest --data-dir data/backfill

# 5. 查看哪些分析与未关闭复核行动需要重新评估
python -m aqanalysis status

# 6. 重新分析并发布修订版（修订说明解释判断为何改变或维持）
python -m aqanalysis analyze --all
python -m aqanalysis report --period 2026-04

# 7. 复核完成后关闭行动
python -m aqanalysis close R-202603-S02-xxxxxx
```

状态默认写入 `.aqstate/`（可用 `--state-dir` 更改），删除该目录即可重置。

## 分析口径

- **观测覆盖**：单日有效需 `quality_flag=valid`、有浓度值且有效小时数 ≥18；
  月覆盖 = 有效天数 / 段内应测天数，≥80% 为充足，50–80% 为不足（谨慎），
  <50% 为不足。缺测不当作达标：覆盖不足时不给出达标结论。
- **序列分段**：仪器更换或迁址把站点序列切成独立的段，跨边界的两期
  不作直接比较（`boundary_confounded`），变化量仅供参考，绝不静默拼接。
- **气象校正**：段内拟合 `pm25 ~ 1 + 气温 + 风速 + 降雨`（普通最小二乘，
  段内无变化的气象项自动剔除），校正值 = 实测均值 − 当月气象效应。
  报告中实测与校正结果始终同时列出。
- **归因**：原始变化与校正后变化对照——校正后残余在噪声带宽内则判
  “主要由气象条件解释”；残余显著则判“扣除气象影响后仍有显著变化”并
  登记复核。覆盖不足或气象资料不足时**不作确定性归因**
  （`insufficient_data` / `met_unavailable`）。
- **治理行动**：行动带适用区域范围；跨区域行动在各区域结果中标注
  “成效不得重复记功”，全市汇总时同一行动只计一次。
- **复核行动**：显著变化、覆盖不足、边界附近显著变化都会登记复核，
  含责任区域与 30 天跟进期限。补录/更正观测后，未关闭行动与相关分析
  自动标记“需重新评估”。
- **报告修订**：每份报告修订保存发布时的观测版本、方法口径与行动状态，
  历史修订不改写；再次发布时自动生成修订说明，解释既有判断为何改变
  或为何维持。

## 数据格式

见 `data/README.md`。`data/` 为合成演示数据，可由
`python scripts/generate_fixtures.py data` 重新生成。

## 本地验证

```bash
python -m unittest discover -s tests -v   # 单元与端到端测试
python -m compileall -q aqanalysis tests scripts   # 语法检查
```

## 代码结构

```
aqanalysis/
  method.py     方法口径常量（阈值集中定义，随报告快照保存）
  models.py     数据模型（观测记录带版本号）
  ingest.py     CSV 导入与校验
  store.py      状态存储、版本解析、变更日志
  segments.py   仪器更换/迁址分段
  coverage.py   覆盖评估与达标判定
  metadjust.py  气象校正（OLS）
  trends.py     月度趋势、归因决策树、区域聚合、行动匹配
  review.py     复核行动登记与再评估标记
  reports.py    报告发布（不可变修订）与 Markdown 呈现
  cli.py        命令行入口
scripts/generate_fixtures.py   合成演示数据生成（确定性）
tests/                         单元测试与端到端流程测试
```
