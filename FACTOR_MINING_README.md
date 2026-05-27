# tinyKaggleClaw 因子挖掘工作流

这份文档说明如何用本项目持续挖掘、回测、复现和拯救 A 股日内因子。它是项目操作手册，包含具体命令、路径和脚本。若只需要给人参考的通用流程文档，请看 [docs/factor_mining_reference_workflow.md](docs/factor_mining_reference_workflow.md)。

## 一句话版本

从项目根目录启动：

```bash
cd /mnt/storage/work/hwang/tinyKaggleClaw
./ai_start.sh start
```

这会启动：

- Research MVP 运行面板
- 主因子挖掘守护进程
- factors.directory 复现守护进程

只启动因子挖掘相关进程：

```bash
./ai_start.sh forever
./ai_start.sh replicate
```

查看状态：

```bash
./ai_start.sh status
```

查看关键日志：

```bash
./ai_start.sh logs
```

## 运行前提

项目默认依赖这些本地组件：

- 项目虚拟环境：`.venv/bin/python`
- Codex CLI：`codex`
- gsim Python：`/usr/local/gsim/.venv/bin/python`
- gsim runner：`/usr/local/gsim/run.py`
- gsim summary：`/usr/local/gsim/tools/simsummary.py`
- 相关数据、pnl、已安装因子库在本机已有路径下可用

常用路径：

```text
/mnt/storage/work/hwang/tinyKaggleClaw
/usr/local/gsim/.venv/bin/python
/usr/local/gsim/run.py
/mnt/storage/work/hwang/pnl
```

## 核心脚本

### `ai_start.sh`

面向人和 AI 的统一启动入口。优先使用它。

```bash
./ai_start.sh guide
./ai_start.sh status
./ai_start.sh start
./ai_start.sh forever
./ai_start.sh replicate
./ai_start.sh rescue-last
./ai_start.sh logs
```

### `baseline/run_factor_mining_v1.sh`

启动一轮主因子挖掘。默认：

- `ITERS=40`
- `PARALLEL=8`
- `FACTOR_MINER_CODEGEN_PARALLEL=2`
- `FACTOR_MINER_DISCUSSION_PARALLEL=false`
- `FACTOR_MINER_CODEX_MODEL=gpt-5.4`
- `FACTOR_MINER_DISCUSSION_MODEL=gpt-5.4`
- `FACTOR_MINER_FEEDBACK_MODEL=gpt-5.4`

手动启动一轮：

```bash
./baseline/run_factor_mining_v1.sh
```

自定义规模：

```bash
ITERS=20 PARALLEL=4 FACTOR_MINER_CODEGEN_PARALLEL=1 ./baseline/run_factor_mining_v1.sh
```

### `scripts/run_factor_mining_forever.py`

主 miner 守护进程。它会持续启动新一轮 `IntraDay` 因子挖掘。

默认策略：

- 每轮 40 个 iter
- 回测并行 8
- Codex codegen 并行 2
- 三代理讨论串行
- 当前主结果达到 40 条后，启动下一轮
- rescue 可以异步跑，不阻塞下一轮主 miner

启动：

```bash
.venv/bin/python scripts/run_factor_mining_forever.py
```

推荐通过：

```bash
./ai_start.sh forever
```

### `scripts/run_factors_directory_replication_forever.py`

复现 `output/factors_directory` 中的 factors.directory 策略。它和主 miner 分开跑，产物仍然落在 `output/factor_mining/`。

默认策略：

- 每个目标复现 40 个 iter
- 回测并行 8
- Codex codegen 并行 2
- 三代理讨论串行
- 复现完成后写入 `output/factors_directory_replication/replication_ledger.csv`

启动：

```bash
./ai_start.sh replicate
```

手动启动：

```bash
FACTOR_REPLICATION_ITERS=40 \
FACTOR_REPLICATION_PARALLEL=8 \
FACTOR_REPLICATION_CODEGEN_PARALLEL=2 \
FACTOR_REPLICATION_DISCUSSION_PARALLEL=false \
.venv/bin/python scripts/run_factors_directory_replication_forever.py
```

## 输出目录

所有因子挖掘 run 默认写到：

```text
output/factor_mining/
```

主 miner 目录形如：

```text
output/factor_mining/factor_mining_IntraDay_20260513_130644/
```

factors.directory 复现目录形如：

```text
output/factor_mining/factor_mining_FactorsDir_0028_momentum-acceleration_20260513_130644/
```

单个 run 内常见文件：

```text
launch.log
results.csv
rescue_candidates.csv
continuous_rescue.csv
continuous_rescue.log
iter_001/
iter_002/
...
iter_040/
```

单个 iter 内常见文件：

```text
agent_1_researcher_response.md
agent_2_reviewer_response.md
agent_3_synthesizer_response.md
agent_response.md
Alpha*.py
Config.tinyclaw.xml
Config.tinyclaw.neg.xml
Config.tinyclaw.decay*.xml
Config.tinyclaw.rescue.xml
gsim.log
gsim.neg.log
gsim.rescue.log
pnl/
```

## 一轮因子的生命周期

每个 iter 通常经过这些阶段：

1. 选择研究方向
2. Agent 1 提出经济假设
3. Agent 2 审查相关性、可实现性和风险
4. Agent 3 合成最终生成 brief
5. Codex codegen 生成 `agent_response.md`
6. 提取因子代码，写出 `Alpha*.py`
7. 写出 `Config.tinyclaw.xml`
8. 调用 gsim 回测
9. 解析 simsummary 指标
10. 记录到 `results.csv`
11. 达标则入库
12. 不达标但有潜力则进入 neg、decay 或 rescue

结果记录的核心文件是：

```text
results.csv
```

它包含：

```text
time,iteration,factor,sharpe,ret_pct,tvr_pct,dd_pct,fitness,accepted,reason,max_corr
```

常见 `reason`：

```text
accepted_and_installed
below_threshold
high_corr
codegen failed: agent failed rc=1
gsim failed
simsummary failed
```

## 启动建议

### 正常持续挖掘

```bash
cd /mnt/storage/work/hwang/tinyKaggleClaw
./ai_start.sh start
```

### 只跑主 miner

```bash
./ai_start.sh forever
```

### 只跑 factors.directory 复现

```bash
./ai_start.sh replicate
```

### 单轮调试

```bash
ITERS=5 PARALLEL=2 FACTOR_MINER_CODEGEN_PARALLEL=1 ./baseline/run_factor_mining_v1.sh
```

### 停止所有项目相关进程

先查看：

```bash
pgrep -af 'tinyKaggleClaw|/usr/local/gsim/run.py'
```

终止时要覆盖：

```bash
pgrep -af 'scripts/run_factor_mining_forever.py'
pgrep -af 'scripts/run_factors_directory_replication_forever.py'
pgrep -af 'src.baseline.local_factor_miner'
pgrep -af 'codex exec .*tinyKaggleClaw'
pgrep -af '/usr/local/gsim/run.py /mnt/storage/work/hwang/tinyKaggleClaw'
```

## 并行策略

当前默认是有界并行，不再使用无限并行。

推荐默认值：

```text
主 miner 回测并行: 8
复现回测并行: 8
rescue 并行: 8
Codex codegen 并行: 2
三代理讨论: 串行
```

原因：

- 过高 Codex 并发容易触发 `429 Too Many Requests`
- 触发 429 后会导致整批 iter 在 agent 阶段失败
- gsim 回测可以较高并行，但也应避免把机器资源打满
- 默认目标是稳定产出有效代码，而不是瞬间提交大量无效请求

临时提高并行：

```bash
PARALLEL=12 FACTOR_MINER_CODEGEN_PARALLEL=3 ./baseline/run_factor_mining_v1.sh
```

临时降低并行：

```bash
PARALLEL=4 FACTOR_MINER_CODEGEN_PARALLEL=1 ./baseline/run_factor_mining_v1.sh
```

factors.directory 复现：

```bash
FACTOR_REPLICATION_PARALLEL=4 \
FACTOR_REPLICATION_CODEGEN_PARALLEL=1 \
FACTOR_REPLICATION_DISCUSSION_PARALLEL=false \
./ai_start.sh replicate
```

## 常看文件

优先看小文件：

```text
logs/
output/factor_mining/<run>/launch.log
output/factor_mining/<run>/results.csv
output/factor_mining/<run>/rescue_candidates.csv
output/factor_mining/<run>/continuous_rescue.csv
output/factors_directory_replication/replication_ledger.csv
output/factors_directory_replication/state.json
```

不要直接读取大日志：

```text
iter_*/gsim.log
iter_*/gsim.rescue.log
iter_*/gsim.neg.log
大型 agent_*.log
```

先看大小：

```bash
stat -c '%n %s bytes' <file>
```

只看尾部：

```bash
tail -30 <file>
```

## 低 token 排障命令

### 看守护进程是否还在

```bash
pgrep -af 'run_factor_mining_forever.py|run_factors_directory_replication_forever.py'
```

### 看 miner、Codex、gsim 并发数量

```bash
printf 'miner='
pgrep -af 'src.baseline.local_factor_miner' | wc -l
printf 'codex='
pgrep -af 'codex exec .*tinyKaggleClaw' | wc -l
printf 'gsim='
pgrep -af '/usr/local/gsim/run.py /mnt/storage/work/hwang/tinyKaggleClaw' | wc -l
```

### 看某个 run 的进度

```bash
RUN=output/factor_mining/<run>
find "$RUN" -maxdepth 1 -type d -name 'iter_*' | wc -l
wc -l "$RUN/results.csv"
tail -40 "$RUN/launch.log"
```

### 看生成了多少代码和 XML

```bash
RUN=output/factor_mining/<run>
find "$RUN" -maxdepth 2 -type f \( -name '*.py' -o -name 'Config.tinyclaw.xml' -o -name 'agent_response.md' \) | wc -l
find "$RUN" -maxdepth 2 -type f \( -name '*.py' -o -name 'Config.tinyclaw.xml' -o -name 'agent_response.md' \) | head -40
```

### 汇总失败原因

```bash
RUN=output/factor_mining/<run>
awk -F, 'NR>1 {reason=$10; sub(/ log=.*/, "", reason); count[reason]++} END {for (r in count) print count[r], r}' "$RUN/results.csv" | sort -nr
```

### 看是否是 Codex 限流

先从 `launch.log` 找一个失败日志路径，然后只看尾部：

```bash
tail -25 output/factor_mining/<run>/iter_001/agent_1_researcher.log
```

典型限流特征：

```text
429 Too Many Requests
403 Forbidden
failed to connect to websocket
usage limit
exceeded retry limit
```

如果出现这些，应降低：

```text
FACTOR_MINER_CODEGEN_PARALLEL
FACTOR_REPLICATION_CODEGEN_PARALLEL
FACTOR_MINER_DISCUSSION_PARALLEL
FACTOR_REPLICATION_DISCUSSION_PARALLEL
```

## 判断 run 卡在哪

### 只有 prompt/log，没有代码

表现：

```text
iter_*/agent_1_researcher_response.md
iter_*/agent_2_reviewer_response.md
iter_*/agent_3_synthesizer_response.md
没有 Alpha*.py
没有 Config.tinyclaw.xml
```

可能原因：

- Codex codegen 尚未完成
- Codex 限流或额度耗尽
- agent 阶段失败

检查：

```bash
tail -40 output/factor_mining/<run>/launch.log
awk -F, 'NR>1 {reason=$10; sub(/ log=.*/, "", reason); count[reason]++} END {for (r in count) print count[r], r}' output/factor_mining/<run>/results.csv | sort -nr
```

### 有代码，没有回测结果

表现：

```text
Alpha*.py 存在
Config.tinyclaw.xml 存在
results.csv 没有对应 iteration
```

可能原因：

- gsim 还在跑
- gsim 子进程被杀
- owner miner 退出后没有记录结果

检查：

```bash
pgrep -af '/usr/local/gsim/run.py /mnt/storage/work/hwang/tinyKaggleClaw'
```

补跑：

```bash
/usr/local/gsim/.venv/bin/python scripts/backtest_pending_runs.py output/factor_mining/<run> --parallel 8
```

### results 已满，但没有 rescue

表现：

```text
results.csv 有 40 条结果
没有 continuous_rescue.csv
没有 rescue_candidates.csv
```

可能原因：

- 没有候选可救
- 全部 codegen failed，没有可回测因子
- watcher 没启动

检查：

```bash
ls output/factor_mining/<run>/rescue_candidates.csv
tail -40 output/factor_mining/<run>/launch.log
```

手动挂 watcher：

```bash
/usr/local/gsim/.venv/bin/python scripts/rescue_run_when_complete.py output/factor_mining/<run> --expected-results 40 --parallel 8
```

### factors.directory 复现没有继续

看 ledger 和 state：

```bash
tail -20 output/factors_directory_replication/replication_ledger.csv
cat output/factors_directory_replication/state.json
pgrep -af 'run_factors_directory_replication_forever.py'
```

如果 state 标记某个 slug 已完成，即使该轮失败，默认也不会再复现它，除非调整 state 或提高每个 slug 的尝试次数：

```bash
FACTOR_REPLICATION_MAX_ATTEMPTS_PER_SLUG=2 ./ai_start.sh replicate
```

## factors.directory 复现流程

输入文件：

```text
output/factors_directory/factors_directory_zh.csv
output/factors_directory/factors_directory_zh.json
```

守护进程会筛选 `feasible_5m` 为：

```text
yes
partial
```

每个目标会设置：

```text
FACTOR_MINER_LIBRARY_TARGET_SLUG
FACTOR_MINER_LIBRARY_TARGET_TITLE
FACTOR_MINER_LIBRARY_TARGET_URL
FACTOR_MINER_LIBRARY_TARGET_TEXT
FACTOR_MINER_LIBRARY_REPLICATION_BIAS=true
```

输出 run 名称：

```text
factor_mining_FactorsDir_<序号>_<slug>_<时间戳>
```

复现结果总账：

```text
output/factors_directory_replication/replication_ledger.csv
```

状态文件：

```text
output/factors_directory_replication/state.json
```

## rescue 和补救

### 自动 rescue

主 forever 守护进程会在一轮主结果完成后异步救援 `rescue_candidates.csv`。

factors.directory 复现守护进程也会在每个目标完成后尝试 continuous rescue。

### 手动 rescue 最新 run

```bash
./ai_start.sh rescue-last
```

### 手动 rescue 指定候选

```bash
/usr/local/gsim/.venv/bin/python scripts/rescue_specific_candidates.py \
  --parallel 8 \
  --out output/factor_mining/<run>/manual_rescue.csv \
  --candidate output/factor_mining/<run>/iter_003:AlphaName:base
```

候选格式：

```text
iter_dir:factor_name:kind
```

`kind` 常见值：

```text
base
neg
decay8
decay10
decay15
decay20
rescue
```

## 入库标准和相关性

`local_factor_miner.py` 默认阈值可通过环境变量覆盖：

```text
FACTOR_MINER_MIN_SHARPE=3.0
FACTOR_MINER_MIN_RET=20.0
FACTOR_MINER_MAX_CORR=0.7
FACTOR_MINER_MAX_TVR=60.0
```

常见判断：

- Sharpe 和 return 达标
- TVR 不过高
- 与已安装因子相关性不过高
- 安装过程成功

如果 `max_corr` 太高，流程会尽量通过 decorrelation/rescue prompt 引导生成低相关变体。

## prompt 设计原则

当前 prompt 偏向低相关日内结构：

- 日内过度反应和反转
- 动量脉冲
- 成交额突增后的波动结构
- 高低价区间成交集中
- 日内成交额 W 型结构
- 价量匹配和背离
- event-time bar
- nonlinear distribution statistic
- breadth/state conditioning

避免：

- 简单 VWAP 位置重复组合
- 原始成交额水平
- 原始 turnover-like 比率
- 固定前半天/后半天线性拼接
- 和已安装因子高度相似的模板

## 常用环境变量

主 miner：

```text
ITERS
PARALLEL
FACTOR_MINER_CODEGEN_PARALLEL
FACTOR_MINER_DISCUSSION_PARALLEL
FACTOR_MINER_DISCUSSION_MODEL
FACTOR_MINER_FEEDBACK_MODEL
FACTOR_MINER_AGENT_RETRIES
FACTOR_MINER_AGENT_RETRY_BASE_SLEEP
FACTOR_MINER_MIN_SHARPE
FACTOR_MINER_MIN_RET
FACTOR_MINER_MAX_CORR
FACTOR_MINER_MAX_TVR
```

factors.directory 复现：

```text
FACTOR_REPLICATION_ITERS
FACTOR_REPLICATION_PARALLEL
FACTOR_REPLICATION_CODEGEN_PARALLEL
FACTOR_REPLICATION_DISCUSSION_PARALLEL
FACTOR_REPLICATION_MAX_ATTEMPTS_PER_SLUG
FACTOR_REPLICATION_RESCUE_PARALLEL
```

gsim：

```text
GSIM_PYTHON
GSIM_RUN
GSIM_SUMMARY
```

## 推荐日常操作顺序

1. 启动：

```bash
./ai_start.sh forever
./ai_start.sh replicate
```

2. 等几分钟后看轻量状态：

```bash
./ai_start.sh status
pgrep -af 'codex exec .*tinyKaggleClaw' | wc -l
pgrep -af '/usr/local/gsim/run.py /mnt/storage/work/hwang/tinyKaggleClaw' | wc -l
```

3. 查看最新 run：

```bash
ls -td output/factor_mining/factor_mining_* | head
```

4. 看最新 run 的 `launch.log` 和 `results.csv`：

```bash
RUN=$(ls -td output/factor_mining/factor_mining_* | head -1)
tail -50 "$RUN/launch.log"
wc -l "$RUN/results.csv"
```

5. 汇总失败原因：

```bash
awk -F, 'NR>1 {reason=$10; sub(/ log=.*/, "", reason); count[reason]++} END {for (r in count) print count[r], r}' "$RUN/results.csv" | sort -nr
```

6. 如果有代码但没结果，补回测：

```bash
/usr/local/gsim/.venv/bin/python scripts/backtest_pending_runs.py "$RUN" --parallel 8
```

7. 如果有 rescue 候选，救援：

```bash
./ai_start.sh rescue-last
```

## 常见问题

### 为什么目录里没有因子代码？

先判断是否真的没有：

```bash
RUN=output/factor_mining/<run>
find "$RUN" -maxdepth 2 -type f \( -name '*.py' -o -name 'Config.tinyclaw.xml' -o -name 'agent_response.md' \) | head
```

如果只有 agent response，没有 `Alpha*.py`，说明最终 codegen 还没成功或失败了。

看原因：

```bash
tail -50 "$RUN/launch.log"
awk -F, 'NR>1 {reason=$10; sub(/ log=.*/, "", reason); count[reason]++} END {for (r in count) print count[r], r}' "$RUN/results.csv" | sort -nr
```

### 为什么复现没开始？

检查守护进程：

```bash
pgrep -af 'run_factors_directory_replication_forever.py'
```

检查复现日志：

```bash
tail -80 logs/factors_directory_replication.log
```

如果使用带时间戳的 bounded 日志：

```bash
ls -t logs/factors_directory_replication_bounded_*.log | head
tail -80 $(ls -t logs/factors_directory_replication_bounded_*.log | head -1)
```

检查 state：

```bash
cat output/factors_directory_replication/state.json
```

### 为什么大量 codegen failed？

最常见是 Codex 限流或模型额度：

```text
429 Too Many Requests
403 Forbidden
usage limit
exceeded retry limit
```

处理：

```bash
FACTOR_REPLICATION_CODEGEN_PARALLEL=1 FACTOR_REPLICATION_DISCUSSION_PARALLEL=false ./ai_start.sh replicate
```

或者主 miner：

```bash
FACTOR_MINER_CODEGEN_PARALLEL=1 FACTOR_MINER_DISCUSSION_PARALLEL=false ./baseline/run_factor_mining_v1.sh
```

### 为什么 `results.csv` 已经 40 行但没有好因子？

可能原因：

- 40 个都 codegen failed
- 回测指标不达标
- 相关性过高
- TVR 过高
- gsim 或 simsummary 失败

先汇总原因，不要直接打开大日志：

```bash
awk -F, 'NR>1 {reason=$10; sub(/ log=.*/, "", reason); count[reason]++} END {for (r in count) print count[r], r}' output/factor_mining/<run>/results.csv | sort -nr
```

### 为什么 rescue 没跑？

检查是否有候选：

```bash
ls output/factor_mining/<run>/rescue_candidates.csv
```

如果没有候选，通常说明没有可救的有效回测结果。

如果有候选，手动跑：

```bash
/usr/local/gsim/.venv/bin/python scripts/rescue_run_when_complete.py output/factor_mining/<run> --expected-results 40 --parallel 8
```

## 文件读取纪律

为了节省 token 和避免卡住：

1. 优先看 CSV、`launch.log`、进程状态。
2. 不直接读取大型 gsim 日志。
3. 先 `stat`，再 `tail`。
4. 只在定位具体失败时看单个 iter 的单个日志尾部。
5. 批量诊断优先用 `awk`、`wc`、`find`、`pgrep` 汇总。

推荐模板：

```bash
RUN=output/factor_mining/<run>
find "$RUN" -maxdepth 1 -type d -name 'iter_*' | wc -l
wc -l "$RUN/results.csv"
tail -40 "$RUN/launch.log"
awk -F, 'NR>1 {reason=$10; sub(/ log=.*/, "", reason); count[reason]++} END {for (r in count) print count[r], r}' "$RUN/results.csv" | sort -nr
```

## 维护建议

- 默认保持有界并行，不要恢复无限并行。
- 如果 Codex 限流，把 codegen 并行降到 1。
- 如果机器空闲但 Codex 稳定，再逐步提高 gsim 回测并行。
- factors.directory 复现失败后，先看 `replication_ledger.csv` 和 run 的失败原因，再决定是否重试该 slug。
- 对已有代码但没结果的 run，优先用 `backtest_pending_runs.py` 补跑，而不是重新 codegen。
- 对高相关但指标不错的因子，优先 rescue/decorrelation，而不是直接丢弃。
